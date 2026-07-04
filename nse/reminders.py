"""
nse/reminders.py — payment-reminder computation
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
Surfaces "money still owed" nudges across the app:

  * AMC annual fee unpaid after 2+ completed visits
  * Visit-linked material quotations approved but not yet paid
  * Service quotations accepted (client committed) but payment not confirmed

Used by the Ops Console (staff sees everyone's) and the customer profile
(filtered to their own). Pure read helper — no side effects.
"""
from flask import url_for


def payment_reminders(customer_id=None):
    """Return a list of reminder dicts. If `customer_id` is given, only that
    customer's reminders are returned (for the portal profile view)."""
    from .models import Contract, Quotation, ServiceQuotation

    reminders = []

    # 1) AMC annual fee unpaid after 2+ completed visits ---------------------
    q = Contract.query.filter(Contract.status == "active")
    if customer_id:
        q = q.filter(Contract.customer_id == customer_id)
    for c in q.all():
        if c.payment_status != "paid" and c.completed_visits >= 2:
            reminders.append({
                "kind": "amc_fee",
                "severity": "high",
                "title": "AMC fee unpaid",
                "detail": (f"{c.reference} — {c.completed_visits} visits done, "
                           f"annual fee not yet received."),
                "customer_id": c.customer_id,
                "customer_name": (c.customer.name if c.customer else c.applicant_name),
                "link": url_for("admin.contract", contract_id=c.id),
            })

    # 2) Visit-linked material quotes approved but unpaid --------------------
    mq = Quotation.query.filter(Quotation.status == "approved",
                                Quotation.payment_status != "paid")
    for m in mq.all():
        cust_id = m.contract.customer_id if m.contract else None
        if customer_id and cust_id != customer_id:
            continue
        reminders.append({
            "kind": "material_quote",
            "severity": "medium",
            "title": "Material quotation unpaid",
            "detail": (f"{m.reference} (₹{m.grand_total:,.0f}) approved"
                       f"{' on ' + m.visit.label if m.visit else ''} — payment pending."),
            "customer_id": cust_id,
            "customer_name": m.customer_name,
            "link": (url_for("admin.visit", visit_id=m.visit_id) if m.visit_id
                     else url_for("admin.contract", contract_id=m.contract_id)),
        })

    # 3) Service quotations accepted but payment not confirmed ----------------
    sq = ServiceQuotation.query.filter(ServiceQuotation.status == "accepted",
                                       ServiceQuotation.payment_status != "paid")
    if customer_id:
        sq = sq.filter(ServiceQuotation.customer_id == customer_id)
    for s in sq.all():
        # An accepted AMC quote whose contract fee reminder already fires above
        # would double-count; skip if it's linked to an active contract that the
        # amc_fee rule already covers.
        if s.contract and s.contract.status == "active" and s.contract.completed_visits >= 2 \
                and s.contract.payment_status != "paid":
            continue
        chose = f" — client chose {s.payment_method_label}" if s.payment_method else ""
        reminders.append({
            "kind": "service_quote",
            "severity": "medium",
            "title": "Quotation accepted, payment pending",
            "detail": f"{s.reference} (₹{s.grand_total:,.0f}) accepted{chose}.",
            "customer_id": s.customer_id,
            "customer_name": s.customer_name,
            "link": url_for("sq.detail_quotation", sq_id=s.id),
        })

    return reminders


def payment_reminder_count(customer_id=None):
    return len(payment_reminders(customer_id=customer_id))


# --------------------------------------------------------------------------- #
# Visit reminders (1 month / 1 week / 1 day before)
# --------------------------------------------------------------------------- #
REMINDER_THRESHOLDS = [
    ("1month", 30,  "1 month"),
    ("1week",   7,  "1 week"),
    ("1day",    1,  "tomorrow"),
]


def process_visit_reminders():
    """Idempotent — safe to call on every request.
    Checks for upcoming visits at 30/7/1-day horizons and fires in-app
    notifications if they have not already been sent.
    New tables (visit_reminder_logs) are created by db.create_all on startup."""
    from datetime import date, timedelta
    from flask import url_for
    from .models import Visit, VisitReminderLog
    from .extensions import db
    from .utils import notify

    today = date.today()

    for reminder_type, days, label in REMINDER_THRESHOLDS:
        target_date = today + timedelta(days=days)
        visits = Visit.query.filter(
            Visit.scheduled_date == target_date,
            Visit.status.in_(["scheduled", "in_progress"]),
        ).all()

        for v in visits:
            c = v.contract
            if not c:
                continue

            # --- Customer reminder ---
            if c.customer_id:
                already = VisitReminderLog.query.filter_by(
                    visit_id=v.id, reminder_type=reminder_type,
                    sent_to="customer").first()
                if not already:
                    try:
                        link = url_for("portal.contract", contract_id=c.id)
                    except Exception:
                        link = None
                    notify(c.customer_id,
                           f"Upcoming visit in {label}",
                           f"{v.label} for {c.reference} is scheduled "
                           f"on {v.scheduled_date.strftime('%d %b %Y')}. "
                           f"Please keep the site accessible.",
                           link=link)
                    db.session.add(VisitReminderLog(
                        visit_id=v.id, reminder_type=reminder_type,
                        sent_to="customer"))

            # --- Technician reminder ---
            if v.technician_id:
                already = VisitReminderLog.query.filter_by(
                    visit_id=v.id, reminder_type=reminder_type,
                    sent_to="technician").first()
                if not already:
                    try:
                        link = url_for("admin.visit", visit_id=v.id)
                    except Exception:
                        link = None
                    notify(v.technician_id,
                           f"Visit reminder — {label} to go",
                           f"You have {v.label} for {c.reference} "
                           f"({c.site_name or c.applicant_name}) "
                           f"on {v.scheduled_date.strftime('%d %b %Y')}.",
                           link=link)
                    db.session.add(VisitReminderLog(
                        visit_id=v.id, reminder_type=reminder_type,
                        sent_to="technician"))

    try:
        db.session.commit()
    except Exception:
        db.session.rollback()


# --------------------------------------------------------------------------- #
# Wave 11 — Renewal pipeline (contracts expiring in 90/60/30 days) & milestones
# --------------------------------------------------------------------------- #
def renewal_reminders(customer_id=None):
    """Active, not-yet-renewed contracts approaching expiry (or expired).
    Returns dicts sorted by urgency for the Ops renewals page & portal banner."""
    from .models import Contract

    q = Contract.query.filter(Contract.status == "active")
    if customer_id:
        q = q.filter(Contract.customer_id == customer_id)

    sev_map = {"expired": "high", "7": "high", "30": "high", "60": "medium", "90": "low"}
    out = []
    for c in q.all():
        w = c.renewal_window
        if not w:
            continue
        d = c.days_to_expiry
        detail = (f"{c.reference} expired {abs(d)} days ago" if w == "expired"
                  else f"{c.reference} expires in {d} days "
                       f"({c.end_date:%d %b %Y})")
        out.append({
            "window": w,
            "severity": sev_map.get(w, "low"),
            "days": d,
            "contract_id": c.id,
            "reference": c.reference,
            "customer_id": c.customer_id,
            "customer_name": (c.customer.name if c.customer else c.applicant_name),
            "site": c.site_name or c.area or "",
            "detail": detail,
        })
    order = {"expired": 0, "7": 1, "30": 2, "60": 3, "90": 4}
    out.sort(key=lambda r: (order.get(r["window"], 9), r["days"] if r["days"] is not None else 999))
    return out


def renewal_reminder_count(customer_id=None):
    return len(renewal_reminders(customer_id=customer_id))


def process_milestones():
    """Idempotent — fire notifications the first time a contract enters a renewal
    band (90/60/30) or reaches a service anniversary. De-duped via MilestoneLog.
    Safe to call on every request (guarded, best-effort)."""
    from flask import url_for
    from .models import Contract, MilestoneLog
    from .extensions import db
    from .utils import notify, notify_staff

    active = Contract.query.filter(Contract.status == "active").all()
    for c in active:
        # ---- Renewal bands ----
        w = c.renewal_window
        if w in ("90", "60", "30", "7"):
            mtype = f"renewal_{w}"
            exists = MilestoneLog.query.filter_by(
                contract_id=c.id, milestone_type=mtype).first()
            if not exists:
                try:
                    clink = url_for("portal.contract", contract_id=c.id)
                    slink = url_for("admin.renewals")
                except Exception:
                    clink = slink = None
                if c.customer_id:
                    notify(c.customer_id, f"AMC renewal due in {w} days",
                           f"Your contract {c.reference} expires on "
                           f"{c.end_date:%d %b %Y}. Renew now to stay protected.",
                           link=clink)
                notify_staff(f"Renewal due — {c.reference} ({w} days)",
                             f"{(c.customer.name if c.customer else c.applicant_name)} "
                             f"— {c.site_name or ''}", link=slink)
                db.session.add(MilestoneLog(contract_id=c.id, milestone_type=mtype))

        # ---- Service anniversaries ----
        yrs = c.anniversary_years
        if yrs >= 1:
            mtype = f"anniversary_{yrs}"
            exists = MilestoneLog.query.filter_by(
                contract_id=c.id, milestone_type=mtype).first()
            # only fire within a week of the anniversary date to stay timely
            if not exists and c.start_date:
                from datetime import date
                anniv = c.start_date.replace(year=date.today().year)
                if 0 <= (date.today() - anniv).days <= 7 and c.customer_id:
                    try:
                        clink = url_for("portal.contract", contract_id=c.id)
                    except Exception:
                        clink = None
                    notify(c.customer_id,
                           f"Thank you for {yrs} year"
                           f"{'s' if yrs > 1 else ''} with us!",
                           f"We're grateful you've trusted Northern Star with "
                           f"{c.reference}. Here's to continued safety together.",
                           link=clink)
                    db.session.add(MilestoneLog(contract_id=c.id, milestone_type=mtype))

    try:
        db.session.commit()
    except Exception:
        db.session.rollback()
