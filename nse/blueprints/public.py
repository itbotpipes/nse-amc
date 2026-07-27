from datetime import datetime, timedelta

from flask import (Blueprint, render_template, request, redirect, url_for,
                   flash, current_app)
from flask_login import current_user, login_user

from ..extensions import db
from ..models import (AMCPlan, Contract, ServiceRequest, Enquiry, User,
                      RefillOrder, RefillItem, FormAttachment, VisitFeedback,
                      CustomerJourneyEvent, ServiceQuotation, ServiceQuotationItem)
from ..utils import notify, notify_staff, save_upload, upi_qr_data_uri

public_bp = Blueprint("public", __name__)


def _save_attachments(files, ref_type, ref_id, att_type="photo"):
    """Save a list of FileStorage objects as FormAttachment rows."""
    for f in files:
        if not f or not f.filename:
            continue
        path = save_upload(f, f"form/{ref_type}/{ref_id}",
                           {"png", "jpg", "jpeg", "gif", "webp", "pdf"})
        if path:
            db.session.add(FormAttachment(
                ref_type=ref_type, ref_id=ref_id,
                file_path=path, attachment_type=att_type))


@public_bp.route("/")
def home():
    plans = AMCPlan.query.filter_by(active=True).order_by(AMCPlan.price).all()

    # Hero dashboard card personalisation:
    #   • logged-in customer WITH a contract → show their real data
    #   • logged-in customer with NO contract (new) → hide the card
    #   • anonymous visitor / staff → show the marketing demo card (default)
    hero_contract = None
    hero_hide = False
    if current_user.is_authenticated and getattr(current_user, "role", None) == "customer":
        # Prefer an active contract linked by customer_id
        active = Contract.query.filter_by(
            customer_id=current_user.id, status="active").order_by(
            Contract.created_at.desc()).first()
        # Fall back to any contract linked by customer_id
        by_id = (Contract.query
                 .filter_by(customer_id=current_user.id)
                 .order_by(Contract.created_at.desc())
                 .first())
        hero_contract = active or by_id
        # Also look for pending contracts matched only by phone (applied before
        # the customer account existed — applicant_phone set but customer_id not)
        if not hero_contract and current_user.phone:
            by_phone = (Contract.query
                        .filter_by(applicant_phone=current_user.phone)
                        .filter(Contract.customer_id.is_(None))
                        .order_by(Contract.created_at.desc())
                        .first())
            hero_contract = by_phone
        if not hero_contract:
            hero_hide = True

    hero_ctx = {}
    if hero_contract:
        c = hero_contract
        total = c.total_visits or 0
        done = c.completed_visits
        compliance = int(round(done / total * 100)) if total else 100
        cid = c.customer_id or (current_user.id if current_user.is_authenticated else None)
        open_reqs = (ServiceRequest.query.filter(
            ServiceRequest.customer_id == cid,
            ServiceRequest.status.notin_(["completed", "cancelled"])).count()
            if cid else 0)
        last_done = max(
            (v for v in c.visits if v.status == "completed" and v.completed_date),
            key=lambda v: v.completed_date, default=None)
        hero_ctx = {
            "title": c.site_name or c.applicant_name or "Your site",
            "subtitle": (c.plan.name if c.plan else "Annual Maintenance Contract")
                        + (f" · {c.start_date.year}" if c.start_date else ""),
            "status": c.status,
            "compliance": compliance,
            "done": done, "total": total,
            "next_visit": c.next_visit,
            "equipment_count": len(c.equipment),
            "last_report": last_done,
            "open_requests": open_reqs,
        }

    return render_template("site/home.html", plans=plans,
                           hero_contract=hero_contract, hero_hide=hero_hide,
                           hero=hero_ctx)


@public_bp.route("/plans")
def plans():
    residential = AMCPlan.query.filter_by(active=True, category="residential").order_by(AMCPlan.price).all()
    commercial = AMCPlan.query.filter_by(active=True, category="commercial").order_by(AMCPlan.price).all()
    return render_template("site/plans.html", residential=residential, commercial=commercial)


@public_bp.route("/apply", methods=["GET", "POST"])
def apply():
    plans = AMCPlan.query.filter_by(active=True).order_by(AMCPlan.category, AMCPlan.price).all()
    if request.method == "POST":
        f = request.form
        plan = db.session.get(AMCPlan, int(f.get("plan_id"))) if f.get("plan_id") else None
        # Merge voice note into notes (prepend so staff see it clearly)
        voice = (f.get("voice_note") or "").strip()
        notes = (f.get("notes") or "").strip()
        combined_notes = (f"[Voice note] {voice}\n\n{notes}".strip() if voice else notes) or None
        contract = Contract(
            plan_id=plan.id if plan else None,
            status="pending",
            site_name=f.get("site_name"),
            site_address=f.get("site_address"),
            area=f.get("area"),
            applicant_name=f.get("name"),
            applicant_phone=f.get("phone"),
            applicant_email=f.get("email"),
            property_type=f.get("property_type"),
            application_notes=combined_notes,
            voice_note=voice or None,
            total_visits=plan.visits_per_year if plan else 4,
            price=plan.price if plan else 0,
            payment_mode=f.get("payment_mode", "cash"),
        )
        existing = User.query.filter_by(phone=f.get("phone"), role="customer").first()
        if existing:
            contract.customer_id = existing.id
        db.session.add(contract)
        db.session.commit()

        # Save uploaded site photos
        _save_attachments(request.files.getlist("site_photos"), "contract", contract.id, "photo")

        # ── Auto-generate a ServiceQuotation so customer can view it immediately ──
        sq_ref = None
        if plan:
            from datetime import datetime as _dt
            sq = ServiceQuotation(
                service_type="amc",
                customer_name=f.get("name"),
                customer_phone=f.get("phone"),
                customer_email=f.get("email") or None,
                project_name=f.get("site_name") or None,
                customer_address=f.get("site_address") or None,
                status="sent",
                sent_at=_dt.utcnow(),
                contract_id=contract.id,
                customer_id=existing.id if existing else None,
                gst_percent=18.0,
                valid_days=7,
                notes=f"Property type: {f.get('property_type') or 'Not specified'}",
            )
            sq.generate_number()
            db.session.add(sq)
            db.session.flush()   # need sq.id before adding items

            # Build the description from plan features if available
            feature_lines = plan.feature_list[:4] if plan.feature_list else []
            detail = (
                f"{plan.visits_per_year} maintenance visits/year | "
                f"{plan.response_time} response time"
            )
            if feature_lines:
                detail += " | " + " | ".join(feature_lines)

            db.session.add(ServiceQuotationItem(
                quotation_id=sq.id,
                category="AMC Services",
                description=f"{plan.name} — Annual Maintenance Contract\n{detail}",
                unit="Year",
                quantity=1.0,
                rate=float(plan.price),
                sort_order=1,
            ))

            # Log journey event if customer already has an account
            if existing:
                db.session.add(CustomerJourneyEvent(
                    customer_id=existing.id,
                    event_type="quote_sent",
                    description=f"Quotation {sq.quotation_number} auto-generated for {plan.name}",
                    ref_type="service_quotation", ref_id=sq.id,
                ))
                notify(existing.id,
                       f"Your quotation {sq.quotation_number} is ready",
                       f"View and accept or negotiate your {plan.name} AMC quote in the portal.",
                       link=url_for("portal.service_quotation", sq_id=sq.id))

            sq_ref = sq.quotation_number

        db.session.commit()

        if sq_ref:
            msg = ("Your quotation has been prepared based on your selected plan. "
                   "Log in with your phone number to view it, accept the price, "
                   "or request a negotiation.")
        else:
            msg = ("Our maintenance team will review your application and "
                   "call you to confirm the schedule and activate your contract.")

        return render_template("site/submitted.html",
                               kind="AMC application", reference=contract.reference,
                               sq_ref=sq_ref, message=msg)
    selected = request.args.get("plan_id", type=int)
    return render_template("site/apply.html", plans=plans, selected=selected)


@public_bp.route("/emergency", methods=["GET", "POST"])
def emergency():
    if request.method == "POST":
        f = request.form
        sr = ServiceRequest(
            request_type="emergency",
            name=f.get("name"),
            phone=f.get("phone"),
            email=f.get("email"),
            area=f.get("area"),
            location=f.get("location"),
            description=f.get("description"),
            payment_mode=f.get("payment_mode", "cash"),
            status="new",
            # Wave 11 — emergency SLA: respond within 4 hours
            sla_due_at=datetime.utcnow() + timedelta(hours=4),
        )
        existing = User.query.filter_by(phone=f.get("phone"), role="customer").first()
        if existing:
            sr.customer_id = existing.id
        db.session.add(sr)
        db.session.commit()
        notify_staff(f"Emergency request {sr.reference}",
                     f"{sr.name} · {sr.area or sr.location or ''} — respond within 4h.",
                     link=url_for("admin.service_request", req_id=sr.id))
        return render_template("site/submitted.html",
                               kind="Emergency visit request", reference=sr.reference,
                               message="Our Fire Emergency Response team has been alerted. "
                                       "We will call you shortly with the team's ETA. For an "
                                       "immediate response, please also call our hotline.")
    return render_template("site/emergency.html")


@public_bp.route("/noc", methods=["GET", "POST"])
def noc():
    if request.method == "POST":
        f = request.form
        # Voice note merges into description
        voice = (f.get("voice_note") or "").strip()
        desc = (f.get("description") or "").strip()
        combined_desc = (f"[Voice note] {voice}\n\n{desc}".strip() if voice else desc) or None
        sr = ServiceRequest(
            request_type="noc",
            name=f.get("name"),
            phone=f.get("phone"),
            email=f.get("email"),
            area=f.get("area"),
            location=f.get("location"),
            description=combined_desc,
            voice_note=voice or None,
            payment_mode=f.get("payment_mode", "cash"),
            status="new",
        )
        db.session.add(sr)
        db.session.commit()
        # Old NOC document upload
        noc_doc = request.files.get("noc_document")
        if noc_doc and noc_doc.filename:
            path = save_upload(noc_doc, f"form/service_request/{sr.id}",
                               {"pdf", "png", "jpg", "jpeg"})
            if path:
                sr.noc_document_path = path
        # Site photos
        _save_attachments(request.files.getlist("site_photos"),
                          "service_request", sr.id, "photo")
        db.session.commit()
        return render_template("site/submitted.html",
                               kind="NOC request", reference=sr.reference,
                               message="Our team will review your NOC requirement and get back "
                                       "to you with the documents and process needed.")
    return render_template("site/noc.html")


@public_bp.route("/refill", methods=["GET", "POST"])
def refill():
    if request.method == "POST":
        f = request.form
        order = RefillOrder(
            name=f.get("name"), phone=f.get("phone"), email=f.get("email"),
            area=f.get("area"), address=f.get("address"),
            service_mode=f.get("service_mode", "onsite"),
            payment_mode=f.get("payment_mode", "cash"),
            notes=f.get("notes"), status="new",
        )
        existing = User.query.filter_by(phone=f.get("phone"), role="customer").first()
        if existing:
            order.customer_id = existing.id
        types = f.getlist("item_type")
        caps = f.getlist("item_capacity")
        qtys = f.getlist("item_qty")
        for i, t in enumerate(types):
            t = (t or "").strip()
            if not t:
                continue
            cap = (caps[i].strip() if i < len(caps) else "")
            try:
                qty = int(qtys[i]) if i < len(qtys) and qtys[i] else 1
            except (ValueError, TypeError):
                qty = 1
            order.items.append(RefillItem(ext_type=t, capacity=cap, quantity=max(1, qty)))
        if not order.items:
            flash("Please add at least one extinguisher to refill.", "warning")
            return render_template("site/refill.html")
        db.session.add(order)
        db.session.commit()
        return render_template(
            "site/submitted.html", kind="Refill booking", reference=order.reference,
            message="Your extinguisher refill is booked. Our team will call to confirm "
                    "pickup/visit timing and the final amount after inspection.")
    return render_template("site/refill.html")


@public_bp.route("/enquiry", methods=["GET", "POST"])
def enquiry():
    if request.method == "POST":
        f = request.form
        e = Enquiry(
            name=f.get("name"), phone=f.get("phone"), email=f.get("email"),
            subject=f.get("subject"), message=f.get("message"),
        )
        db.session.add(e)
        db.session.commit()
        flash("Thanks! Your question has been sent — our team will reply soon.", "success")
        return redirect(url_for("public.enquiry"))
    return render_template("site/enquiry.html")


@public_bp.route("/faq")
def faq():
    return render_template("site/faq.html")


@public_bp.route("/about")
def about():
    return render_template("site/about.html")


@public_bp.route("/qr")
def qr():
    """Mobile QR code page — QR generated server-side (no CDN dependency)."""
    import socket, io, base64
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        local_ip = s.getsockname()[0]
        s.close()
    except Exception:
        local_ip = "127.0.0.1"
    from flask import current_app
    port = current_app.config.get("PORT", 5055)
    url  = f"http://{local_ip}:{port}"

    # Generate QR code as base64 PNG (qrcode is installed in .venv)
    qr_b64 = None
    try:
        import qrcode
        qr = qrcode.QRCode(version=2, box_size=10, border=3,
                           error_correction=qrcode.constants.ERROR_CORRECT_M)
        qr.add_data(url)
        qr.make(fit=True)
        img = qr.make_image(fill_color="#16235b", back_color="white")
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        qr_b64 = base64.b64encode(buf.getvalue()).decode()
    except Exception:
        pass

    return render_template("site/qr.html", url=url, local_ip=local_ip,
                           port=port, qr_b64=qr_b64)


# ─────────────────────────────────────────────────────────────────
# Post-visit feedback (token-based, no login required)
# ─────────────────────────────────────────────────────────────────

@public_bp.route("/feedback/<token>", methods=["GET", "POST"])
def visit_feedback(token):
    fb = VisitFeedback.query.filter_by(token=token).first_or_404()

    if fb.is_submitted:
        return render_template("site/feedback_done.html", fb=fb)

    if request.method == "POST":
        f = request.form
        try:
            fb.rating_behaviour     = int(f.get("rating_behaviour", 0))
            fb.rating_quality       = int(f.get("rating_quality", 0))
            fb.rating_punctuality   = int(f.get("rating_punctuality", 0))
            fb.rating_communication = int(f.get("rating_communication", 0))
            fb.rating_overall       = int(f.get("rating_overall", 0))
            fb.comment = f.get("comment", "").strip()
        except (ValueError, TypeError):
            flash("Please rate all dimensions.", "danger")
            return redirect(url_for("public.visit_feedback", token=token))

        # Log journey event
        if fb.customer_id:
            db.session.add(CustomerJourneyEvent(
                customer_id=fb.customer_id,
                event_type="feedback_given",
                description=f"Feedback submitted for {fb.visit.label} — Overall: {fb.rating_overall}/5",
                ref_type="visit", ref_id=fb.visit_id,
            ))

        db.session.commit()
        return render_template("site/feedback_done.html", fb=fb)

    return render_template("site/feedback_form.html", fb=fb)


# ─────────────────────────────────────────────────────────────────
# Custom / tailored AMC plan enquiry
# ─────────────────────────────────────────────────────────────────

@public_bp.route("/custom-plan", methods=["GET", "POST"])
def custom_plan():
    """Enquiry form for clients who need a plan beyond the standard tiers."""
    if request.method == "POST":
        f = request.form
        name     = f.get("name", "").strip()
        phone    = f.get("phone", "").strip()
        email    = f.get("email", "").strip()
        services = ", ".join(f.getlist("services")) or "Not specified"
        msg_parts = [
            f"Name: {name}",
            f"Phone: {phone}",
            f"Email: {email}" if email else None,
            f"Property type: {f.get('property_type', '')}",
            f"Floors: {f.get('floors', '')}",
            f"Area (sq ft): {f.get('area_sqft', '')}",
            f"Extinguishers on site: {f.get('extinguishers', '')}",
            f"Services required: {services}",
            f"Additional notes: {f.get('requirements', '')}" if f.get('requirements', '').strip() else None,
        ]
        message = "\n".join(p for p in msg_parts if p)
        e = Enquiry(name=name, phone=phone, email=email,
                    subject="Custom AMC Plan Enquiry",
                    message=message, status="new")
        db.session.add(e)
        db.session.commit()
        flash("Thank you! Our team will contact you within 24 hours with a tailored plan.", "success")
        return redirect(url_for("public.custom_plan"))
    return render_template("site/custom_plan.html")


# ─────────────────────────────────────────────────────────────────
# No-login public quotation link  (/q/<token>)
# The engineer WhatsApps this link; the client opens it with NO login,
# NO OTP, NO app download — sees the quote and pays. Designed for
# non-tech-savvy / elderly users: big text, 2-3 buttons, one task per screen.
# ─────────────────────────────────────────────────────────────────

def _ensure_customer_account(sq):
    """Silently create (or link) the customer's account from quote details.

    Returns the User or None. Mobile number is the login key; email is optional.
    Never overwrites an existing account — links to it by phone, then email.
    """
    if sq.customer_id:
        user = sq.customer
        if user and not current_user.is_authenticated:
            login_user(user, remember=True)
        return user
    user = None
    phone = (sq.customer_phone or "").strip()
    email = (sq.customer_email or "").strip()
    if phone:
        user = User.query.filter_by(phone=phone).first()
    if not user and email:
        user = User.query.filter_by(email=email).first()
    if not user and phone:
        user = User(
            role="customer",
            name=sq.customer_name or "Customer",
            phone=phone,
            email=email or None,
            company_name=(sq.project_name or None),
            address=(sq.customer_address or None),
        )
        db.session.add(user)
        db.session.flush()
    if user and not sq.customer_id:
        sq.customer_id = user.id
    if user and not current_user.is_authenticated:
        # Seamless for non-tech-savvy customers: they just paid/accepted via a
        # WhatsApp link, so log them straight into their portal account instead
        # of also making them do an OTP round-trip to see the same contract.
        login_user(user, remember=True)
    return user


def _get_sq_by_token(token):
    sq = ServiceQuotation.query.filter_by(public_token=token).first()
    if not sq:
        from flask import abort
        abort(404)
    return sq


@public_bp.route("/q/<token>")
def public_quote(token):
    """Senior-friendly, no-login view of a quotation."""
    sq = _get_sq_by_token(token)
    # Mark viewed (first open) — does not require a customer account.
    if sq.status == "sent":
        sq.status = "viewed"
        sq.viewed_at = datetime.utcnow()
        db.session.commit()
    upi_configured = bool(current_app.config.get("COMPANY_UPI_ID"))
    return render_template("site/quote_public.html", sq=sq,
                           upi_configured=upi_configured)


@public_bp.route("/q/<token>/pay", methods=["POST"])
def public_quote_pay(token):
    """Client picked a payment method. Accept the quote + auto-create account."""
    sq = _get_sq_by_token(token)
    method = request.form.get("method", "")
    if method not in ("upi", "cash", "cheque"):
        flash("Please choose a payment option.", "warning")
        return redirect(url_for("public.public_quote", token=token))

    _ensure_customer_account(sq)

    # Choosing to pay = accepting the quotation.
    if sq.status in ("sent", "viewed", "negotiation_requested"):
        sq.status = "accepted"
        sq.responded_at = datetime.utcnow()
        if sq.customer_id:
            db.session.add(CustomerJourneyEvent(
                customer_id=sq.customer_id,
                event_type="quote_accepted",
                description=f"Accepted {sq.reference} (₹{sq.grand_total:,.0f}) via public link",
                ref_type="service_quotation", ref_id=sq.id,
            ))
    sq.payment_method = method
    db.session.commit()

    notify_staff(
        f"Quotation {sq.reference} accepted (pay by {sq.payment_method_label})",
        f"{sq.customer_name} chose to pay ₹{sq.grand_total:,.0f} by {sq.payment_method_label}. "
        f"{'Confirm payment once received.' if method != 'upi' else 'Verify the UPI payment.'}",
        link=url_for("sq.detail_quotation", sq_id=sq.id))

    if method == "upi":
        return redirect(url_for("public.public_quote_upi", token=token))
    return redirect(url_for("public.public_quote_thanks", token=token, m=method))


@public_bp.route("/q/<token>/upi")
def public_quote_upi(token):
    """Show the UPI QR + amount so the client can scan & pay with GPay etc."""
    sq = _get_sq_by_token(token)
    vpa  = current_app.config.get("COMPANY_UPI_ID", "")
    name = current_app.config.get("COMPANY_UPI_NAME") or current_app.config["COMPANY_NAME"]
    upi_link_str, qr_uri = (None, None)
    if vpa:
        upi_link_str, qr_uri = upi_qr_data_uri(
            vpa, name, sq.grand_total, note=f"{sq.reference}")
    return render_template("site/quote_upi.html", sq=sq, vpa=vpa,
                           upi_link=upi_link_str, qr_uri=qr_uri)


@public_bp.route("/q/<token>/paid-claim", methods=["POST"])
def public_quote_paid_claim(token):
    """Client taps 'I have paid'. We don't auto-confirm (no gateway yet) — we
    flag it for staff to verify and thank the client."""
    sq = _get_sq_by_token(token)
    if not sq.payment_reference:
        sq.payment_reference = "Customer marked as paid via UPI — awaiting staff verification"
    db.session.commit()
    notify_staff(
        f"UPI payment to verify — {sq.reference}",
        f"{sq.customer_name} says they paid ₹{sq.grand_total:,.0f} by UPI. Please verify and confirm.",
        link=url_for("sq.detail_quotation", sq_id=sq.id))
    return redirect(url_for("public.public_quote_thanks", token=token, m="upi"))


@public_bp.route("/q/<token>/thanks")
def public_quote_thanks(token):
    """Friendly confirmation screen after a payment choice."""
    sq = _get_sq_by_token(token)
    method = request.args.get("m", sq.payment_method or "")
    return render_template("site/quote_thanks.html", sq=sq, method=method)


# --------------------------------------------------------------------------- #
# Wave 11 — Razorpay online payment (gateway-ready; degrades to manual flow)
# --------------------------------------------------------------------------- #
@public_bp.route("/q/<token>/pay-online")
def public_quote_pay_online(token):
    """Open Razorpay Checkout for a public quotation (no login). Falls back to
    the UPI page when the gateway is not configured."""
    from .. import payments
    sq = _get_sq_by_token(token)
    if not payments.enabled():
        return redirect(url_for("public.public_quote_upi", token=token))
    order = payments.create_order(sq.grand_total, receipt=sq.reference,
                                  notes={"quotation": sq.reference})
    if not order:
        flash("Online payment is temporarily unavailable — please use UPI or cash.",
              "warning")
        return redirect(url_for("public.public_quote", token=token))
    sq.gateway_order_id = order["id"]
    _ensure_customer_account(sq)
    db.session.commit()
    return render_template("site/quote_checkout.html", sq=sq, order=order,
                           key_id=payments.key_id())


@public_bp.route("/q/<token>/pay-online/verify", methods=["POST"])
def public_quote_pay_online_verify(token):
    """Razorpay Checkout success handler posts the payment id + signature here."""
    from .. import payments
    sq = _get_sq_by_token(token)
    order_id = request.form.get("razorpay_order_id")
    payment_id = request.form.get("razorpay_payment_id")
    signature = request.form.get("razorpay_signature")
    if payments.verify_payment_signature(order_id, payment_id, signature):
        payments.mark_quote_paid(sq, payment_id=payment_id, method="online")
        notify_staff(f"Online payment received — {sq.reference}",
                     f"{sq.customer_name} paid ₹{sq.grand_total:,.0f} online (Razorpay).",
                     link=url_for("sq.detail_quotation", sq_id=sq.id))
        return redirect(url_for("public.public_quote_thanks", token=token, m="online"))
    flash("We could not verify the payment. If money was deducted it will be "
          "reconciled — please contact us.", "warning")
    return redirect(url_for("public.public_quote", token=token))


@public_bp.route("/pay/webhook", methods=["POST"])
def razorpay_webhook():
    """Razorpay webhook — auto-confirms payment by matching gateway_order_id."""
    from .. import payments
    raw = request.get_data()
    sig = request.headers.get("X-Razorpay-Signature", "")
    if not payments.verify_webhook_signature(raw, sig):
        return ("invalid signature", 400)
    payload = request.get_json(silent=True) or {}
    try:
        entity = payload["payload"]["payment"]["entity"]
        order_id = entity.get("order_id")
        payment_id = entity.get("id")
    except (KeyError, TypeError):
        return ("ignored", 200)
    if order_id:
        sq = ServiceQuotation.query.filter_by(gateway_order_id=order_id).first()
        if sq:
            payments.mark_quote_paid(sq, payment_id=payment_id, method="online")
    return ("ok", 200)
