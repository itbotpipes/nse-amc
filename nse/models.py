from datetime import datetime, date, timedelta

from flask_login import UserMixin
from werkzeug.security import generate_password_hash, check_password_hash

from .extensions import db, login_manager


# --------------------------------------------------------------------------- #
# Users & auth
# --------------------------------------------------------------------------- #
class User(UserMixin, db.Model):
    __tablename__ = "users"

    id = db.Column(db.Integer, primary_key=True)
    role = db.Column(db.String(20), nullable=False, default="customer")  # customer/technician/admin
    name = db.Column(db.String(120), nullable=False)

    # Customers authenticate by phone (OTP); staff by email + password.
    phone = db.Column(db.String(20), unique=True)
    email = db.Column(db.String(120), unique=True)
    password_hash = db.Column(db.String(255))

    address = db.Column(db.String(255))
    area = db.Column(db.String(120))
    city = db.Column(db.String(120), default="Surat, Gujarat")
    # Extended profile fields (added post-Wave-6)
    company_name = db.Column(db.String(200))
    gst_number   = db.Column(db.String(50))
    photo_path   = db.Column(db.String(255))   # profile photo / ID card upload
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    contracts = db.relationship("Contract", backref="customer", lazy=True,
                                foreign_keys="Contract.customer_id")

    def set_password(self, raw):
        # pbkdf2 works on the system LibreSSL build (scrypt is unavailable there).
        self.password_hash = generate_password_hash(raw, method="pbkdf2:sha256")

    def check_password(self, raw):
        return bool(self.password_hash) and check_password_hash(self.password_hash, raw)

    @property
    def is_staff(self):
        return self.role in ("technician", "admin")

    @property
    def is_admin(self):
        return self.role == "admin"

    # ----- Wave 11: Customer Health Score (0-100, retention risk) -----------
    @property
    def health_score(self):
        """Loyalty / retention score across all of the customer's contracts:
        payments, visit reliability, feedback given, agreement, referrals."""
        contracts = [c for c in self.contracts if c.status in ("active", "expired")]
        if not contracts:
            return None
        score = 0
        # Payments on time — 30 pts
        paid = sum(1 for c in contracts if c.payment_status == "paid")
        score += 30 * (paid / len(contracts))
        # Visit reliability (few cancellations) — 20 pts
        all_visits = [v for c in contracts for v in c.visits]
        if all_visits:
            cancelled = sum(1 for v in all_visits if v.status == "cancelled")
            score += 20 * (1 - min(1.0, cancelled / len(all_visits)))
        else:
            score += 20
        # Feedback engagement — 20 pts
        completed = [v for v in all_visits if v.status == "completed"]
        if completed:
            rated = sum(1 for v in completed if v.feedback and v.feedback.is_submitted)
            score += 20 * (rated / len(completed))
        else:
            score += 10
        # Agreement accepted — 15 pts
        if any(c.agreement_accepted for c in contracts):
            score += 15
        # Referrals made — 15 pts (any referral = full marks)
        refs = [r for c in contracts for r in getattr(c, "referrals", [])]
        score += 15 if refs else 0
        return max(0, min(100, round(score)))

    @property
    def health_band(self):
        s = self.health_score
        if s is None:
            return ("New", "slate")
        if s >= 75:
            return ("Loyal", "green")
        if s >= 50:
            return ("Stable", "blue")
        if s >= 30:
            return ("At risk", "amber")
        return ("Churn risk", "red")

    def __repr__(self):
        return f"<User {self.id} {self.role} {self.name}>"


@login_manager.user_loader
def load_user(user_id):
    return db.session.get(User, int(user_id))


class OtpCode(db.Model):
    """One-time codes for customer phone login (dev flow / pluggable SMS)."""
    __tablename__ = "otp_codes"

    id = db.Column(db.Integer, primary_key=True)
    phone = db.Column(db.String(20), nullable=False, index=True)
    code = db.Column(db.String(6), nullable=False)
    expires_at = db.Column(db.DateTime, nullable=False)
    used = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    @property
    def is_valid(self):
        return (not self.used) and datetime.utcnow() < self.expires_at


# --------------------------------------------------------------------------- #
# AMC plans & contracts
# --------------------------------------------------------------------------- #
class AMCPlan(db.Model):
    __tablename__ = "amc_plans"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), nullable=False)
    category = db.Column(db.String(30), nullable=False)  # residential/commercial
    tier = db.Column(db.String(30))                      # Basic/Standard/Premium
    price = db.Column(db.Integer, nullable=False)        # rupees per year
    visits_per_year = db.Column(db.Integer, nullable=False, default=4)
    response_time = db.Column(db.String(60), default="48 hours")
    sla_hours = db.Column(db.Integer, default=48)   # Wave 11 — emergency response SLA (hrs)
    description = db.Column(db.Text)
    features = db.Column(db.Text)  # one feature per line
    active = db.Column(db.Boolean, default=True)

    @property
    def feature_list(self):
        return [f.strip() for f in (self.features or "").splitlines() if f.strip()]


class Contract(db.Model):
    __tablename__ = "contracts"

    id = db.Column(db.Integer, primary_key=True)
    customer_id = db.Column(db.Integer, db.ForeignKey("users.id"))
    plan_id = db.Column(db.Integer, db.ForeignKey("amc_plans.id"))

    status = db.Column(db.String(20), default="pending")  # pending/active/expired/cancelled
    site_name = db.Column(db.String(160))
    site_address = db.Column(db.String(255))
    area = db.Column(db.String(120))

    # Captured on application before a user account may exist.
    applicant_name = db.Column(db.String(120))
    applicant_phone = db.Column(db.String(20))
    applicant_email = db.Column(db.String(120))
    property_type = db.Column(db.String(60))
    application_notes = db.Column(db.Text)
    voice_note = db.Column(db.Text)          # speech-to-text transcript from the apply form

    start_date = db.Column(db.Date)
    end_date = db.Column(db.Date)
    total_visits = db.Column(db.Integer, default=4)
    price = db.Column(db.Integer, default=0)
    payment_mode = db.Column(db.String(20), default="cash")    # cash/online
    payment_status = db.Column(db.String(20), default="pending")
    payment_date = db.Column(db.Date)                          # when contract fee received
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    # AMC agreement (T&C click-through accepted by the customer in-app)
    agreement_accepted = db.Column(db.Boolean, default=False)
    agreement_accepted_at = db.Column(db.DateTime)
    agreement_version = db.Column(db.String(20))

    # AMC certificate (issued after 4th completed visit)
    certificate_issued = db.Column(db.Boolean, default=False)
    certificate_issued_at = db.Column(db.DateTime)

    # Wave 11 — renewal / clone lineage (points at the prior year's contract)
    renewed_from_id = db.Column(db.Integer, db.ForeignKey("contracts.id"), nullable=True)

    plan = db.relationship("AMCPlan")
    renewed_from = db.relationship("Contract", remote_side=[id],
                                   backref="renewals", foreign_keys=[renewed_from_id])
    visits = db.relationship("Visit", backref="contract", lazy=True,
                             cascade="all, delete-orphan", order_by="Visit.visit_number")
    equipment = db.relationship("Equipment", backref="contract", lazy=True,
                                cascade="all, delete-orphan")
    quotations = db.relationship("Quotation", backref="contract", lazy=True,
                                 cascade="all, delete-orphan")
    service_quotations = db.relationship(
        "ServiceQuotation", backref="contract", lazy=True,
        foreign_keys="ServiceQuotation.contract_id")

    @property
    def reference(self):
        return f"NSE-AMC-{self.id:04d}"

    @property
    def completed_visits(self):
        return sum(1 for v in self.visits if v.status == "completed")

    @property
    def next_visit(self):
        upcoming = [v for v in self.visits if v.status in ("scheduled", "in_progress")]
        return min(upcoming, key=lambda v: v.scheduled_date or date.max) if upcoming else None

    # ----- Quotation ↔ contract interlink (gates activation) ----------------
    @property
    def amc_quote(self):
        """The most recent AMC sales quotation linked to this contract (or None).

        The apply flow auto-creates a ServiceQuotation per AMC application, so a
        pending contract normally has exactly one. Activation is gated on the
        customer accepting it.
        """
        qs = [q for q in self.service_quotations if q.service_type == "amc"]
        if not qs:
            return None
        return max(qs, key=lambda q: q.created_at or datetime.min)

    @property
    def can_activate(self):
        """True when there is no linked AMC quote, or the client has accepted it.

        Blocks `Activate & generate visits` while a quote is still sent / viewed
        / under negotiation / rejected — staff must wait for the client's accept.
        """
        q = self.amc_quote
        return (q is None) or (q.status == "accepted")

    @property
    def quote_locked_price(self):
        """Annual price locked from an accepted quote (pre-GST subtotal), else None.

        When set, the contract activation price field is read-only — the figure
        was already agreed in the quotation, so it cannot be changed again here.
        """
        q = self.amc_quote
        if q and q.status == "accepted":
            # GST-inclusive grand total — the full amount the client agreed to pay.
            return int(round(q.grand_total))
        return None

    # ----- Wave 6: workflow roadmap (customer portal progress track) ---------
    @property
    def workflow_steps(self):
        """Ordered roadmap steps with done/active flags, shown as a filling track:
        Quote accepted → Contract started → Visit 1 … → Visit N.
        The first not-yet-done step is marked `active`.
        """
        steps = []
        q = self.amc_quote
        # Step 1 — quotation confirmed (or no quote needed)
        steps.append({
            "label": "Quote confirmed",
            "done": (q is None) or (q.status == "accepted"),
        })
        # Step 2 — contract started
        steps.append({
            "label": "Contract started",
            "done": self.status in ("active", "expired"),
        })
        # Steps 3..N — each visit
        ordered = sorted(self.visits, key=lambda v: v.visit_number)
        for v in ordered:
            steps.append({
                "label": v.label,
                "done": v.status == "completed",
            })
        # Mark the first incomplete step as active
        for s in steps:
            if not s["done"]:
                s["active"] = True
                break
        return steps

    @property
    def visit_material_quotes(self):
        """All material quotations raised against this contract's visits."""
        return [q for q in self.quotations if q.visit_id]

    # ----- Wave 11: renewal lifecycle ---------------------------------------
    @property
    def days_to_expiry(self):
        """Days until the contract ends (negative = already expired). None if no end date."""
        if not self.end_date:
            return None
        return (self.end_date - date.today()).days

    @property
    def is_renewed(self):
        """True when a follow-on contract already renews this one."""
        return bool(getattr(self, "renewals", None))

    @property
    def renewal_window(self):
        """Which renewal-reminder band the contract falls in (or None).
        Only active, not-yet-renewed contracts qualify."""
        if self.status != "active" or self.is_renewed:
            return None
        d = self.days_to_expiry
        if d is None:
            return None
        if d < 0:
            return "expired"
        if d <= 7:
            return "7"
        if d <= 30:
            return "30"
        if d <= 60:
            return "60"
        if d <= 90:
            return "90"
        return None

    @property
    def anniversary_years(self):
        """Whole years elapsed since the contract start date (0 if <1yr / no date)."""
        if not self.start_date:
            return 0
        today = date.today()
        yrs = today.year - self.start_date.year
        if (today.month, today.day) < (self.start_date.month, self.start_date.day):
            yrs -= 1
        return max(0, yrs)

    # ----- Wave 11: Property Fire Safety Score (0-100) ----------------------
    @property
    def safety_score(self):
        """A 0-100 compliance score for this property, weighting visit
        compliance, equipment health, open defects, certificate & agreement."""
        score = 0
        # Visit compliance — 35 pts
        total = self.total_visits or len(self.visits) or 0
        if total:
            score += 35 * min(1.0, self.completed_visits / total)
        else:
            score += 35
        # Equipment health — 25 pts (penalise overdue/due-soon)
        eq = list(self.equipment)
        if eq:
            ok = sum(1 for e in eq if e.refill_status in ("ok", "unknown"))
            score += 25 * (ok / len(eq))
        else:
            score += 25
        # Open defects — 20 pts (any open high-severity defect hurts most)
        defects = [d for v in self.visits for d in getattr(v, "defects", [])
                   if d.status not in ("resolved",)]
        if not defects:
            score += 20
        else:
            highs = sum(1 for d in defects if d.severity == "high")
            score += max(0, 20 - highs * 8 - (len(defects) - highs) * 3)
        # Certificate issued — 10 pts
        if self.certificate_issued:
            score += 10
        elif self.completed_visits >= 2:
            score += 5
        # Agreement accepted — 10 pts
        if self.agreement_accepted:
            score += 10
        return max(0, min(100, round(score)))

    @property
    def safety_grade(self):
        s = self.safety_score
        if s >= 85:
            return ("A", "green")
        if s >= 70:
            return ("B", "blue")
        if s >= 50:
            return ("C", "amber")
        return ("D", "red")

    @property
    def open_defects(self):
        return [d for v in self.visits for d in getattr(v, "defects", [])
                if d.status not in ("resolved",)]

    @property
    def installment_plan(self):
        """Ordered instalments for this contract (empty when paid in full)."""
        return sorted(getattr(self, "installments", []), key=lambda i: i.sequence)


class Visit(db.Model):
    __tablename__ = "visits"

    id = db.Column(db.Integer, primary_key=True)
    contract_id = db.Column(db.Integer, db.ForeignKey("contracts.id"))
    visit_number = db.Column(db.Integer, nullable=False, default=1)
    scheduled_date = db.Column(db.Date)
    completed_date = db.Column(db.Date)
    status = db.Column(db.String(20), default="scheduled")  # scheduled/in_progress/completed/cancelled
    technician_id = db.Column(db.Integer, db.ForeignKey("users.id"))
    work_done = db.Column(db.Text)
    notes = db.Column(db.Text)
    service_report_path = db.Column(db.String(255))
    # Wave 6 — customer sign-off on a completed visit (gates the rating prompt)
    customer_approved = db.Column(db.Boolean, default=False)
    approved_at = db.Column(db.DateTime)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    # Technician day-plan confirmation (None=pending, True=accepted, False=rejected)
    technician_confirmed = db.Column(db.Boolean, nullable=True)
    technician_note = db.Column(db.Text)
    # Wave 11 — on-site check-in / check-out (proof of visit, SLA timing)
    checkin_at   = db.Column(db.DateTime, nullable=True)
    checkout_at  = db.Column(db.DateTime, nullable=True)
    checkin_note = db.Column(db.String(255))   # optional geo string / free note

    technician = db.relationship("User", foreign_keys=[technician_id])
    photos = db.relationship("VisitPhoto", backref="visit", lazy=True,
                             cascade="all, delete-orphan")
    checklist_items = db.relationship("VisitChecklistItem", backref="visit", lazy=True,
                                      cascade="all, delete-orphan",
                                      order_by="VisitChecklistItem.sort_order")

    @property
    def label(self):
        return f"Visit {self.visit_number}"

    @property
    def days_until(self):
        """Days remaining until scheduled date (negative = overdue). None if no date."""
        if self.scheduled_date and self.status in ("scheduled", "in_progress"):
            return (self.scheduled_date - date.today()).days
        return None

    @property
    def material_quote(self):
        """The most recent material quotation raised against this visit (or None)."""
        qs = sorted(self.material_quotes, key=lambda q: q.created_at or datetime.min)
        return qs[-1] if qs else None

    @property
    def feedback(self):
        """The customer's feedback/rating for this visit, if submitted (or None)."""
        return VisitFeedback.query.filter_by(visit_id=self.id).first()

    @property
    def checklist_summary(self):
        items = list(self.checklist_items)
        if not items:
            return None
        ok = sum(1 for i in items if i.status == "ok")
        issues = sum(1 for i in items if i.status == "issue")
        return {"total": len(items), "ok": ok, "issues": issues}

    @property
    def is_checked_in(self):
        return self.checkin_at is not None and self.checkout_at is None

    @property
    def onsite_minutes(self):
        """Minutes between check-in and check-out (None if not both recorded)."""
        if self.checkin_at and self.checkout_at:
            return int((self.checkout_at - self.checkin_at).total_seconds() // 60)
        return None

    @property
    def onsite_duration_label(self):
        m = self.onsite_minutes
        if m is None:
            return None
        h, mm = divmod(m, 60)
        return (f"{h}h {mm}m" if h else f"{mm}m")

    @property
    def open_defects(self):
        return [d for d in getattr(self, "defects", []) if d.status != "resolved"]


class VisitChecklistItem(db.Model):
    """Wave 2 — maintenance checklist items ticked off during a visit."""
    __tablename__ = "visit_checklist_items"

    id         = db.Column(db.Integer, primary_key=True)
    visit_id   = db.Column(db.Integer, db.ForeignKey("visits.id"), nullable=False)
    item       = db.Column(db.String(120), nullable=False)   # e.g. "Smoke Detectors"
    status     = db.Column(db.String(20),  default="ok")     # ok / issue / na
    note       = db.Column(db.String(255))
    sort_order = db.Column(db.Integer, default=0)

    STANDARD_ITEMS = [
        "Smoke Detectors", "Heat Detectors", "Manual Call Points",
        "Fire Alarm Control Panel", "Fire Extinguishers (ABC)",
        "Fire Extinguishers (CO₂)", "Hydrant System", "Hose Reels",
        "Sprinkler Heads", "Emergency Lighting", "Exit Signage",
        "PA / Evacuation System",
    ]


class VisitPhoto(db.Model):
    __tablename__ = "visit_photos"

    id = db.Column(db.Integer, primary_key=True)
    visit_id = db.Column(db.Integer, db.ForeignKey("visits.id"))
    file_path = db.Column(db.String(255), nullable=False)  # relative to static/
    caption = db.Column(db.String(255))
    uploaded_at = db.Column(db.DateTime, default=datetime.utcnow)


# --------------------------------------------------------------------------- #
# Equipment & refills
# --------------------------------------------------------------------------- #
class Equipment(db.Model):
    __tablename__ = "equipment"

    id = db.Column(db.Integer, primary_key=True)
    contract_id = db.Column(db.Integer, db.ForeignKey("contracts.id"))
    name = db.Column(db.String(160), nullable=False)        # e.g. ABC Dry Powder 6kg
    equip_type = db.Column(db.String(80))                   # extinguisher/hydrant/alarm/...
    location = db.Column(db.String(160))
    serial_no = db.Column(db.String(80))
    install_date = db.Column(db.Date)
    last_refill_date = db.Column(db.Date)
    refill_interval_months = db.Column(db.Integer, default=12)
    next_refill_date = db.Column(db.Date)

    refills = db.relationship("RefillRecord", backref="equipment", lazy=True,
                              cascade="all, delete-orphan", order_by="RefillRecord.refill_date.desc()")

    def recompute_next_refill(self):
        base = self.last_refill_date or self.install_date
        if base and self.refill_interval_months:
            months = self.refill_interval_months
            year = base.year + (base.month - 1 + months) // 12
            month = (base.month - 1 + months) % 12 + 1
            day = min(base.day, 28)
            self.next_refill_date = date(year, month, day)

    @property
    def days_to_refill(self):
        if not self.next_refill_date:
            return None
        return (self.next_refill_date - date.today()).days

    @property
    def refill_status(self):
        d = self.days_to_refill
        if d is None:
            return "unknown"
        if d < 0:
            return "overdue"
        if d <= 30:
            return "due_soon"
        return "ok"


class RefillRecord(db.Model):
    __tablename__ = "refill_records"

    id = db.Column(db.Integer, primary_key=True)
    equipment_id = db.Column(db.Integer, db.ForeignKey("equipment.id"))
    refill_date = db.Column(db.Date, nullable=False, default=date.today)
    performed_by = db.Column(db.String(120))
    notes = db.Column(db.String(255))


# --------------------------------------------------------------------------- #
# Quotations (materials to replace)
# --------------------------------------------------------------------------- #
class Quotation(db.Model):
    __tablename__ = "quotations"

    id = db.Column(db.Integer, primary_key=True)
    contract_id = db.Column(db.Integer, db.ForeignKey("contracts.id"))
    visit_id = db.Column(db.Integer, db.ForeignKey("visits.id"))
    status = db.Column(db.String(20), default="pending")  # pending/approved/rejected
    notes = db.Column(db.Text)
    payment_mode = db.Column(db.String(20))               # chosen on approval
    # Wave 6 — payment tracking for a visit-linked material quote
    payment_status = db.Column(db.String(20), default="pending")  # pending/paid
    payment_date = db.Column(db.Date)
    # Wave 6 — liability waiver acknowledged when the client rejects a quote
    rejection_acknowledged = db.Column(db.Boolean, default=False)
    waiver_text = db.Column(db.Text)
    # Client can request a re-quote / negotiation after rejecting
    negotiation_note = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    decided_at = db.Column(db.DateTime)

    items = db.relationship("QuotationItem", backref="quotation", lazy=True,
                            cascade="all, delete-orphan")
    visit = db.relationship("Visit", backref="material_quotes",
                            foreign_keys=[visit_id])

    @property
    def reference(self):
        return f"QT-{self.id:05d}"

    @property
    def total(self):
        return sum(i.amount for i in self.items)

    @property
    def is_paid(self):
        return self.payment_status == "paid"

    # ── PDF-compatible properties (mirrors ServiceQuotation interface) ────────
    @property
    def customer_name(self):
        c = self.contract
        return (c.customer.name if c and c.customer else c.applicant_name if c else "")

    @property
    def customer_phone(self):
        c = self.contract
        return (c.customer.phone if c and c.customer else c.applicant_phone if c else "")

    @property
    def customer_email(self):
        c = self.contract
        return (c.customer.email if c and c.customer else c.applicant_email if c else "")

    @property
    def customer_address(self):
        c = self.contract
        return c.site_address if c else ""

    @property
    def project_name(self):
        c = self.contract
        return c.site_name if c else ""

    @property
    def valid_days(self):
        return 30

    @property
    def gst_percent(self):
        return 18

    @property
    def subtotal(self):
        return self.total

    @property
    def gst_amount(self):
        return round(self.total * 0.18)

    @property
    def grand_total(self):
        return self.total + self.gst_amount

    @property
    def items_by_category(self):
        """Group items under a single 'Materials' category for the PDF template."""
        return {"Materials / Spare Parts": self.items}


class QuotationItem(db.Model):
    __tablename__ = "quotation_items"

    id = db.Column(db.Integer, primary_key=True)
    quotation_id = db.Column(db.Integer, db.ForeignKey("quotations.id"))
    description = db.Column(db.String(255), nullable=False)
    quantity = db.Column(db.Integer, default=1)
    unit_price = db.Column(db.Integer, default=0)

    @property
    def amount(self):
        return (self.quantity or 0) * (self.unit_price or 0)

    # Aliases so the shared PDF template (pdf/quotation.html) can render
    # both ServiceQuotationItem and QuotationItem without branching.
    @property
    def rate(self):
        return float(self.unit_price or 0)

    @property
    def unit(self):
        return "Nos"

    @property
    def total(self):
        return float(self.amount)

    @property
    def hsn_code(self):
        return ""


# --------------------------------------------------------------------------- #
# Emergency / NOC service requests
# --------------------------------------------------------------------------- #
class ServiceRequest(db.Model):
    __tablename__ = "service_requests"

    id = db.Column(db.Integer, primary_key=True)
    request_type = db.Column(db.String(20), default="emergency")  # emergency/noc
    customer_id = db.Column(db.Integer, db.ForeignKey("users.id"))

    name = db.Column(db.String(120), nullable=False)
    phone = db.Column(db.String(20), nullable=False)
    email = db.Column(db.String(120))
    area = db.Column(db.String(120))
    location = db.Column(db.String(255))
    description = db.Column(db.Text)        # what happened / NOC requirement
    voice_note = db.Column(db.Text)         # speech-to-text transcript from the form
    noc_document_path = db.Column(db.String(255))  # uploaded old NOC for renewal

    status = db.Column(db.String(20), default="new")  # new/scheduled/dispatched/in_progress/completed/cancelled
    scheduled_date = db.Column(db.DateTime)
    team_eta = db.Column(db.String(80))
    assigned_technician_id = db.Column(db.Integer, db.ForeignKey("users.id"))

    payment_mode = db.Column(db.String(20), default="cash")  # cash/online
    amount = db.Column(db.Integer, default=0)
    payment_status = db.Column(db.String(20), default="pending")
    # Wave 11 — SLA response tracking
    sla_due_at        = db.Column(db.DateTime, nullable=True)   # deadline to respond
    first_response_at = db.Column(db.DateTime, nullable=True)   # when staff acted
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    technician = db.relationship("User", foreign_keys=[assigned_technician_id])

    @property
    def reference(self):
        prefix = "NOC" if self.request_type == "noc" else "EMG"
        return f"{prefix}-{self.id:05d}"

    # ----- Wave 11: SLA status --------------------------------------------
    @property
    def sla_status(self):
        """met / breached / at_risk / pending / none — based on sla_due_at vs
        first_response_at (or now). 'none' when no SLA deadline is set."""
        if not self.sla_due_at:
            return "none"
        if self.first_response_at:
            return "met" if self.first_response_at <= self.sla_due_at else "breached"
        now = datetime.utcnow()
        if now > self.sla_due_at:
            return "breached"
        # within 25% of the window remaining → at risk
        remaining = (self.sla_due_at - now).total_seconds()
        if remaining <= 3600:  # under an hour left
            return "at_risk"
        return "pending"

    @property
    def sla_label(self):
        return {
            "met": "SLA met", "breached": "SLA breached", "at_risk": "SLA at risk",
            "pending": "Within SLA", "none": "",
        }.get(self.sla_status, "")


# --------------------------------------------------------------------------- #
# Extinguisher refilling (on-demand, no AMC needed)
# --------------------------------------------------------------------------- #
class RefillOrder(db.Model):
    __tablename__ = "refill_orders"

    id = db.Column(db.Integer, primary_key=True)
    customer_id = db.Column(db.Integer, db.ForeignKey("users.id"))

    name = db.Column(db.String(120), nullable=False)
    phone = db.Column(db.String(20), nullable=False)
    email = db.Column(db.String(120))
    area = db.Column(db.String(120))
    address = db.Column(db.String(255))

    # How the refill is handled: we pick up the units, or service on-site.
    service_mode = db.Column(db.String(20), default="onsite")  # onsite/pickup
    status = db.Column(db.String(20), default="new")
    # new/scheduled/picked_up/in_progress/completed/cancelled
    scheduled_date = db.Column(db.DateTime)
    team_eta = db.Column(db.String(80))
    notes = db.Column(db.Text)

    payment_mode = db.Column(db.String(20), default="cash")  # cash/online
    amount = db.Column(db.Integer, default=0)                # final, set by ops
    payment_status = db.Column(db.String(20), default="pending")
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    items = db.relationship("RefillItem", backref="order", lazy=True,
                            cascade="all, delete-orphan")

    @property
    def reference(self):
        return f"RF-{self.id:05d}"

    @property
    def total_units(self):
        return sum(i.quantity or 0 for i in self.items)

    @property
    def summary(self):
        return ", ".join(
            f"{i.quantity}× {i.ext_type}{(' ' + i.capacity) if i.capacity else ''}"
            for i in self.items)


class RefillItem(db.Model):
    __tablename__ = "refill_items"

    id = db.Column(db.Integer, primary_key=True)
    order_id = db.Column(db.Integer, db.ForeignKey("refill_orders.id"))
    ext_type = db.Column(db.String(40), nullable=False)  # ABC/CO2/K-class/Modular
    capacity = db.Column(db.String(40))                  # e.g. "6 kg", "4.5 kg"
    quantity = db.Column(db.Integer, default=1)
    notes = db.Column(db.String(255))


# --------------------------------------------------------------------------- #
# Form attachments (photos uploaded on AMC apply / NOC / refill forms)
# --------------------------------------------------------------------------- #
class FormAttachment(db.Model):
    """Photos or documents attached at form-submission time (public forms).

    ref_type: 'contract' | 'service_request' | 'refill_order'
    ref_id:   the id of the referenced row.
    """
    __tablename__ = "form_attachments"

    id = db.Column(db.Integer, primary_key=True)
    ref_type = db.Column(db.String(30), nullable=False, index=True)
    ref_id = db.Column(db.Integer, nullable=False, index=True)
    file_path = db.Column(db.String(255), nullable=False)  # relative or absolute URL
    attachment_type = db.Column(db.String(20), default="photo")  # photo / document
    caption = db.Column(db.String(255))
    uploaded_at = db.Column(db.DateTime, default=datetime.utcnow)


# --------------------------------------------------------------------------- #
# Payments, enquiries, notifications
# --------------------------------------------------------------------------- #
class Payment(db.Model):
    __tablename__ = "payments"

    id = db.Column(db.Integer, primary_key=True)
    customer_id = db.Column(db.Integer, db.ForeignKey("users.id"))
    contract_id = db.Column(db.Integer, db.ForeignKey("contracts.id"))
    service_request_id = db.Column(db.Integer, db.ForeignKey("service_requests.id"))
    quotation_id = db.Column(db.Integer, db.ForeignKey("quotations.id"))
    amount = db.Column(db.Integer, default=0)
    mode = db.Column(db.String(20), default="cash")     # cash/online
    status = db.Column(db.String(20), default="pending")  # pending/paid
    reference = db.Column(db.String(80))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)


class Enquiry(db.Model):
    __tablename__ = "enquiries"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), nullable=False)
    phone = db.Column(db.String(20))
    email = db.Column(db.String(120))
    subject = db.Column(db.String(160))
    message = db.Column(db.Text, nullable=False)
    status = db.Column(db.String(20), default="new")  # new/responded
    created_at = db.Column(db.DateTime, default=datetime.utcnow)


class Notification(db.Model):
    __tablename__ = "notifications"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"))
    title = db.Column(db.String(160), nullable=False)
    body = db.Column(db.String(255))
    link = db.Column(db.String(255))   # where clicking the notification navigates
    read = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)


# --------------------------------------------------------------------------- #
# Service Quotations (sales proposals — AMC / NOC / Refilling / Emergency)
# Separate from Quotation which handles material replacements during AMC visits.
# --------------------------------------------------------------------------- #
class ServiceQuotation(db.Model):
    __tablename__ = "service_quotations"

    id = db.Column(db.Integer, primary_key=True)
    quotation_number = db.Column(db.String(30), unique=True)  # QUO-26-27-00001
    service_type = db.Column(db.String(20), default="amc")    # amc/noc/refilling/emergency

    # Customer info (may or may not have a linked User account yet)
    customer_name    = db.Column(db.String(200), nullable=False)
    customer_phone   = db.Column(db.String(20))
    customer_email   = db.Column(db.String(200))
    project_name     = db.Column(db.String(200))   # e.g. "Rajshree City Centre"
    customer_address = db.Column(db.Text)

    # Status flow: draft -> sent -> viewed -> negotiation_requested -> accepted / rejected
    status             = db.Column(db.String(30), default="draft")
    negotiation_note   = db.Column(db.Text)   # customer counter-offer / message
    staff_response     = db.Column(db.Text)   # staff reply to negotiation

    # Hassle-free no-login link: the engineer WhatsApps /q/<public_token> to the
    # client, who opens it without any login/OTP. Generated on first share.
    public_token   = db.Column(db.String(40), unique=True, index=True)

    # Payment (manual now / gateway-ready). payment_status: pending / paid.
    # payment_method: upi / cash / cheque. payment_reference holds a UTR / cheque
    # number / note. payment_marked_at is when staff (or a future gateway webhook)
    # confirmed the money was received.
    payment_status    = db.Column(db.String(20), default="pending")
    payment_method    = db.Column(db.String(20))
    payment_reference = db.Column(db.String(120))
    payment_marked_at = db.Column(db.DateTime, nullable=True)
    # Wave 11 — payment-gateway (Razorpay) order/payment ids, gateway-ready
    gateway_order_id   = db.Column(db.String(120), nullable=True)
    gateway_payment_id = db.Column(db.String(120), nullable=True)

    # Financial
    gst_percent = db.Column(db.Float, default=18.0)
    valid_days  = db.Column(db.Integer, default=7)
    notes       = db.Column(db.Text)          # internal / footer notes

    # Optional links to existing records
    contract_id        = db.Column(db.Integer, db.ForeignKey("contracts.id"),       nullable=True)
    refill_order_id    = db.Column(db.Integer, db.ForeignKey("refill_orders.id"),   nullable=True)
    service_request_id = db.Column(db.Integer, db.ForeignKey("service_requests.id"),nullable=True)
    customer_id        = db.Column(db.Integer, db.ForeignKey("users.id"),           nullable=True)
    created_by_id      = db.Column(db.Integer, db.ForeignKey("users.id"),           nullable=True)

    # Timestamps
    created_at   = db.Column(db.DateTime, default=datetime.utcnow)
    sent_at      = db.Column(db.DateTime, nullable=True)
    viewed_at    = db.Column(db.DateTime, nullable=True)
    responded_at = db.Column(db.DateTime, nullable=True)

    items      = db.relationship("ServiceQuotationItem", backref="quotation",
                                 lazy=True, cascade="all, delete-orphan",
                                 order_by="ServiceQuotationItem.sort_order, ServiceQuotationItem.id")
    created_by = db.relationship("User", foreign_keys=[created_by_id])
    customer   = db.relationship("User", foreign_keys=[customer_id])

    # ------------------------------------------------------------------ helpers
    @property
    def subtotal(self):
        return sum(i.total for i in self.items)

    @property
    def gst_amount(self):
        return round(self.subtotal * (self.gst_percent or 0) / 100, 2)

    @property
    def grand_total(self):
        return self.subtotal + self.gst_amount

    @property
    def reference(self):
        return self.quotation_number or f"SQ-{self.id:05d}"

    def generate_number(self):
        """Assign QUO-YY-YY-NNNNN number (Indian financial year Apr-Mar)."""
        today = date.today()
        fy_start = today.year if today.month >= 4 else today.year - 1
        fy_end   = fy_start + 1
        prefix   = f"QUO-{str(fy_start)[-2:]}-{str(fy_end)[-2:]}-"
        last = (ServiceQuotation.query
                .filter(ServiceQuotation.quotation_number.like(prefix + "%"))
                .order_by(ServiceQuotation.id.desc())
                .first())
        try:
            last_num = int(last.quotation_number.split("-")[-1]) if last else 0
        except Exception:
            last_num = 0
        self.quotation_number = f"{prefix}{last_num + 1:05d}"

    # items grouped by category — used in PDF and display templates
    @property
    def items_by_category(self):
        cats = {}
        for item in self.items:
            cats.setdefault(item.category or "General", []).append(item)
        return cats

    @property
    def status_label(self):
        return {
            "draft":                  "Draft",
            "sent":                   "Sent to client",
            "viewed":                 "Viewed by client",
            "negotiation_requested":  "Negotiation requested",
            "accepted":               "Accepted",
            "rejected":               "Rejected",
        }.get(self.status, self.status.replace("_", " ").title())

    @property
    def is_editable(self):
        # Editable while drafting, and again when the client asks to negotiate
        # so staff can revise rates and re-send a fresh quote for acceptance.
        return self.status in ("draft", "negotiation_requested")

    # ---------------------------------------------------------------- no-login link
    def ensure_token(self):
        """Assign a URL-safe public token (idempotent) for the share link."""
        if not self.public_token:
            import secrets
            self.public_token = secrets.token_urlsafe(12)
        return self.public_token

    @property
    def is_paid(self):
        return self.payment_status == "paid"

    @property
    def payment_method_label(self):
        return {
            "upi":    "UPI / GPay",
            "cash":   "Cash",
            "cheque": "Cheque",
        }.get(self.payment_method, (self.payment_method or "").title())


class ServiceQuotationItem(db.Model):
    __tablename__ = "service_quotation_items"

    id         = db.Column(db.Integer, primary_key=True)
    quotation_id = db.Column(db.Integer, db.ForeignKey("service_quotations.id"), nullable=False)
    category   = db.Column(db.String(100))  # e.g. "Maintenance", "Phase-8 (NOC & Consulting)"
    description = db.Column(db.Text, nullable=False)
    unit       = db.Column(db.String(20), default="Job")   # Job / No. / Mtr / Set
    quantity   = db.Column(db.Float, default=1.0)
    rate       = db.Column(db.Float, default=0.0)
    sort_order = db.Column(db.Integer, default=0)

    @property
    def total(self):
        return round((self.quantity or 0) * (self.rate or 0), 2)


# --------------------------------------------------------------------------- #
# Post-visit customer feedback (drives technician performance dashboard)
# --------------------------------------------------------------------------- #
class VisitFeedback(db.Model):
    __tablename__ = "visit_feedback"

    id            = db.Column(db.Integer, primary_key=True)
    visit_id      = db.Column(db.Integer, db.ForeignKey("visits.id"), unique=True)
    customer_id   = db.Column(db.Integer, db.ForeignKey("users.id"))
    technician_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)

    # Five dimensions, each rated 1-5
    rating_behaviour     = db.Column(db.Integer)  # Technician attitude & behaviour
    rating_quality       = db.Column(db.Integer)  # Quality of work completed
    rating_punctuality   = db.Column(db.Integer)  # On-time arrival
    rating_communication = db.Column(db.Integer)  # Explanation & communication
    rating_overall       = db.Column(db.Integer)  # Overall satisfaction

    comment    = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    # Unique token embedded in feedback email link — no login required to submit
    token      = db.Column(db.String(64), unique=True)

    visit      = db.relationship("Visit",      foreign_keys=[visit_id])
    customer   = db.relationship("User",       foreign_keys=[customer_id])
    technician = db.relationship("User",       foreign_keys=[technician_id])

    @property
    def avg_rating(self):
        vals = [r for r in [self.rating_behaviour, self.rating_quality,
                             self.rating_punctuality, self.rating_communication,
                             self.rating_overall] if r is not None]
        return round(sum(vals) / len(vals), 1) if vals else None

    @property
    def is_submitted(self):
        return self.rating_overall is not None


# --------------------------------------------------------------------------- #
# Customer journey event log (timeline in customer profile)
# --------------------------------------------------------------------------- #
class CustomerJourneyEvent(db.Model):
    __tablename__ = "customer_journey_events"

    id          = db.Column(db.Integer, primary_key=True)
    customer_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    event_type  = db.Column(db.String(50), nullable=False)
    # quote_requested / quote_sent / quote_viewed / negotiation_requested /
    # quote_accepted / quote_rejected / contract_activated / visit_scheduled /
    # visit_completed / payment_received / feedback_given
    description    = db.Column(db.String(500))
    ref_type       = db.Column(db.String(30), nullable=True)   # service_quotation/contract/visit
    ref_id         = db.Column(db.Integer,    nullable=True)
    created_at     = db.Column(db.DateTime, default=datetime.utcnow)
    created_by_id  = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)

    customer   = db.relationship("User", foreign_keys=[customer_id])
    created_by = db.relationship("User", foreign_keys=[created_by_id])

    # icon_key maps to (svg_path_d, color) — rendered as SVG in journey.html
    EVENT_ICONS = {
        "quote_requested":       ("doc",      "blue"),
        "quote_sent":            ("send",     "navy"),
        "quote_viewed":          ("eye",      "slate"),
        "negotiation_requested": ("chat",     "amber"),
        "quote_accepted":        ("check",    "green"),
        "quote_rejected":        ("x",        "red"),
        "contract_activated":    ("shield",   "green"),
        "agreement_accepted":    ("sign",     "green"),
        "visit_scheduled":       ("calendar", "blue"),
        "visit_completed":       ("check",    "green"),
        "visit_rescheduled":     ("calendar", "amber"),
        "payment_received":      ("coin",     "green"),
        "feedback_given":        ("star",     "amber"),
        "referral_submitted":    ("users",    "blue"),
    }

    @property
    def icon_color(self):
        return self.EVENT_ICONS.get(self.event_type, ("dot", "slate"))


# --------------------------------------------------------------------------- #
# Wave 6 — Inventory (spare parts the technician picks when raising a
# material quotation linked to a visit)
# --------------------------------------------------------------------------- #
class InventoryItem(db.Model):
    __tablename__ = "inventory_items"

    id       = db.Column(db.Integer, primary_key=True)
    name     = db.Column(db.String(200), nullable=False)
    category = db.Column(db.String(80), default="Materials")
    unit     = db.Column(db.String(20), default="No.")   # No. / Kg / Mtr / Set / Job
    rate     = db.Column(db.Integer, default=0)           # default unit rate (₹)
    hsn      = db.Column(db.String(20))
    active   = db.Column(db.Boolean, default=True)

    def __repr__(self):
        return f"<InventoryItem {self.name} ₹{self.rate}>"


# --------------------------------------------------------------------------- #
# Wave 6 — Fire System Health Checkup Report (FSHCR)
# Mirrors NSE's printed inspection form. Most answers live in a JSON blob keyed
# by the section/question constants below; key header fields are columns so the
# report can be listed and linked. Can stand alone (non-AMC property survey) or
# be linked to a contract/visit.
# --------------------------------------------------------------------------- #
class HealthCheckReport(db.Model):
    __tablename__ = "health_check_reports"

    id = db.Column(db.Integer, primary_key=True)
    contract_id = db.Column(db.Integer, db.ForeignKey("contracts.id"), nullable=True)
    visit_id    = db.Column(db.Integer, db.ForeignKey("visits.id"),    nullable=True)

    property_name    = db.Column(db.String(200))
    property_address = db.Column(db.Text)
    property_contact = db.Column(db.String(120))
    inspector_name   = db.Column(db.String(120))
    inspector_contact = db.Column(db.String(120))
    report_date      = db.Column(db.Date, default=date.today)

    data       = db.Column(db.Text)     # JSON: checkbox answers, floor rows, particulars, remarks
    scan_path  = db.Column(db.String(255))   # uploaded scanned copy (fallback to in-app form)
    status     = db.Column(db.String(20), default="draft")   # draft / completed

    created_by_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)
    created_at    = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at    = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    contract   = db.relationship("Contract", foreign_keys=[contract_id])
    visit      = db.relationship("Visit", foreign_keys=[visit_id])
    created_by = db.relationship("User", foreign_keys=[created_by_id])

    @property
    def reference(self):
        return f"HCR-{self.id:05d}"

    @property
    def answers(self):
        """Deserialised JSON payload (always a dict)."""
        import json
        if not self.data:
            return {}
        try:
            return json.loads(self.data)
        except (ValueError, TypeError):
            return {}

    def set_answers(self, payload):
        import json
        self.data = json.dumps(payload)

    # ---- Form structure constants (used by the in-app form and the PDF) -----
    # Yes/No/N-A inspection questions grouped by section.
    SECTIONS = [
        ("Pump House", [
            ("ph_heat",  "Heat in Pump room is 25°C or higher"),
            ("ph_vent",  "The room has ventilation for smoke to exit"),
            ("ph_water", "Excessive water does not appear on the floor"),
            ("ph_coupling", "Coupling Guard is in place"),
        ]),
        ("Pump System", [
            ("ps_valves", "Pump suction, discharge and bypass valves are open"),
            ("ps_leaks",  "No piping or hose leaks"),
            ("ps_seal",   "Fire Pump leaking one drop of water per second at seals"),
            ("ps_suction", "Suction line pressure is within acceptable range"),
            ("ps_system",  "System line pressure is within acceptable range"),
            ("ps_switch",  "Pressure Switch & Gauge installed with Ball Valve, in good condition"),
            ("ps_reservoir", "Suction reservoir is full"),
            ("ps_flowtest",  "Water flow test valves are in closed position"),
        ]),
        ("Electrical Systems", [
            ("es_pilot",   "Controller pilot light (Power on Light) is illuminated"),
            ("es_transfer", "Transfer switch normal power is illuminated"),
            ("es_isolating", "Isolating switch for standby power is closed"),
            ("es_reverse", "Reverse phase alarm light is NOT illuminated"),
            ("es_normal",  "Normal phase rotation light is illuminated"),
            ("es_oil",     "Oil level in vertical motor sight glass is within range"),
            ("es_jockey",  "Jockey / Pressure Maintenance Pump has power"),
            ("es_switches", "Pressure Switch and Flow Switch are in good condition"),
        ]),
        ("Diesel Engine Systems", [
            ("de_fuel",    "Diesel fuel tank is at least two-thirds full"),
            ("de_selector", "Controller Selector Switch is in AUTO position"),
            ("de_voltage", "Voltage readings for Batteries are within range"),
            ("de_charge",  "Charging Current readings within range for batteries"),
            ("de_power",   "Battery power light indicates ON (failure light OFF)"),
        ]),
    ]

    # Hydrant & sprinkler items that also carry a "(Nos)" count.
    HYDRANT_ITEMS = [
        ("hy_line",       "Hydrant water line (G.I./M.S.) — no damage/rust/leakage", False),
        ("hy_valve",      "Hydrant Valve", True),
        ("hy_nozzle",     "Shut-Off Nozzle", True),
        ("hy_hosebox",    "Hose Box", True),
        ("hy_butterfly",  "Butter-Fly Valve", True),
        ("hy_branch",     "Branch Pipe", True),
        ("hy_sluice",     "Sluice Valve", True),
        ("hy_rrl",        "RRL / Hose Pipe", True),
        ("hy_strainer",   "Strainer", True),
        ("hy_ball",       "Ball Valve", True),
        ("hy_foot",       "Foot Valve", True),
        ("hy_reelpipe",   "Reel Pipe", True),
        ("hy_sprinkler",  "Sprinkler Head", True),
        ("hy_reeldrum",   "Reel Drum", True),
        ("hy_nrv",        "NRV", True),
        ("hy_air",        "Air Release Valve", True),
        ("hy_pswitch",    "Pressure Switch", True),
        ("hy_pgauge",     "Pressure Gauge", True),
        ("hy_inlet",      "Inlet Valve (2 Way / 4 Way)", False),
        ("hy_charged",    "Line charged with 4 Kg to 6 Kg pressure", False),
    ]

    # Floor-wise equipment table columns.
    FLOOR_COLUMNS = [
        "Hydrant Valve", "Hose Box", "Branch Pipe", "RRL/Hose Pipe", "Reel Hose",
        "MCP/Hooter", "Pump On/Off Switch", "Signages", "ABC Ext.", "CO2 Ext.", "Sprinklers",
    ]
    FLOOR_NAMES = [
        "Basement (2)", "Basement (1)", "Ground Floor", "1st Floor", "2nd Floor",
        "3rd Floor", "4th Floor", "5th Floor", "6th Floor", "7th Floor", "8th Floor",
        "9th Floor", "10th Floor", "11th-15th Floor", "16th-20th Floor", "21st-30th Floor",
    ]

    # Numbered particulars (free-text answers).
    PARTICULARS = [
        ("p_charged",   "Is the line charged? (Comments)"),
        ("p_ug_tank",   "Underground Tank Capacity (Ltrs)"),
        ("p_upg_tank",  "Upper-ground Tank Capacity (Ltrs)"),
        ("p_oh_tank",   "Over Head Tank Capacity (Ltrs)"),
        ("p_diesel",    "Diesel Engine — HP / LPM / Make"),
        ("p_mainpump",  "Main Pump — HP / LPM / Make"),
        ("p_sprpump",   "Sprinkler Pump — HP / LPM / Make"),
        ("p_jockey1",   "Jockey Pump (1) — HP / LPM / Make"),
        ("p_jockey2",   "Jockey Pump (2) — HP / LPM / Make"),
        ("p_booster",   "Booster Pump — HP / LPM / Make"),
        ("p_pressure",  "Does the gauge indicate required pressure (4-6 Kg)?"),
        ("p_zones",     "Fire Alarm Panel — zones total / in use"),
        ("p_smoke",     "Are all Smoke Detectors working?"),
        ("p_cables",    "Are main cables painted with Fire Retardant paint?"),
        ("p_hydpaint",  "Is Hydrant System painted, no washout of red?"),
        ("p_exits",     "How many emergency exit doors?"),
    ]

    # Yes/No extras at the foot of the form.
    EXTRAS = [
        ("ex_evac",   "Evacuation Plan"),
        ("ex_pa",     "Public Addressing System"),
        ("ex_lifts",  "External Lifts"),
    ]


# --------------------------------------------------------------------------- #
# Referrals (client recommends NSE to someone after good AMC experience)
# --------------------------------------------------------------------------- #
class Referral(db.Model):
    __tablename__ = "referrals"

    id               = db.Column(db.Integer, primary_key=True)
    contract_id      = db.Column(db.Integer, db.ForeignKey("contracts.id"), nullable=False)
    submitted_by_id  = db.Column(db.Integer, db.ForeignKey("users.id"),    nullable=False)

    referee_name     = db.Column(db.String(120), nullable=False)
    referee_phone    = db.Column(db.String(20))
    referee_company  = db.Column(db.String(200))
    referee_area     = db.Column(db.String(120))
    notes            = db.Column(db.Text)
    # Status: new → contacted → converted (tracking by ops team)
    status           = db.Column(db.String(20), default="new")

    created_at       = db.Column(db.DateTime, default=datetime.utcnow)

    contract      = db.relationship("Contract", backref="referrals")
    submitted_by  = db.relationship("User", foreign_keys=[submitted_by_id])


# --------------------------------------------------------------------------- #
# Site System Checking List — the floor-by-floor inspection survey an engineer
# fills on site (tablet) BEFORE raising a quotation. A quantity matrix of every
# fire-safety component per floor, plus a pump-room details table. Mirrors NSE's
# printed "system checking list" and feeds the quotation.
# --------------------------------------------------------------------------- #
class SystemCheckList(db.Model):
    __tablename__ = "system_checklists"

    id = db.Column(db.Integer, primary_key=True)

    # Site & client header (client fields let the survey flow straight into a quote)
    site_name     = db.Column(db.String(200))
    site_address  = db.Column(db.Text)
    client_name   = db.Column(db.String(200))
    client_phone  = db.Column(db.String(20))
    client_email  = db.Column(db.String(200))

    survey_date   = db.Column(db.Date, default=date.today)
    surveyed_by_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)
    status        = db.Column(db.String(20), default="draft")   # draft / completed

    # Optional links
    contract_id   = db.Column(db.Integer, db.ForeignKey("contracts.id"), nullable=True)
    service_quotation_id = db.Column(db.Integer, db.ForeignKey("service_quotations.id"), nullable=True)

    general_remarks = db.Column(db.Text)
    # All survey data in one JSON blob:
    #   {"floors": [..active floor names..],
    #    "matrix": {item: {floor: qty}},
    #    "custom_items": [names],
    #    "pumps": {pump: {col: value}},
    #    "item_remarks": {item: text}}
    data          = db.Column(db.Text)

    created_at    = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at    = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    surveyed_by   = db.relationship("User", foreign_keys=[surveyed_by_id])
    contract      = db.relationship("Contract", foreign_keys=[contract_id])

    # ----- standard structure (matches the printed format, with additions) ----
    # The original 26 items from NSE's sheet, plus a few common additions at the
    # end (marked) — engineers can also free-add custom rows in the form.
    ITEMS = [
        "Hydrant Valve", "Hose Box", "Branch Pipe", "RRL", "Hose Reel",
        "Shutt-off Nozzle", "Hose Reel Ball Valve", "Alarm Panel", "MCP",
        "Hooter", "Smoke Detector", "Sprinkler", "ABC (F.E)", "CO2 (F.E)",
        "Modular", "NRV", "Butterfly Valve", "Ball Valve", "Air Release Valve",
        "Rubber Bellow", "Strainer", "Pressure Gauge", "Pressure Switch",
        "Underground Tank (Ltr)", "Upperground Tank (Ltr)", "Terrace Tank (Ltr)",
        # ---- additions ----
        "Heat Detector", "Foam (F.E)", "Clean Agent (F.E)", "Water-CO2 (F.E)",
        "Landing Valve", "Emergency Light", "Exit Signage", "Fire Door",
        "Fire Damper",
    ]

    FLOORS = [
        "Basement 3", "Basement 2", "Basement 1", "Ground",
        "1st Floor", "2nd Floor", "3rd Floor", "4th Floor", "5th Floor",
        "6th Floor", "7th Floor", "8th Floor", "9th Floor", "10th Floor",
        "11th Floor", "12th Floor", "13th Floor", "14th Floor", "15th Floor",
        "16th Floor", "17th Floor", "18th Floor", "19th Floor", "20th Floor",
        "21st Floor", "22nd Floor", "23rd Floor", "24th Floor", "Terrace",
    ]

    PUMPS = [
        "Hydrant Pump", "Sprinkler Pump", "Jockey Pump 1", "Jockey Pump 2",
        "Diesel Engine", "Booster Pump", "Fire Electrical Panel",
    ]
    PUMP_COLUMNS = ["Qty", "HP", "LPM", "Make", "Condition", "Area"]

    @property
    def reference(self):
        return f"SCL-{self.id:04d}"

    # ----- JSON helpers -------------------------------------------------------
    def get_data(self):
        import json
        if not self.data:
            return {}
        try:
            return json.loads(self.data)
        except Exception:
            return {}

    def set_data(self, payload):
        import json
        self.data = json.dumps(payload)

    @property
    def active_floors(self):
        d = self.get_data()
        return d.get("floors") or ["Ground"]

    @property
    def all_items(self):
        """Standard items + any custom rows the engineer added."""
        d = self.get_data()
        return list(self.ITEMS) + [c for c in d.get("custom_items", []) if c]

    @property
    def matrix(self):
        return self.get_data().get("matrix", {})

    @property
    def pumps_data(self):
        return self.get_data().get("pumps", {})

    @property
    def item_remarks(self):
        return self.get_data().get("item_remarks", {})

    @property
    def item_totals(self):
        """Total count of each item across all active floors (skips tank litres)."""
        out = {}
        mat = self.matrix
        for item in self.all_items:
            if "Tank" in item:   # tanks store litres, not a count
                continue
            total = 0
            for _floor, val in (mat.get(item) or {}).items():
                try:
                    total += float(val)
                except (TypeError, ValueError):
                    pass
            if total:
                out[item] = int(total) if total == int(total) else total
        return out


# --------------------------------------------------------------------------- #
# Support Tickets / Complaints
# --------------------------------------------------------------------------- #
class SupportTicket(db.Model):
    """Customer complaint or support request linked to a contract or visit."""
    __tablename__ = "support_tickets"

    id               = db.Column(db.Integer, primary_key=True)
    customer_id      = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    contract_id      = db.Column(db.Integer, db.ForeignKey("contracts.id"), nullable=True)
    visit_id         = db.Column(db.Integer, db.ForeignKey("visits.id"), nullable=True)

    title            = db.Column(db.String(200), nullable=False)
    description      = db.Column(db.Text, nullable=False)
    voice_note       = db.Column(db.Text)   # speech-to-text transcript

    status           = db.Column(db.String(20), default="open")
    # open → acknowledged (staff replied within 24h) → resolved → closed
    priority         = db.Column(db.String(20), default="normal")  # low/normal/high

    # Staff response fields
    staff_reply      = db.Column(db.Text)
    replied_by_id    = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)
    replied_at       = db.Column(db.DateTime)
    resolved_at      = db.Column(db.DateTime)
    resolved_by_id   = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)

    # Re-trigger tracking (client can nudge again if ignored >24h)
    retriggered_at   = db.Column(db.DateTime)
    retrigger_count  = db.Column(db.Integer, default=0)

    created_at       = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at       = db.Column(db.DateTime, default=datetime.utcnow,
                                  onupdate=datetime.utcnow)

    customer     = db.relationship("User", foreign_keys=[customer_id],
                                   backref="support_tickets")
    replied_by   = db.relationship("User", foreign_keys=[replied_by_id])
    resolved_by  = db.relationship("User", foreign_keys=[resolved_by_id])
    contract     = db.relationship("Contract", foreign_keys=[contract_id],
                                   backref="support_tickets")
    visit        = db.relationship("Visit", foreign_keys=[visit_id],
                                   backref="support_tickets")
    attachments  = db.relationship("TicketAttachment", backref="ticket", lazy=True,
                                    cascade="all, delete-orphan")

    @property
    def reference(self):
        return f"TKT-{self.id:04d}"

    STATUS_COLORS = {
        "open":           "bg-red-50 text-red-800",
        "acknowledged":   "bg-amber-50 text-amber-800",
        "resolved":       "bg-green-50 text-green-800",
        "closed":         "bg-slate-100 text-slate-600",
    }

    @property
    def status_label(self):
        return self.status.replace("_", " ").title()

    @property
    def status_color(self):
        return self.STATUS_COLORS.get(self.status, "bg-slate-100 text-slate-700")

    @property
    def is_overdue(self):
        """Open/acknowledged and no reply for >24 hours."""
        if self.status in ("resolved", "closed"):
            return False
        if self.replied_at:
            return False
        age = datetime.utcnow() - self.created_at
        return age.total_seconds() > 86400

    @property
    def can_retrigger(self):
        """Client can re-trigger if open/acknowledged and no reply for >24h."""
        return self.is_overdue


class TicketAttachment(db.Model):
    """Photos or videos attached to a support ticket."""
    __tablename__ = "ticket_attachments"

    id              = db.Column(db.Integer, primary_key=True)
    ticket_id       = db.Column(db.Integer, db.ForeignKey("support_tickets.id"),
                                 nullable=False)
    file_path       = db.Column(db.String(255), nullable=False)
    attachment_type = db.Column(db.String(20), default="photo")  # photo / video
    created_at      = db.Column(db.DateTime, default=datetime.utcnow)


class VisitReminderLog(db.Model):
    """Tracks which visit reminders have been sent to avoid duplicates."""
    __tablename__ = "visit_reminder_logs"

    id            = db.Column(db.Integer, primary_key=True)
    visit_id      = db.Column(db.Integer, db.ForeignKey("visits.id"), nullable=False)
    reminder_type = db.Column(db.String(20), nullable=False)  # 1month/1week/1day
    sent_to       = db.Column(db.String(20), nullable=False)  # customer/technician
    sent_at       = db.Column(db.DateTime, default=datetime.utcnow)

    __table_args__ = (
        db.UniqueConstraint("visit_id", "reminder_type", "sent_to",
                            name="uq_visit_reminder"),
    )


# --------------------------------------------------------------------------- #
# Wave 11 — Photo-based visit defect reports
# A technician flags a specific piece of equipment as defective during a visit,
# with a severity and photo. Surfaces to the customer for acknowledgement and
# can be turned into a material quotation.
# --------------------------------------------------------------------------- #
class VisitDefect(db.Model):
    __tablename__ = "visit_defects"

    id           = db.Column(db.Integer, primary_key=True)
    visit_id     = db.Column(db.Integer, db.ForeignKey("visits.id"), nullable=False)
    contract_id  = db.Column(db.Integer, db.ForeignKey("contracts.id"), nullable=True)
    equipment_name = db.Column(db.String(160), nullable=False)   # what is faulty
    location     = db.Column(db.String(160))
    severity     = db.Column(db.String(20), default="medium")    # low/medium/high
    description  = db.Column(db.Text)
    photo_path   = db.Column(db.String(255))
    # open → acknowledged (client saw it) → quoted → resolved
    status       = db.Column(db.String(20), default="open")
    acknowledged_at = db.Column(db.DateTime)
    reported_by_id  = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)
    created_at   = db.Column(db.DateTime, default=datetime.utcnow)

    visit       = db.relationship("Visit", backref="defects", foreign_keys=[visit_id])
    contract    = db.relationship("Contract", foreign_keys=[contract_id])
    reported_by = db.relationship("User", foreign_keys=[reported_by_id])

    @property
    def reference(self):
        return f"DEF-{self.id:04d}"

    SEVERITY_META = {
        "high":   ("High",   "red"),
        "medium": ("Medium", "amber"),
        "low":    ("Low",    "blue"),
    }

    @property
    def severity_label(self):
        return self.SEVERITY_META.get(self.severity, ("Medium", "amber"))[0]

    @property
    def severity_color(self):
        return self.SEVERITY_META.get(self.severity, ("Medium", "amber"))[1]


# --------------------------------------------------------------------------- #
# Wave 11 — Bulk WhatsApp / SMS broadcast (saved log of a broadcast a staffer
# composed; recipients are resolved from a saved filter at render time, then the
# staffer taps each wa.me link to send — free, no API).
# --------------------------------------------------------------------------- #
class Broadcast(db.Model):
    __tablename__ = "broadcasts"

    id             = db.Column(db.Integer, primary_key=True)
    title          = db.Column(db.String(160))
    message        = db.Column(db.Text, nullable=False)
    audience       = db.Column(db.String(30), default="all")   # all/active/expiring/area/plan
    audience_value = db.Column(db.String(120))                 # e.g. area name / plan id
    recipient_count = db.Column(db.Integer, default=0)
    created_by_id  = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)
    created_at     = db.Column(db.DateTime, default=datetime.utcnow)

    created_by = db.relationship("User", foreign_keys=[created_by_id])

    AUDIENCE_LABELS = {
        "all":      "All customers",
        "active":   "Active AMC customers",
        "expiring": "Contracts expiring soon",
        "area":     "By area",
        "plan":     "By plan tier",
    }

    @property
    def audience_label(self):
        base = self.AUDIENCE_LABELS.get(self.audience, self.audience)
        return f"{base}: {self.audience_value}" if self.audience_value else base


# --------------------------------------------------------------------------- #
# Wave 11 — Instalment / EMI schedule for a large AMC contract
# --------------------------------------------------------------------------- #
class Installment(db.Model):
    __tablename__ = "installments"

    id          = db.Column(db.Integer, primary_key=True)
    contract_id = db.Column(db.Integer, db.ForeignKey("contracts.id"), nullable=False)
    sequence    = db.Column(db.Integer, default=1)       # 1..N
    label       = db.Column(db.String(60))               # e.g. "Q1", "Instalment 1"
    amount      = db.Column(db.Integer, default=0)
    due_date    = db.Column(db.Date)
    paid        = db.Column(db.Boolean, default=False)
    paid_date   = db.Column(db.Date)
    payment_mode = db.Column(db.String(20))
    created_at  = db.Column(db.DateTime, default=datetime.utcnow)

    contract = db.relationship("Contract", backref="installments",
                               foreign_keys=[contract_id])

    @property
    def is_overdue(self):
        return (not self.paid) and self.due_date is not None and self.due_date < date.today()

    @property
    def days_to_due(self):
        if not self.due_date:
            return None
        return (self.due_date - date.today()).days


# --------------------------------------------------------------------------- #
# Wave 11 — Milestone / reminder dedup log (renewal reminders, anniversaries).
# Guards against re-notifying the same milestone. Keyed like VisitReminderLog.
# --------------------------------------------------------------------------- #
class MilestoneLog(db.Model):
    __tablename__ = "milestone_logs"

    id             = db.Column(db.Integer, primary_key=True)
    contract_id    = db.Column(db.Integer, db.ForeignKey("contracts.id"), nullable=False)
    milestone_type = db.Column(db.String(40), nullable=False)  # renewal_90/60/30, anniversary_1..
    sent_at        = db.Column(db.DateTime, default=datetime.utcnow)

    __table_args__ = (
        db.UniqueConstraint("contract_id", "milestone_type",
                            name="uq_milestone"),
    )
