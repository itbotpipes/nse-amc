from datetime import date, datetime

from flask import Flask, request
from flask_login import current_user

from .config import Config
from .extensions import db, login_manager, mail
from .utils import rupees, upload_url


def create_app(config_class=Config):
    import os
    import sys
    app = Flask(__name__)
    
    # Path debugging for Vercel template resolution
    print(f"--- PATH DEBUGGING ---", file=sys.stderr)
    print(f"Current Working Directory (getcwd): {os.getcwd()}", file=sys.stderr)
    print(f"App Root Path: {app.root_path}", file=sys.stderr)
    print(f"App Template Folder: {app.template_folder}", file=sys.stderr)
    print(f"App absolute template folder: {os.path.abspath(os.path.join(app.root_path, app.template_folder or 'templates'))}", file=sys.stderr)
    try:
        t_dir = os.path.join(app.root_path, app.template_folder or 'templates')
        print(f"Templates directory exists: {os.path.exists(t_dir)}", file=sys.stderr)
        if os.path.exists(t_dir):
            print(f"Contents of templates dir: {os.listdir(t_dir)}", file=sys.stderr)
    except Exception as e:
        print(f"Error checking template folder: {e}", file=sys.stderr)
    print(f"----------------------", file=sys.stderr)

    app.config.from_object(config_class)
    app.config.setdefault("SERVER_PORT", int(os.getenv("PORT", "5055")))

    db.init_app(app)
    login_manager.init_app(app)
    mail.init_app(app)

    # Blueprints
    from .blueprints.public import public_bp
    from .blueprints.auth import auth_bp
    from .blueprints.portal import portal_bp
    from .blueprints.admin import admin_bp
    from .blueprints.chat import chat_bp
    from .blueprints.sq import sq_bp
    from .blueprints.api import api_bp   # JSON REST API for the mobile app

    app.register_blueprint(public_bp)
    app.register_blueprint(auth_bp)
    app.register_blueprint(portal_bp)
    app.register_blueprint(admin_bp)
    app.register_blueprint(chat_bp)
    app.register_blueprint(sq_bp)
    app.register_blueprint(api_bp)

    # Jinja helpers
    app.jinja_env.filters["rupees"] = rupees
    app.jinja_env.filters["upload_url"] = upload_url

    @app.context_processor
    def inject_globals():
        from .models import Notification, ServiceQuotation, SupportTicket
        unread = 0
        recent_notifications = []
        sq_action_count = 0
        reminder_count = 0
        open_ticket_count = 0
        renewal_count = 0
        if current_user and current_user.is_authenticated:
            unread = Notification.query.filter_by(
                user_id=current_user.id, read=False).count()
            recent_notifications = (Notification.query
                .filter_by(user_id=current_user.id)
                .order_by(Notification.created_at.desc())
                .limit(8).all())
            # Staff-only: quotations awaiting action + open tickets + payment reminders
            if current_user.is_staff:
                pending_sq = ServiceQuotation.query.filter(
                    ServiceQuotation.status.in_(
                        ["negotiation_requested", "accepted"])).all()
                sq_action_count = sum(
                    1 for q in pending_sq
                    if q.status == "negotiation_requested"
                    or q.contract is None or q.contract.status != "active")
                try:
                    from .reminders import payment_reminder_count
                    reminder_count = payment_reminder_count()
                except Exception:
                    reminder_count = 0
                try:
                    open_ticket_count = SupportTicket.query.filter(
                        SupportTicket.status.in_(["open", "acknowledged"])).count()
                except Exception:
                    open_ticket_count = 0
                try:
                    from .reminders import renewal_reminder_count
                    renewal_count = renewal_reminder_count()
                except Exception:
                    renewal_count = 0
                # Process visit reminders + renewal/anniversary milestones
                # idempotently (fires notifications not yet sent).
                try:
                    from .reminders import process_visit_reminders, process_milestones
                    process_visit_reminders()
                    process_milestones()
                except Exception:
                    pass
        return {
            "COMPANY_NAME": app.config["COMPANY_NAME"],
            "COMPANY_CITY": app.config["COMPANY_CITY"],
            "COMPANY_TAGLINE": app.config["COMPANY_TAGLINE"],
            "EMERGENCY_HOTLINE": app.config["EMERGENCY_HOTLINE"],
            "COMPANY_PHONE": app.config["COMPANY_PHONE"],
            "COMPANY_EMAIL": app.config["COMPANY_EMAIL"],
            "COMPANY_ADDRESS": app.config["COMPANY_ADDRESS"],
            "COMPANY_UPI_ID": app.config.get("COMPANY_UPI_ID", ""),
            "AI_ENABLED": Config.ai_enabled(),
            "unread_notifications": unread,
            "recent_notifications": recent_notifications,
            "sq_action_count": sq_action_count,
            "payment_reminder_count": reminder_count,
            "open_ticket_count": open_ticket_count,
            "renewal_reminder_count": renewal_count,
            "RAZORPAY_ENABLED": Config.razorpay_enabled(),
            "now": datetime.utcnow(),
            "today": date.today(),
        }

    @app.template_filter("datefmt")
    def datefmt(value, fmt="%d %b %Y"):
        if not value:
            return "—"
        return value.strftime(fmt)

    @app.after_request
    def _no_cache_html(response):
        """Every page here is dynamic/session-specific (notifications, role-gated
        content, live statuses) — never let a browser serve a stale cached copy.
        This matters a lot during phone-based testing: Safari's back-forward
        cache in particular will happily show a page from before your last
        template edit unless the response explicitly forbids it. Static files
        (CSS/JS/images/uploads) are left alone so they keep normal caching."""
        if not request.path.startswith("/static/"):
            response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
            response.headers["Pragma"] = "no-cache"
        return response

    with app.app_context():
        db.create_all()

    return app
