"""
JSON REST API for the Northern Star mobile app.
=================================================

The rest of this project is server-rendered HTML (Jinja templates + Flask-Login
session cookies). A native mobile app cannot consume HTML pages — it needs JSON.
This blueprint is that layer: a thin, read-mostly JSON API that reuses the exact
same models the web portal uses, so there is a single source of truth.

Design choices (so the mobile dev can rely on them):
  * Prefix: everything lives under /api/v1 (version the URL, never break clients).
  * Auth: **stateless bearer tokens**, not session cookies. A mobile app stores a
    token in secure storage and sends `Authorization: Bearer <token>` on every
    request. We sign the token with the app SECRET_KEY via itsdangerous (already a
    dependency — no new package, no DB table). Tokens carry {uid, role} and expire
    in 30 days.
  * Scope of THIS module: the Customer Portal — OTP login + contracts. Emergency,
    refills, quotations, tickets etc. follow the exact same pattern; add them as
    more routes here.
  * Every response is JSON. Errors are `{"error": "..."}` with a real HTTP status.

To wire it up it is registered in nse/__init__.py:  app.register_blueprint(api_bp)
"""

from datetime import date, datetime
from functools import wraps

from flask import Blueprint, request, jsonify, current_app, g
from itsdangerous import URLSafeTimedSerializer, BadSignature, SignatureExpired

from ..extensions import db
from ..models import (
    User, Contract, Visit, ServiceRequest, Notification,
    ServiceQuotation, Quotation, SupportTicket, VisitFeedback,
    CustomerJourneyEvent, Referral, RefillOrder, AMCPlan,
)
from ..utils import (
    generate_otp, verify_otp, notify, notify_staff,
    WAIVER_TEXT, AMC_AGREEMENT_VERSION,
)

api_bp = Blueprint("api", __name__, url_prefix="/api/v1")

TOKEN_MAX_AGE = 60 * 60 * 24 * 30   # 30 days, in seconds


# --------------------------------------------------------------------------- #
# Token auth (stateless — signed with SECRET_KEY, no DB session table)
# --------------------------------------------------------------------------- #
def _serializer():
    return URLSafeTimedSerializer(current_app.config["SECRET_KEY"], salt="mobile-api-token")


def issue_token(user):
    """Mint a signed bearer token for a logged-in user."""
    return _serializer().dumps({"uid": user.id, "role": user.role})


def _user_from_token(token):
    try:
        data = _serializer().loads(token, max_age=TOKEN_MAX_AGE)
    except (BadSignature, SignatureExpired):
        return None
    return db.session.get(User, data.get("uid"))


def token_required(fn):
    """Gate a route on a valid bearer token; exposes the user as g.current_user."""
    @wraps(fn)
    def wrapper(*args, **kwargs):
        header = request.headers.get("Authorization", "")
        token = header[7:].strip() if header.lower().startswith("bearer ") else None
        user = _user_from_token(token) if token else None
        if not user:
            return jsonify(error="Not authenticated. Send a valid Bearer token."), 401
        g.current_user = user
        return fn(*args, **kwargs)
    return wrapper


def customer_only(fn):
    """token_required + must be a customer (staff use the web ops console)."""
    @wraps(fn)
    @token_required
    def wrapper(*args, **kwargs):
        if g.current_user.role != "customer":
            return jsonify(error="This endpoint is for customer accounts."), 403
        return fn(*args, **kwargs)
    return wrapper


# --------------------------------------------------------------------------- #
# CORS — native apps don't enforce CORS, but Expo web preview + browsers do.
# Kept dependency-free (no flask-cors) and scoped to this blueprint only.
# --------------------------------------------------------------------------- #
@api_bp.after_request
def _cors(resp):
    resp.headers["Access-Control-Allow-Origin"] = "*"
    resp.headers["Access-Control-Allow-Headers"] = "Content-Type, Authorization"
    resp.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
    return resp


@api_bp.route("/<path:_any>", methods=["OPTIONS"])
@api_bp.route("/", methods=["OPTIONS"])
def _preflight(_any=None):
    return ("", 204)


# --------------------------------------------------------------------------- #
# Serializers — turn models into plain JSON dicts. These mirror what the web
# portal templates already display, so the mobile app shows the same data.
# --------------------------------------------------------------------------- #
def _d(dt):
    """Date / datetime -> ISO string (or None)."""
    if not dt:
        return None
    if isinstance(dt, datetime):
        return dt.isoformat()
    return dt.isoformat()


def user_json(u):
    return {
        "id": u.id,
        "name": u.name,
        "phone": u.phone,
        "email": u.email,
        "role": u.role,
        "area": u.area,
        "city": u.city,
        "address": u.address,
        "company_name": u.company_name,
        "gst_number": u.gst_number,
    }


def visit_json(v):
    fb = v.feedback
    return {
        "id": v.id,
        "label": v.label,
        "visit_number": v.visit_number,
        "status": v.status,
        "scheduled_date": _d(v.scheduled_date),
        "completed_date": _d(v.completed_date),
        "days_until": v.days_until,
        "work_done": v.work_done,
        "technician": v.technician.name if v.technician else None,
        "has_report": bool(v.service_report_path),
        "customer_approved": v.customer_approved,
        "rated": bool(fb and getattr(fb, "is_submitted", False)),
        "checklist_summary": v.checklist_summary,
        "onsite_duration": v.onsite_duration_label,
    }


def contract_json(c, full=False):
    grade, grade_color = c.safety_grade
    data = {
        "id": c.id,
        "reference": c.reference,
        "status": c.status,
        "site_name": c.site_name,
        "site_address": c.site_address,
        "area": c.area,
        "plan": c.plan.name if c.plan else None,
        "start_date": _d(c.start_date),
        "end_date": _d(c.end_date),
        "days_to_expiry": c.days_to_expiry,
        "price": c.price,
        "payment_status": c.payment_status,
        "total_visits": c.total_visits,
        "completed_visits": c.completed_visits,
        "safety_score": c.safety_score,
        "safety_grade": grade,
        "safety_grade_color": grade_color,
        "agreement_accepted": c.agreement_accepted,
        "certificate_issued": bool(getattr(c, "certificate_issued", False)),
        "next_visit": visit_json(c.next_visit) if c.next_visit else None,
    }
    if full:
        data["visits"] = [visit_json(v) for v in
                          sorted(c.visits, key=lambda v: v.visit_number)]
        data["workflow_steps"] = c.workflow_steps
    return data


def notification_json(n):
    return {
        "id": n.id,
        "title": n.title,
        "body": n.body,
        "link": getattr(n, "link", None),
        "read": n.read,
        "created_at": _d(n.created_at),
    }


# --------------------------------------------------------------------------- #
# Auth — phone OTP (mirrors auth.py, but returns a token instead of a session)
# --------------------------------------------------------------------------- #
@api_bp.route("/auth/otp/request", methods=["POST"])
def otp_request():
    body = request.get_json(silent=True) or {}
    phone = (body.get("phone") or "").strip()
    if len(phone) < 8:
        return jsonify(error="Please enter a valid phone number."), 400
    code = generate_otp(phone)
    # Dev flow: return the code so the app can auto-fill it (no SMS gateway yet).
    # In production, wire generate_otp() to an SMS provider and stop returning it.
    resp = {"ok": True, "message": "OTP sent."}
    if current_app.debug or not current_app.config.get("SMS_ENABLED"):
        resp["dev_code"] = code
    return jsonify(resp)


@api_bp.route("/auth/otp/verify", methods=["POST"])
def otp_verify():
    body = request.get_json(silent=True) or {}
    phone = (body.get("phone") or "").strip()
    code = (body.get("code") or "").strip()
    name = (body.get("name") or "").strip()
    if not phone or not verify_otp(phone, code):
        return jsonify(error="Invalid or expired OTP."), 401

    user = User.query.filter_by(phone=phone, role="customer").first()
    if not user:
        user = User(role="customer", phone=phone,
                    name=name or f"Customer {phone[-4:]}")
        db.session.add(user)
        db.session.commit()
    elif name and user.name.startswith("Customer "):
        user.name = name
        db.session.commit()

    return jsonify(token=issue_token(user), user=user_json(user))


# --------------------------------------------------------------------------- #
# Customer resources
# --------------------------------------------------------------------------- #
@api_bp.route("/me")
@customer_only
def me():
    return jsonify(user=user_json(g.current_user))


@api_bp.route("/contracts")
@customer_only
def contracts():
    rows = (Contract.query
            .filter_by(customer_id=g.current_user.id)
            .order_by(Contract.created_at.desc()).all())
    return jsonify(contracts=[contract_json(c) for c in rows])


@api_bp.route("/contracts/<int:contract_id>")
@customer_only
def contract_detail(contract_id):
    c = db.session.get(Contract, contract_id)
    if not c or c.customer_id != g.current_user.id:
        return jsonify(error="Contract not found."), 404
    return jsonify(contract=contract_json(c, full=True))


@api_bp.route("/notifications")
@customer_only
def notifications():
    rows = (Notification.query
            .filter_by(user_id=g.current_user.id)
            .order_by(Notification.created_at.desc()).limit(50).all())
    unread = sum(1 for n in rows if not n.read)
    return jsonify(unread=unread,
                   notifications=[notification_json(n) for n in rows])


@api_bp.route("/health")
def health():
    """Unauthenticated ping so the app can check connectivity."""
    return jsonify(ok=True, service="nse-mobile-api", time=_d(datetime.utcnow()))


# =========================================================================== #
#  MODULE 2+  — full customer portal parity (added for the React Native app)
#  Everything below reuses the same models the web portal (portal.py) uses, so
#  the mobile app and the website stay a single source of truth.
# =========================================================================== #

# --------------------------------------------------------------------------- #
# Extra serializers
# --------------------------------------------------------------------------- #
def sq_item_json(i):
    return {
        "id": i.id, "category": i.category, "description": i.description,
        "unit": i.unit, "quantity": i.quantity, "rate": i.rate, "total": i.total,
    }


def service_quote_json(sq, full=False):
    data = {
        "id": sq.id,
        "reference": sq.reference,
        "service_type": sq.service_type,
        "status": sq.status,
        "status_label": sq.status_label,
        "project_name": sq.project_name,
        "customer_name": sq.customer_name,
        "subtotal": sq.subtotal,
        "gst_percent": sq.gst_percent,
        "gst_amount": sq.gst_amount,
        "grand_total": sq.grand_total,
        "payment_status": sq.payment_status,
        "is_paid": sq.is_paid,
        "valid_days": sq.valid_days,
        "created_at": _d(sq.created_at),
        "negotiation_note": sq.negotiation_note,
        "staff_response": sq.staff_response,
    }
    if full:
        data["items"] = [sq_item_json(i) for i in sq.items]
        data["notes"] = sq.notes
        data["customer_address"] = sq.customer_address
    return data


def material_quote_json(q, full=False):
    data = {
        "id": q.id,
        "reference": q.reference,
        "status": q.status,
        "total": q.total,
        "subtotal": q.subtotal,
        "gst_amount": q.gst_amount,
        "grand_total": q.grand_total,
        "payment_status": q.payment_status,
        "is_paid": q.is_paid,
        "notes": q.notes,
        "visit_id": q.visit_id,
        "contract_id": q.contract_id,
        "created_at": _d(q.created_at),
        "rejection_acknowledged": q.rejection_acknowledged,
        "negotiation_note": q.negotiation_note,
    }
    if full:
        data["items"] = [{
            "id": it.id, "description": it.description, "quantity": it.quantity,
            "unit_price": it.unit_price, "amount": it.amount,
        } for it in q.items]
    return data


def request_json(r):
    return {
        "id": r.id,
        "reference": r.reference,
        "request_type": r.request_type,
        "status": r.status,
        "description": r.description,
        "location": r.location,
        "area": r.area,
        "scheduled_date": _d(r.scheduled_date),
        "team_eta": r.team_eta,
        "amount": r.amount,
        "payment_status": r.payment_status,
        "sla_status": r.sla_status,
        "sla_label": r.sla_label,
        "created_at": _d(r.created_at),
    }


def refill_json(o):
    return {
        "id": o.id,
        "reference": o.reference,
        "status": o.status,
        "summary": o.summary,
        "total_units": o.total_units,
        "amount": o.amount,
        "payment_status": o.payment_status,
        "scheduled_date": _d(o.scheduled_date),
        "created_at": _d(o.created_at),
    }


def ticket_json(t, full=False):
    data = {
        "id": t.id,
        "reference": t.reference,
        "title": t.title,
        "status": t.status,
        "status_label": t.status_label,
        "priority": t.priority,
        "is_overdue": t.is_overdue,
        "can_retrigger": t.can_retrigger,
        "contract_id": t.contract_id,
        "created_at": _d(t.created_at),
    }
    if full:
        data["description"] = t.description
        data["voice_note"] = t.voice_note
        data["staff_reply"] = t.staff_reply
        data["replied_at"] = _d(t.replied_at)
        data["resolved_at"] = _d(t.resolved_at)
    return data


def journey_json(e):
    icon, color = e.icon_color
    return {
        "id": e.id,
        "event_type": e.event_type,
        "description": e.description,
        "icon": icon,
        "color": color,
        "created_at": _d(e.created_at),
    }


def feedback_json(fb):
    if not fb:
        return None
    return {
        "behaviour": fb.rating_behaviour,
        "quality": fb.rating_quality,
        "punctuality": fb.rating_punctuality,
        "communication": fb.rating_communication,
        "overall": fb.rating_overall,
        "avg": fb.avg_rating,
        "comment": fb.comment,
        "submitted": fb.is_submitted,
    }


# --------------------------------------------------------------------------- #
# Dashboard summary — one call the home screen can use for badges/counts
# --------------------------------------------------------------------------- #
@api_bp.route("/dashboard")
@customer_only
def dashboard():
    u = g.current_user
    contracts = Contract.query.filter_by(customer_id=u.id).all()
    pending_quotes = (ServiceQuotation.query
                      .filter(ServiceQuotation.customer_id == u.id,
                              ServiceQuotation.status.in_(["sent", "viewed"]))
                      .count())
    open_tickets = (SupportTicket.query
                    .filter(SupportTicket.customer_id == u.id,
                            SupportTicket.status.in_(["open", "acknowledged"]))
                    .count())
    unread = Notification.query.filter_by(user_id=u.id, read=False).count()
    return jsonify(
        user=user_json(u),
        contracts=[contract_json(c) for c in contracts],
        counts={
            "contracts": len(contracts),
            "pending_quotes": pending_quotes,
            "open_tickets": open_tickets,
            "unread_notifications": unread,
        },
    )


# --------------------------------------------------------------------------- #
# Profile — read is /me (above); this updates it
# --------------------------------------------------------------------------- #
@api_bp.route("/me", methods=["PUT", "POST"])
@customer_only
def update_me():
    u = g.current_user
    body = request.get_json(silent=True) or {}
    for field in ("name", "area", "city", "address", "email",
                  "company_name", "gst_number"):
        if field in body and body[field] is not None:
            setattr(u, field, str(body[field]).strip())
    db.session.commit()
    return jsonify(user=user_json(u))


# --------------------------------------------------------------------------- #
# Visit detail + rating
# --------------------------------------------------------------------------- #
@api_bp.route("/visits/<int:visit_id>")
@customer_only
def visit_detail(visit_id):
    v = db.session.get(Visit, visit_id)
    if not v or not v.contract or v.contract.customer_id != g.current_user.id:
        return jsonify(error="Visit not found."), 404
    data = visit_json(v)
    data["contract_reference"] = v.contract.reference
    data["contract_id"] = v.contract_id
    data["feedback"] = feedback_json(v.feedback)
    data["material_quotes"] = [material_quote_json(q) for q in v.material_quotes]
    data["checklist"] = [{
        "item": ci.item, "status": ci.status, "note": ci.note,
    } for ci in sorted(v.checklist_items, key=lambda x: x.sort_order or 0)]
    return jsonify(visit=data)


@api_bp.route("/visits/<int:visit_id>/rate", methods=["POST"])
@customer_only
def visit_rate(visit_id):
    v = db.session.get(Visit, visit_id)
    if not v or not v.contract or v.contract.customer_id != g.current_user.id:
        return jsonify(error="Visit not found."), 404
    if v.status != "completed":
        return jsonify(error="You can only rate a completed visit."), 400
    body = request.get_json(silent=True) or {}

    def _clamp(x):
        try:
            return max(1, min(5, int(x)))
        except (TypeError, ValueError):
            return None

    fb = v.feedback or VisitFeedback(visit_id=v.id, customer_id=g.current_user.id)
    fb.rating_behaviour = _clamp(body.get("behaviour"))
    fb.rating_quality = _clamp(body.get("quality"))
    fb.rating_punctuality = _clamp(body.get("punctuality"))
    fb.rating_communication = _clamp(body.get("communication"))
    fb.rating_overall = _clamp(body.get("overall"))
    fb.comment = (body.get("comment") or "").strip()
    fb.technician_id = v.technician_id
    if fb.rating_overall is None:
        return jsonify(error="Overall rating is required."), 400
    if fb.id is None:
        db.session.add(fb)
    v.customer_approved = True
    v.approved_at = datetime.utcnow()
    db.session.commit()
    if v.technician_id:
        notify(v.technician_id, "Visit rated",
               f"{v.contract.reference}: client rated {fb.avg_rating}/5.")
    return jsonify(ok=True, feedback=feedback_json(fb))


# --------------------------------------------------------------------------- #
# Service quotations — list / detail / accept / negotiate
# --------------------------------------------------------------------------- #
@api_bp.route("/service-quotations")
@customer_only
def service_quotations():
    from sqlalchemy import or_
    u = g.current_user
    rows = (ServiceQuotation.query
            .filter(or_(ServiceQuotation.customer_id == u.id,
                        ServiceQuotation.customer_phone == u.phone))
            .order_by(ServiceQuotation.created_at.desc()).all())
    return jsonify(quotations=[service_quote_json(q) for q in rows])


def _own_sq(sq_id):
    u = g.current_user
    sq = db.session.get(ServiceQuotation, sq_id)
    if not sq or (sq.customer_id != u.id and sq.customer_phone != u.phone):
        return None
    return sq


@api_bp.route("/service-quotations/<int:sq_id>")
@customer_only
def service_quotation_detail(sq_id):
    sq = _own_sq(sq_id)
    if not sq:
        return jsonify(error="Quotation not found."), 404
    if sq.status == "sent":
        sq.status = "viewed"
        sq.viewed_at = datetime.utcnow()
        db.session.commit()
    return jsonify(quotation=service_quote_json(sq, full=True))


@api_bp.route("/service-quotations/<int:sq_id>/accept", methods=["POST"])
@customer_only
def service_quotation_accept(sq_id):
    sq = _own_sq(sq_id)
    if not sq:
        return jsonify(error="Quotation not found."), 404
    if sq.status in ("accepted", "rejected"):
        return jsonify(error="This quotation has already been responded to."), 400
    sq.status = "accepted"
    sq.responded_at = datetime.utcnow()
    if not sq.customer_id:
        sq.customer_id = g.current_user.id
    db.session.add(CustomerJourneyEvent(
        customer_id=g.current_user.id, event_type="quote_accepted",
        description=f"Accepted quotation {sq.reference}",
        ref_type="service_quotation", ref_id=sq.id))
    db.session.commit()
    notify_staff("Quotation accepted",
                 f"{g.current_user.name} accepted {sq.reference}.")
    return jsonify(ok=True, quotation=service_quote_json(sq, full=True))


@api_bp.route("/service-quotations/<int:sq_id>/negotiate", methods=["POST"])
@customer_only
def service_quotation_negotiate(sq_id):
    sq = _own_sq(sq_id)
    if not sq:
        return jsonify(error="Quotation not found."), 404
    body = request.get_json(silent=True) or {}
    note = (body.get("note") or "").strip()
    if not note:
        return jsonify(error="Please add a message for our team."), 400
    sq.status = "negotiation_requested"
    sq.negotiation_note = note
    sq.responded_at = datetime.utcnow()
    if not sq.customer_id:
        sq.customer_id = g.current_user.id
    db.session.add(CustomerJourneyEvent(
        customer_id=g.current_user.id, event_type="negotiation_requested",
        description=f"Requested revision on {sq.reference}",
        ref_type="service_quotation", ref_id=sq.id))
    db.session.commit()
    notify_staff("Quotation — revision requested",
                 f"{g.current_user.name} on {sq.reference}: {note[:120]}")
    return jsonify(ok=True, quotation=service_quote_json(sq, full=True))


# --------------------------------------------------------------------------- #
# Material quotations (visit-linked) — detail / approve / reject-with-waiver
# --------------------------------------------------------------------------- #
def _own_material_quote(q_id):
    q = db.session.get(Quotation, q_id)
    if not q or not q.contract or q.contract.customer_id != g.current_user.id:
        return None
    return q


@api_bp.route("/quotations/<int:q_id>")
@customer_only
def material_quotation_detail(q_id):
    q = _own_material_quote(q_id)
    if not q:
        return jsonify(error="Quotation not found."), 404
    return jsonify(quotation=material_quote_json(q, full=True), waiver_text=WAIVER_TEXT)


@api_bp.route("/quotations/<int:q_id>/decide", methods=["POST"])
@customer_only
def material_quotation_decide(q_id):
    q = _own_material_quote(q_id)
    if not q:
        return jsonify(error="Quotation not found."), 404
    body = request.get_json(silent=True) or {}
    decision = (body.get("decision") or "").strip()   # approve / reject
    if decision == "approve":
        q.status = "approved"
        q.decided_at = datetime.utcnow()
        db.session.commit()
        notify_staff("Material quote approved", f"{q.reference} approved by client.")
    elif decision == "reject":
        if not body.get("waiver_accepted"):
            return jsonify(error="waiver_required", waiver_text=WAIVER_TEXT), 400
        q.status = "rejected"
        q.decided_at = datetime.utcnow()
        q.rejection_acknowledged = True
        q.waiver_text = WAIVER_TEXT
        db.session.commit()
        notify_staff("Material quote declined", f"{q.reference} declined (waiver signed).")
    else:
        return jsonify(error="decision must be 'approve' or 'reject'."), 400
    return jsonify(ok=True, quotation=material_quote_json(q, full=True))


# --------------------------------------------------------------------------- #
# Service requests (emergency / NOC) + refills — read-only lists
# --------------------------------------------------------------------------- #
@api_bp.route("/requests")
@customer_only
def service_requests():
    u = g.current_user
    from sqlalchemy import or_
    rows = (ServiceRequest.query
            .filter(or_(ServiceRequest.customer_id == u.id,
                        ServiceRequest.phone == u.phone))
            .order_by(ServiceRequest.created_at.desc()).all())
    refills = (RefillOrder.query
               .filter_by(customer_id=u.id)
               .order_by(RefillOrder.created_at.desc()).all())
    return jsonify(requests=[request_json(r) for r in rows],
                   refills=[refill_json(o) for o in refills])


# --------------------------------------------------------------------------- #
# Support tickets / complaints — list / create / detail / retrigger
# --------------------------------------------------------------------------- #
@api_bp.route("/tickets")
@customer_only
def tickets():
    rows = (SupportTicket.query
            .filter_by(customer_id=g.current_user.id)
            .order_by(SupportTicket.created_at.desc()).all())
    return jsonify(tickets=[ticket_json(t) for t in rows])


@api_bp.route("/tickets", methods=["POST"])
@customer_only
def raise_ticket():
    body = request.get_json(silent=True) or {}
    title = (body.get("title") or "").strip()
    description = (body.get("description") or "").strip()
    if not title or not description:
        return jsonify(error="Title and description are required."), 400
    contract_id = body.get("contract_id")
    if contract_id:
        c = db.session.get(Contract, contract_id)
        if not c or c.customer_id != g.current_user.id:
            contract_id = None
    t = SupportTicket(
        customer_id=g.current_user.id,
        contract_id=contract_id,
        visit_id=body.get("visit_id"),
        title=title,
        description=description,
        voice_note=(body.get("voice_note") or "").strip() or None,
        priority=(body.get("priority") or "normal"),
    )
    db.session.add(t)
    db.session.commit()
    notify_staff("New complaint raised",
                 f"{g.current_user.name}: {title}", link=f"/ops/ticket/{t.id}")
    return jsonify(ok=True, ticket=ticket_json(t, full=True))


@api_bp.route("/tickets/<int:ticket_id>")
@customer_only
def ticket_detail(ticket_id):
    t = db.session.get(SupportTicket, ticket_id)
    if not t or t.customer_id != g.current_user.id:
        return jsonify(error="Ticket not found."), 404
    return jsonify(ticket=ticket_json(t, full=True))


@api_bp.route("/tickets/<int:ticket_id>/retrigger", methods=["POST"])
@customer_only
def retrigger_ticket(ticket_id):
    t = db.session.get(SupportTicket, ticket_id)
    if not t or t.customer_id != g.current_user.id:
        return jsonify(error="Ticket not found."), 404
    if not t.can_retrigger:
        return jsonify(error="This complaint cannot be re-sent yet."), 400
    t.retriggered_at = datetime.utcnow()
    t.retrigger_count = (t.retrigger_count or 0) + 1
    db.session.commit()
    notify_staff("Complaint reminder", f"{t.reference} re-sent by client.",
                 link=f"/ops/ticket/{t.id}")
    return jsonify(ok=True, ticket=ticket_json(t, full=True))


# --------------------------------------------------------------------------- #
# Journey timeline
# --------------------------------------------------------------------------- #
@api_bp.route("/journey")
@customer_only
def journey():
    rows = (CustomerJourneyEvent.query
            .filter_by(customer_id=g.current_user.id)
            .order_by(CustomerJourneyEvent.created_at.desc()).limit(100).all())
    return jsonify(events=[journey_json(e) for e in rows])


# --------------------------------------------------------------------------- #
# Contract actions — renewal request, referral, agreement acceptance
# --------------------------------------------------------------------------- #
def _own_contract_api(contract_id):
    c = db.session.get(Contract, contract_id)
    if not c or c.customer_id != g.current_user.id:
        return None
    return c


@api_bp.route("/contracts/<int:contract_id>/renew", methods=["POST"])
@customer_only
def request_renewal(contract_id):
    c = _own_contract_api(contract_id)
    if not c:
        return jsonify(error="Contract not found."), 404
    notify_staff("Renewal requested",
                 f"{g.current_user.name} wants to renew {c.reference}.",
                 link=f"/ops/contract/{c.id}")
    db.session.add(CustomerJourneyEvent(
        customer_id=g.current_user.id, event_type="quote_requested",
        description=f"Requested renewal for {c.reference}",
        ref_type="contract", ref_id=c.id))
    db.session.commit()
    return jsonify(ok=True, message="Renewal request sent. Our team will reach out.")


@api_bp.route("/contracts/<int:contract_id>/refer", methods=["POST"])
@customer_only
def submit_referral(contract_id):
    c = _own_contract_api(contract_id)
    if not c:
        return jsonify(error="Contract not found."), 404
    body = request.get_json(silent=True) or {}
    name = (body.get("referee_name") or "").strip()
    phone = (body.get("referee_phone") or "").strip()
    if not name or not phone:
        return jsonify(error="Referee name and phone are required."), 400
    r = Referral(
        contract_id=c.id, submitted_by_id=g.current_user.id,
        referee_name=name, referee_phone=phone,
        referee_company=(body.get("referee_company") or "").strip(),
        referee_area=(body.get("referee_area") or "").strip(),
        notes=(body.get("notes") or "").strip(),
    )
    db.session.add(r)
    db.session.add(CustomerJourneyEvent(
        customer_id=g.current_user.id, event_type="referral_submitted",
        description=f"Referred {name}", ref_type="contract", ref_id=c.id))
    db.session.commit()
    notify_staff("New referral", f"{g.current_user.name} referred {name} ({phone}).")
    return jsonify(ok=True, message="Thank you! Our team will contact your referral.")


@api_bp.route("/contracts/<int:contract_id>/agreement", methods=["GET"])
@customer_only
def agreement(contract_id):
    from ..utils import AMC_AGREEMENT_CLAUSES
    c = _own_contract_api(contract_id)
    if not c:
        return jsonify(error="Contract not found."), 404
    return jsonify(
        version=AMC_AGREEMENT_VERSION,
        accepted=c.agreement_accepted,
        accepted_at=_d(c.agreement_accepted_at),
        clauses=[{"title": t, "body": b} for t, b in AMC_AGREEMENT_CLAUSES],
    )


@api_bp.route("/contracts/<int:contract_id>/agreement/accept", methods=["POST"])
@customer_only
def agreement_accept(contract_id):
    c = _own_contract_api(contract_id)
    if not c:
        return jsonify(error="Contract not found."), 404
    if c.agreement_accepted:
        return jsonify(ok=True, message="Already accepted.")
    c.agreement_accepted = True
    c.agreement_accepted_at = datetime.utcnow()
    c.agreement_version = AMC_AGREEMENT_VERSION
    db.session.add(CustomerJourneyEvent(
        customer_id=g.current_user.id, event_type="agreement_accepted",
        description=f"Accepted AMC agreement for {c.reference}",
        ref_type="contract", ref_id=c.id))
    db.session.commit()
    notify_staff("Agreement accepted",
                 f"{g.current_user.name} accepted the AMC agreement for {c.reference}.")
    return jsonify(ok=True, message="Agreement accepted. Thank you!")


# --------------------------------------------------------------------------- #
# Notifications — mark all read
# --------------------------------------------------------------------------- #
@api_bp.route("/notifications/read", methods=["POST"])
@customer_only
def notifications_read():
    Notification.query.filter_by(user_id=g.current_user.id, read=False)\
        .update({"read": True})
    db.session.commit()
    return jsonify(ok=True)


# --------------------------------------------------------------------------- #
# Plans — public list (used by the "request a new service" flow)
# --------------------------------------------------------------------------- #
@api_bp.route("/plans")
def plans():
    rows = AMCPlan.query.filter_by(active=True).all()
    return jsonify(plans=[{
        "id": p.id, "name": p.name, "tier": p.tier, "category": p.category,
        "price": p.price, "visits": p.visits_per_year,
        "response_time": p.response_time, "features": p.feature_list,
    } for p in rows])
