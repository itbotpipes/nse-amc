# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A web platform for **Northern Star Engineering** (fire & safety company, Surat) — a **"Blinkit for
fire safety"** covering four instant-response services: **AMC** (Annual Maintenance Contracts),
**emergency response**, **on-demand extinguisher refilling**, and **fire NOC assistance**. Three
surfaces in one Flask app: a public marketing/intake site, a customer portal, and a staff ops console
— plus **Tara**, an OpenRouter-backed AI assistant. The name means "star" in Sanskrit, fitting the North Star brand identity and the company tagline **"Enlightening Safety"**.

**"Blinkit" is an internal concept shorthand only — never put the competitor brand name in
customer-facing copy** (templates, AI replies). It was removed from the hero, footer, and home
subtitle on request; describe the idea in NSE's own words ("fast, all-in-one fire safety") instead.

## Commands

```bash
.venv/bin/python run.py        # run dev server → http://127.0.0.1:5055 (PORT env overrides)
.venv/bin/python seed.py       # (re)seed demo plans, staff, and a demo customer+contract

# After any schema change to nse/models.py (no migrations — db.create_all only adds tables):
rm nse_amc.db && .venv/bin/python seed.py

# Smoke-test routes without a browser (curl is in /usr/bin on macOS):
/usr/bin/curl -s -o /dev/null -w "%{http_code}" http://127.0.0.1:5055/
```

There is no build step, test suite, or linter. Verify changes by running the server and exercising
routes. The **Claude preview pane cannot run this app**: the project lives under `~/Downloads`,
which macOS TCC blocks — the sandbox gets "Operation not permitted" on every project file and cannot
read `.venv`. Always use the Bash-run server. `run.py` prepends venv `site-packages` to `sys.path`,
so it also boots under system Python (`/usr/bin/python3 run.py`) — the `.claude/launch.json` uses
`/usr/bin/python3` for exactly this reason (the preview tool's sandbox also can't read `.venv`).

Demo logins (created by `seed.py`): admin `admin@northernstar.example`/`admin123`,
technician `tech@northernstar.example`/`tech123`, customer phone `9876543210` (dev OTP is printed on
the verify screen).

## Environment constraints (important — these shaped the stack)

- **No Node.js / npm / Homebrew / Docker** on this machine. Only Python 3.9, pip, git, sqlite3. Do not
  reach for a JS toolchain; this is intentionally a Python/Flask app.
- **System LibreSSL has no `hashlib.scrypt`.** Werkzeug password hashing must use
  `method="pbkdf2:sha256"` (see `User.set_password` in `nse/models.py`). The default scrypt crashes.
- **Port 5000 is occupied by macOS AirPlay** — hence port 5055.
- Tailwind is loaded via CDN in `base.html`; there is no CSS build.

## Architecture

App-factory pattern. `nse/__init__.py` builds the app, registers blueprints, injects template globals
(company branding from config, unread-notification count, `AI_ENABLED`), defines the `datefmt` filter,
`rupees` filter, and `upload_url` filter, and calls `db.create_all()` on startup (no migrations —
schema changes to `models.py` require deleting `nse_amc.db` and re-seeding). An `after_request` hook
sends `Cache-Control: no-store` (+ `Pragma: no-cache`) on every non-`/static/` response — every page
here is dynamic/session-specific (notifications, role-gated content), so nothing should ever be served
from a browser cache. This was added after phone-based QA testing: Safari's back-forward cache was
showing pre-fix template renders on reload, making live fixes look like they hadn't landed. Static
assets are excluded and keep normal caching. If you ever add a Python-level change here (not a template
edit), remember the dev server needs a real process restart to pick it up — template edits alone
auto-reload without one.

**Blueprints** (`nse/blueprints/`), each owning one surface:
- `public.py` — unauthenticated: home, plans, apply, **refill**, emergency, noc, **about**, enquiry, faq. Forms
  write `Contract` (status `pending`), `RefillOrder` (+`RefillItem` line items), `ServiceRequest`, or
  `Enquiry` rows. Applicants/bookers are linked to an existing customer `User` by phone when one exists.
  **AMC apply auto-quotes:** when a plan is selected, the apply POST also auto-creates a `ServiceQuotation`
  (status `sent`, QUO number assigned, one line item from the plan price + 18% GST) linked to the new
  contract. The confirmation page shows the QUO reference and prompts the customer to log in and respond.
- `auth.py` — customers log in by **phone + OTP** (`/auth/otp/request` → `/auth/otp/verify`, creates the
  `User` on first verify); staff log in by **email + password** (`/auth/staff`). `_home_for(user)`
  routes to portal vs ops console by role.
- `portal.py` (`/portal`) — customer views; every handler checks `contract.customer_id == current_user.id`.
- `admin.py` (`/ops`) — staff console. Key flow: `activate_contract` creates/links the customer account
  and **auto-generates the year's `Visit` rows** evenly spaced from the start date. Visit edits handle
  photo/report uploads and notify the customer on completion. Also manages `RefillOrder`s
  (`/ops/refills`, `/ops/refill/<id>`): set status/ETA/scheduled-date/final-amount/payment, notifying
  the customer.
- `chat.py` (`/chat/ask`) — JSON endpoint for the AI widget.

**Roles & access:** `User.role` is `customer` / `technician` / `admin`. Decorators in `nse/utils.py`
(`customer_required`, `staff_required`, `admin_required`) gate handlers. `is_staff` = technician or admin.

**Template layout** (`nse/templates/`): `base.html` + `_macros.html` + `_chat_widget.html` at the
root; subdirs `public/`, `portal/`, `admin/` mirror the blueprints. `auth/` holds login screens.
`admin/` templates extend `base_ops.html`; `portal/` templates extend `base_portal.html`; `public/` and `auth/` templates extend `base.html` directly. See the "Template layout architecture" section below. All surfaces lean on `_macros.html` for `status_badge`, `refill_badge`, and the `field()` form helper.

**Domain model** (`nse/models.py`): `Contract` is the spine — it has `Visit`s (each with `VisitPhoto`s
and a service-report file path), `Equipment` (each with `RefillRecord`s), and `Quotation`s (with
`QuotationItem`s). `ServiceRequest` is the standalone emergency/NOC channel (works without a contract).
`RefillOrder` (+`RefillItem`) is the standalone **on-demand extinguisher refilling** channel (also no
contract needed) — `reference` `RF-00001`, `summary`/`total_units` helpers, surfaced in the customer
portal and the Ops Console. `FormAttachment` holds photos and documents uploaded at form-submission
time (ref_type: `contract` / `service_request` / `refill_order`; attachment_type: `photo` / `document`).
`Contract.voice_note` and `ServiceRequest.voice_note` store speech-to-text transcripts from the apply
and NOC forms. `ServiceRequest.noc_document_path` holds the uploaded old NOC for renewals.
Several models expose computed helpers used directly in templates — e.g. `Contract.reference`
(`AMC-00001`), `Contract.completed_visits`/`next_visit`, `Equipment.refill_status`
(`ok`/`due_soon`/`overdue`) and `days_to_refill`, `Quotation.total`. After changing an equipment's
refill dates, call `recompute_next_refill()` so the derived `next_refill_date` stays correct.

**AI** (`nse/ai.py`): the assistant is named **Tara** ("star" in Sanskrit — fits the North Star brand and the "Enlightening Safety" tagline). `ask(messages)` prepends a system prompt with company + AMC + **refilling** + NOC +
emergency facts (sourced from the brochure, the Maintenance SOP, and northernstarengineering.com) and
calls OpenRouter. The prompt enforces a **tidy reply format**: one-line answer, then 2-5 short `- `
bullets, `**bold**` figures, <~90 words. The chat widget (`_chat_widget.html`) renders that small
markdown subset (`**bold**`, `-`/`•` bullets, line breaks) via a safe in-JS `renderMd()` (HTML-escaped
first). If `OPENROUTER_API_KEY` is unset or starts with `PLACEHOLDER`, or the call fails, it returns a
canned fallback reply rather than erroring — the chat never hard-fails. Model is `OPENROUTER_MODEL`
(default `anthropic/claude-haiku-4.5`). Note: OpenRouter slugs are current-gen — the older
`anthropic/claude-3.5-sonnet` 404s with "No endpoints found"; verify a slug exists via
`GET https://openrouter.ai/api/v1/models` before setting it.

**Uploads (dual backend):** `save_upload` (in `utils.py`) has two backends chosen at runtime. When
`BLOB_READ_WRITE_TOKEN` is set (production on Vercel), it uploads to **Vercel Blob** via REST and returns
an absolute `https://` URL; otherwise (local dev) it writes under `nse/static/uploads/<subdir>/` and
returns a path relative to `static/`. Whatever it returns is stored on the model (`VisitPhoto.file_path`,
`Visit.service_report_path`). Templates **must not** call `url_for('static', ...)` on these directly —
use the `upload_url` Jinja filter (`p.file_path|upload_url`), which returns absolute URLs as-is and
resolves relative paths against `static/`. The report-download route (`portal.report`) likewise redirects
to the Blob URL when the stored path is absolute, else falls back to `send_from_directory`.

**Config** (`nse/config.py`): reads `.env`. Relative SQLite paths are rewritten to absolute against the
project root so the DB location is stable regardless of CWD. Branding/contact strings come from env and
surface app-wide via the context processor in `__init__.py` — `COMPANY_NAME`, `COMPANY_CITY`,
`COMPANY_TAGLINE`, `EMERGENCY_HOTLINE` (toll-free, used in `tel:` links — the top emergency strip was removed; hotline still appears in footer and hero CTA),
`COMPANY_PHONE` (direct line), `COMPANY_EMAIL`, `COMPANY_ADDRESS`. To change any of these, edit `.env`
(not the templates). When adding a new branding field, add it in three places: `config.py`, the
`inject_globals` context processor, and `.env`/`.env.example`.

**Branding assets:** the logo is the company's **official artwork**. `nse/static/img/logo-full.png` is
the full stacked logo (navy-circle + white-star + orbit emblem *above* the "NORTHERN STAR ENGINEERING"
wordmark). `nse/static/img/logo-emblem.png` is the round emblem cropped out of it with a **transparent
background** (outer white flood-filled away via Pillow, while the white star *inside* the circle is
preserved) — this is what renders in the nav, footer, chat-widget header, and favicon (via
`url_for('static', ...)`), because those sit on the dark canvas and a white-box PNG would look wrong.
The earlier hand-built `logo-emblem.svg` recreation is kept in the folder but no longer referenced. If a
new official logo is dropped in, re-crop the emblem the same way (Pillow isn't a runtime dep — it was
pip-installed into `.venv` just for this). Company facts/stats on the home page and in the AI prompt are
sourced from the live site northernstarengineering.com.

## Template layout architecture (3 base templates)

**IMPORTANT:** There are now three separate base templates. Choose the correct one when creating new templates:

| Template | Extends | Used by | Layout |
|---|---|---|---|
| `base.html` | — (root) | `public/`, `auth/` templates | Marketing top-nav + footer |
| `base_ops.html` | `base.html` | All `admin/` templates | Dark navy sidebar (238px) + warm content area |
| `base_portal.html` | `base.html` | All `portal/` templates | Sticky portal top-nav + centered content (max 1040px) |

**How it works:** `base.html` defines `{% block body %}` (the public layout). `base_ops.html` and `base_portal.html` both override `{% block body %}` to inject their own layout shells, then define `{% block content %}` inside. Child templates override `{% block content %}` as normal. This is standard Jinja2 multi-level inheritance — `{% block %}` cannot live inside `{% if %}`/`{% else %}` (Jinja2 raises "block defined twice").

**Never use `{% if request.path ... %}` in base.html to switch layouts** — it causes the "block defined twice" error. Always use the right base template instead.

**Adding a new admin page:** `{% extends "base_ops.html" %}` — gets sidebar + ops header automatically.
**Adding a new portal page:** `{% extends "base_portal.html" %}` — gets portal nav + centered content.
**Adding a new public page:** `{% extends "base.html" %}` — gets marketing nav + footer.

The Tara AI chat widget is included in `base_portal.html` and `base.html` (public) only — not in `base_ops.html` (staff don't need it).

## Styling / theme (updated — new design system)

The UI was redesigned from Claude Design prototypes in `~/Downloads/App-design/Northern Star Fire Safety Platform/`. The design has three distinct visual surfaces:

**Ops Console** (`base_ops.html`): Dark navy sidebar (`linear-gradient(180deg, #171d3a, #141834)`) + warm `#f6f5f2` content canvas. White cards with `border: 1px solid #eceae4`, `border-radius: 12px`, `box-shadow: 0 1px 2px rgba(16,24,40,.04)`. Header: sticky, `rgba(246,245,242,.92)` blur. Primary action: navy `#072076` button. Amber badge on Quotations badge; red badge on Complaints/Reminders. User profile at sidebar bottom. "New Quotation" amber button in header.

**Customer Portal** (`base_portal.html`): Sticky top nav, `rgba(246,245,242,.92)` blur. NSE logo in navy square `#072076` (white emblem inside) + "Northern Star" / "Customer Portal" subtitle. Nav links as pill buttons (active = white bg + border). User avatar (2-letter initials, navy gradient). Notification bell (native `<details>` popup, same as before). Content max-width 1040px centered.

**Public site** (`base.html`): Unchanged marketing nav. Dark mode toggle still present (public-only). Footer navy gradient.

**Design tokens (new — replaces the old palette):**
- Canvas: `#f6f5f2` (was `#faf8f5`)
- Card bg: `#ffffff`, card border: `#eceae4` (was `#e8e3db`)
- Primary navy: `#072076` (was `#16235b`) — darker, more saturated
- Sidebar bg: `#171d3a → #141834` gradient
- Text ink: `#1b1c22`, muted: `#4a4b52`, subtle: `#8a8a90`, tertiary: `#9a9aa2`
- Success: `#17864a`, danger: `#d23f3f`, warning: `#b45309`
- CSS vars: `--pri`, `--pri-h`, `--pri-dk`, `--s1`, `--s2`, `--hair`, `--hair2`, `--hair3`, `--ink` through `--ink4`, `--succ`, `--danger`, `--amber`, `--amber-h`, `--amber-lt`

**Fonts:** Inter (all weights 400–700 from Google Fonts) + JetBrains Mono (for reference numbers like `NSE-AMC-0001`, `QUO-0186`). Body `letter-spacing: -0.006em`; headings more negative.

**Status pills** (`status_badge` macro in `_macros.html`): Now use **inline `style=` attributes** (not Tailwind classes) so colors are correct regardless of Tailwind's CDN scan. Color map: active/completed/approved/paid → green `#e7f4ec / #17864a`; pending/new/warning → amber `#fbf0dc / #b45309`; danger/rejected/open → red `#fdecea / #d23f3f`; scheduled/sent/info → blue `#eef1fb / #2f6fed`; in_progress/dispatched → indigo `#ede9fe / #5b21b6`; draft/expired/closed → slate `#f1f0ee / #6b6b72`.

**`.btn-primary` behavior:** On public pages = amber `#f59e0b` (hero CTAs). On ops/portal = navy `#072076` (the `html:not(.ops-mode) .btn-primary` override in base.html handles this for public; base_ops uses explicit styles). Use `.btn-amber` for explicit amber anywhere, `.btn-primary` for the context-appropriate primary.

**Sidebar nav item classes:** `.snav` (base), `.snav.active` (white bg 8% opacity + full white text). `.snav-section` = uppercase section label (grey). Defined in `<style>` inside `base_ops.html`'s `{% block body %}`.

**Portal nav link classes:** `.pnav` (base), `.pnav.active` (white bg + border). Defined in `<style>` inside `base_portal.html`'s `{% block body %}`.

**Mobile responsiveness (post-launch-QA patch).** The dark sidebar and portal top-nav were originally desktop-only fixed layouts — on a phone the 238px sidebar ate ~60% of the screen with no way to dismiss it, and the portal's one-row nav (logo + 5 pills + name/city + bell + avatar + logout) overflowed. Fixed:
- **`base_ops.html`** (breakpoint `@media (max-width:860px)`): `.nse-sidebar` becomes `position:fixed` with `transform:translateX(-100%)`, slid open via a `.nse-sidebar-open` class toggled on `<body>` by a hamburger button (`.nse-ops-hamburger`, hidden on desktop) in the header. A `.nse-sidebar-backdrop` overlay (tap to close) sits between content and sidebar; clicking any sidebar `<a>` also closes it (inline `onclick` on the `<aside>` checking `event.target.closest('a')`). `.hidden-mobile-crumb` hides the breadcrumb "Northern Star /" prefix and the "New Quotation" button label (icon stays) below the breakpoint to prevent header overflow.
- **`base_portal.html`** (breakpoint `@media (max-width:700px)`): the 5 nav pills sit in a `.pnav-links` container (`overflow-x:auto`, scrollbar hidden) so they scroll horizontally instead of wrapping/overflowing. `.nse-user-text` (logo subtitle, avatar name/city) and `.nse-logout-text` (the word "Logout") are hidden below the breakpoint — icons/avatar stay tappable, just without the label text.
- **`home.html`** hero: a `NSE_MOBILE = window.innerWidth < 768` gate (top of `{% block scripts %}`) skips the video background entirely (`vid.pause()`, clears `src`, `vid.load()`, `display:none` — stops the download, not just hides it) and skips starting the particle-canvas `requestAnimationFrame` loop (56 points × pairwise distance every frame was visibly janky on mid-range phones) — canvas is hidden and the static `.hero-bg` navy gradient shows through untouched. Desktop behavior is unchanged.
- **Admin data tables** (Quotations, Contracts, Enquiries, Tickets, etc. — the common `<div class="card overflow-hidden"><table class="w-full text-sm">` pattern): on mobile, `overflow-hidden` was clipping right-hand columns (e.g. "Amount") instead of letting you scroll to them. `base_ops.html`'s mobile block overrides this: `.nse-ops-inner .card:has(> table)` (and the one-level-nested variant) gets `overflow-x:auto`, and `table.w-full` gets `width:auto;min-width:640px` so columns keep a readable width and the table scrolls horizontally instead of being squeezed or clipped. Applies automatically to any existing/future table using that same card+table pattern — no per-template change needed.

When adding new ops/portal pages or touching the hero, keep these breakpoints/classes in mind — don't reintroduce a fixed-width sidebar or an ungated animation loop.

**Dark mode:** Still available on public pages only (floating toggle bottom-left). Ops console and portal are light-only — no dark mode toggle shown there. `html.dark` overrides in `base.html` still work for public templates.

**No emojis policy still applies.** SVG icons only in all UI elements.

**Nav tagline**: "Enlightening Safety" (not the old "Maintenance · Refilling · Emergency" — changed on request). **No top emergency strip** — removed; the hotline still appears in the footer and in hero CTAs.

**Hero** (`home.html`): Full-viewport split two-column layout. Left: amber badge pill "India's First Fire Safety Platform" + headline **"Fire Safety, reinvented."** (amber accent on "reinvented.") + subtitle "India's first digital platform..." + 3 CTAs (amber/outline/emergency-pulse) + **animated stat counters** (600+, 700+, 7+, NBC·BIS·IS·NFPA counting up via JS IntersectionObserver). Right (desktop only): upgraded SaaS-style glass dashboard with 8 widgets: header bar (NSE Platform · LIVE), KPI strip (AMC Status/Compliance 98%/Emergency Online), contract block with animated compliance ring SVG + visit progress bar, Upcoming Inspection, QR Asset Tracking, Fire Extinguishers, Last Inspection Report, Service Requests. Two floating mini-badges (Team Dispatched, Refill Booked). **Video background** (`<video id="heroBgVid">`): loads `nse/static/video/hero.mp4` (autoplay, muted, loop, playsinline). File is **not committed** — drop a free stock MP4 (e.g. Mixkit "Aerial view glass corporate buildings at night" ID 49845, downloaded manually from mixkit.co) into `nse/static/video/hero.mp4`. On `canplaythrough`, JS fades it in to 42% opacity (2s transition) and deepens the gradient overlay; if the file is absent nothing breaks — canvas stays as background. **Canvas background** (`#heroCanvas`, z-index 0 behind video): 56-particle network with connection lines + amber radar sweep + 12-second cinematic phase cycle (calm → alert red pulse → resolve green pulse). Canvas dims to 35% opacity once video is playing (stays as particle overlay on top of footage). **Floating live notifications** (`#h-notifs`, bottom-left, desktop only): 6 notification types cycling every 3.5s with slide-in/fade-out CSS transitions. **Entrance animation**: 6 elements stagger in with `.ha.in` CSS transition class added by JS (260ms → 960ms delays). **`{% block scripts %}`** at bottom of file contains 5 JS functions (video handler, canvas, entrance, notifications, counters). Inline `<style>` block in hero section holds all hero-specific CSS classes (`.ha`, `.hb-*`, `.h-dash-*`, `.ldot`, `.cring`, etc.) to avoid polluting base.html. IMPORTANT: CSS IDs like `{#h-notifs}` in `<style>` blocks trigger Jinja comment errors — always add a space: `{ #h-notifs { ... } }`.

All theming lives in **`base.html`**:
1. **`darkMode: 'class'`** in Tailwind CDN config — `dark:` variants work when `dark` class is on `<html>`.
2. **CSS custom properties** (`:root` + `html.dark`) drive all semantic tokens. Components reference vars and auto-flip in dark mode.
3. The Tailwind CDN config defines the palette:
   - **Semantic tokens**: `bg-canvas` (`#faf8f5` warm off-white), `bg-surface-1..4` (warm gray scale), `text-ink`/`ink-muted`/`ink-subtle`/`ink-tertiary`, `hairline`/`hairline-strong`.
   - **Amber accent**: `amber` family (`DEFAULT:#f59e0b`, `hover:#d97706`, `light:#fef3c7`, `dark:#92400e`) — also available as CSS vars `--amber`, `--amber-h`, `--amber-lt`.
   - **Brand**: `bg-primary` (#16235b navy) stays for nav/hero/footer; `text-primary` = navy.
   - **Brand families**: `navy` (50→900). `star` remapped to navy.
4. **Dark mode global overrides** patch common Tailwind utilities via `html.dark .class { !important }`.
5. **Component layer** (prefer over ad-hoc utilities):
   - Buttons: `.btn` + `.btn-primary` (**amber** `#f59e0b`, dark text — changed from navy), `.btn-white`, `.btn-outline` (hero), `.btn-ghost`, `.btn-emergency` (red).
   - Surfaces: `.card`, `.card-hover`, `.eyebrow` (uppercase accent kicker).
   - Hero: `.hero-bg` (navy radial gradient) + `.hero-grid`.
   - Icons/badges: `.icon-chip` / `.icon-chip-soft`, `.num-badge`, `.star-badge`.
   - **Service tiles**: `.service-tile` + `.service-tile-icon` — icon-grid cards (used on home page "What can we help you with?" section). Hover turns border amber and icon bg to amber-light.
   - **Status track**: `.status-track` + `.status-track-step` + `.status-track-dot` (`done`/`active` modifiers) + `.status-track-line` (`done` modifier) — horizontal progress timeline (used on portal contract cards: Applied → Active → Completed).
   - **Filter chips**: `.filter-chip` + `.filter-chip.active` — pill-shaped filter tags (used on ops console dashboard). Active state = amber border + amber-light bg.
   - **Priority cards**: `.priority-card` + `.p-high` (red left border) / `.p-medium` (amber) / `.p-low` (green) / `.p-info` (blue) — request/contract list cards with colored left border indicator.
   - **Form question**: `.form-question` + `.form-question-sub` — large bold heading style for form sections ("What's the issue?").
   - Theme: `.theme-pill`. Animations: `.fade-up` + `.stagger`.

**Dark mode token values**: canvas `#0d1117`, s1 `#161c2d`, s2 `#1c2338`, ink `#dde6f8`. `.btn-primary` is amber in both modes (dark bg = `#d97706`).

**Nav background**: `.nse-nav` = `rgba(250,248,245,0.94)` light / `rgba(13,17,23,0.94)` dark.

**Chat widget** (`_chat_widget.html`): The chatbot is named **Tara** (renamed from Dhruv). Toggle button = navy circle with a gold 4-pointed star SVG + amber notification dot. Panel header = navy gradient with Tara's star avatar + green online dot. Bubble panels use inline `style="background:var(--s1);border:1px solid var(--hair)"` so they auto-flip in dark mode without Tailwind. Input placeholder: "Ask Tara anything…". `ai.py` system prompt and all template references updated to Tara.

**Key separation**: `.btn-primary` (component class) = amber action buttons. `bg-primary` (Tailwind utility) = navy, used for nav/portal links and stays navy. Never mix them.

Consequences: a color utility renders in its natural light Tailwind value unless there's a global dark override. Keep light text (`text-white`) only on dark surfaces. `home.html` / `plans.html` are the richest reference; `_macros.html` keeps `status_badge`/`refill_badge`/`field()`.

**Voice input** (`base.html` global JS + `_macros.html` `voice_note_widget` macro): uses the browser's
built-in **Web Speech API** (`SpeechRecognition` / `webkitSpeechRecognition`). No server-side component
or paid API — transcription runs entirely in the browser, result stored in a `<input type="hidden">` and
posted as plain text. `rec.lang = 'en-IN'` for Indian-accent optimisation. Falls back gracefully (shows
unsupported message) on browsers without speech support. The macro is in `_macros.html`; import it with
`{% from "_macros.html" import voice_note_widget %}`. The `photo_upload_widget` macro similarly renders a
drag-and-drop photo input with thumbnail previews; the global JS in `base.html` handles the preview.

## Conventions

- **Template inheritance:** `admin/` templates extend `base_ops.html`; `portal/` templates extend `base_portal.html`; `public/` and `auth/` templates extend `base.html`. Never use a single base template with `{% if request.path %}` branching — Jinja2 prohibits `{% block %}` inside conditionals.
- Macros from `_macros.html` (`status_badge`, `refill_badge`, `field`, `workflow_track`, `voice_note_widget`, `star_rating_inputs`). The AI chat widget is `_chat_widget.html`, included in `base.html` and `base_portal.html` (not ops).
- **Jinja string literals cannot contain escaped single quotes** (`\'`). When putting prose with apostrophes into `{% set %}` lists (e.g. the FAQ), reword to avoid them — `\'` raises a `TemplateSyntaxError`.
- Customer-facing money is rendered with the `rupees` filter (₹ with thousands separators).
- Status strings are lowercase with underscores (`in_progress`); `status_badge` maps them to colors via inline `style=` (not Tailwind classes).
- Reference numbers (`NSE-AMC-0001`, `QUO-0186`, `TKT-0001`) should use `class="mono"` for JetBrains Mono rendering.

## Hooks / automation

A project-level **`Stop` hook keeps this file honest.** `.claude/settings.json` registers
`.claude/hooks/check_claudemd.py`, which runs when a turn ends: it scans the session transcript for
`Edit`/`Write` calls and, if any **source** file (`.py`, `.html`, `.js`, `.css`, `.svg`, …) was edited
*after* the last `CLAUDE.md` edit, it blocks the stop and asks Claude to update `CLAUDE.md` (or state
the change is trivial). It guards on `stop_hook_active`, so it nudges at most once per stop sequence —
no loops. Uses system `python3` (stdlib only; no `.venv` needed). The hook is registered at session
startup, so edits to it take effect next session — verify with `/hooks`.

A second hook, **`PostToolUse` → `.claude/hooks/compact_on_claudemd.py`** (matcher
`Edit|Write|MultiEdit|NotebookEdit`), fires after every edit and, **only when the edited file is
`CLAUDE.md`**, prints a `{"systemMessage": ...}` reminder to run `/compact` (folding the freshly
updated project state into a compacted context); it stays silent for all other files. Note: hooks
**cannot** invoke `/compact` themselves — no hook event or output field starts a compaction (`PreCompact`
only runs *during* one already in progress), so this hook only surfaces the reminder; the user runs
`/compact`. Same stdlib-`python3` / next-session-activation rules as the Stop hook.

## Deployment (Vercel)

The app is wired to run as a **Vercel Python serverless function**. Key pieces:
- `api/index.py` exposes the WSGI `app` (via `create_app()`); `vercel.json` routes **all** paths to it
  (`builds` → `@vercel/python` with `includeFiles: "nse/**"` so templates + static get bundled).
- **Database:** SQLite does not persist on Vercel (read-only/ephemeral FS), so production needs a managed
  **Postgres** (Neon etc.) via `DATABASE_URL`. `config.py` normalises `postgres://` → `postgresql://` and,
  for Postgres, sets `SQLALCHEMY_ENGINE_OPTIONS` to `NullPool` + `pool_pre_ping` (serverless workers must
  not hold a pool). `create_app()` runs `db.create_all()` on cold start, so tables self-create; seed once
  by running `seed.py` with `DATABASE_URL` pointed at the remote DB.
- **Uploads:** set `BLOB_READ_WRITE_TOKEN` so `save_upload` uses Vercel Blob (see Uploads above) — the
  local-disk path would be lost between invocations.
- **Env vars:** set `SECRET_KEY`, `DATABASE_URL`, `BLOB_READ_WRITE_TOKEN`, `OPENROUTER_API_KEY`, and the
  `COMPANY_*` overrides in the Vercel dashboard (Project → Settings → Environment Variables). `.env` is
  gitignored and not deployed.
- **Deploy path:** the Vercel CLI needs Node (absent on the dev machine), so deploy via **Git integration**
  — push the repo to GitHub and connect it in Vercel. `psycopg2-binary` is in `requirements.txt` for the
  Postgres driver.

## Wave 1 features (added post-launch)

**Service Quotations** (`nse/blueprints/sq.py`, blueprint `sq`, prefix `/ops/sq/`): sales proposals for any service type (AMC/NOC/Refilling/Emergency). Status flow: `draft → sent → viewed → negotiation_requested → accepted / rejected`. Model: `ServiceQuotation` + `ServiceQuotationItem`. PDF generated via xhtml2pdf from `nse/templates/pdf/quotation.html` (matches NSE's real QUO format exactly — GST 24ALQPD0899P1ZD, 19 T&C clauses). Staff creates manually in Ops Console; **AMC apply auto-creates one** (see `public.py` above). Customer views/accepts/negotiates in portal (`/portal/service-quotation/<id>`). The portal dashboard `pending_quotes` banner queries `ServiceQuotation` by both `customer_id` and `customer_phone` (via `sqlalchemy.or_`) so quotes created before the customer's account existed are still surfaced after login.

**Email automation** (`nse/email_service.py` + Flask-Mail): Outlook SMTP (`smtp.office365.com:587`). Config in `config.py` (`MAIL_*`). Set `MAIL_PASSWORD` in `.env` to enable actual sending; `MAIL_SUPPRESS_SEND=true` (default) logs to console only. Templates in `nse/templates/email/` (7 templates). Triggers: quote sent, accepted, negotiation, visit scheduled, visit reminder, feedback request, payment confirmation.

**Post-visit feedback** (`/feedback/<token>`, public — no login): After visit marked completed in Ops Console, `_trigger_feedback()` creates a `VisitFeedback` stub with a unique token and sends the customer a feedback email link. Form captures 5 dimensions (behaviour, quality, punctuality, communication, overall) each rated 1–5. Token stored on `VisitFeedback.token`.

**Technician performance dashboard** (`/ops/technician-performance`): aggregates `VisitFeedback` ratings + on-time completion rate (completed_date ≤ scheduled_date) per technician. Rankings: top 3 shown as leaderboard cards; full table below.

**Customer journey timeline** (`CustomerJourneyEvent` model, `/portal/journey`): audit log of all key events per customer (quote sent, accepted, contract activated, visit scheduled/completed, payment received, feedback given). Written at each trigger point across blueprints.

**New models** (in `nse/models.py`): `ServiceQuotation`, `ServiceQuotationItem`, `VisitFeedback`, `CustomerJourneyEvent`. All new tables added via `db.create_all()` — no migration needed for fresh installs; existing installs must `rm nse_amc.db && seed.py`.

**New dependencies** (in `requirements.txt`): `flask-mail==0.10.0`, `xhtml2pdf==0.2.17`, `reportlab==4.5.1`. Flask-Mail registered on `mail` in `nse/extensions.py`.

## Wave 2 features (added post-Wave 1)

**Visit maintenance checklist** (`VisitChecklistItem` model, `nse/models.py`): new table (safe to add via `db.create_all()` — no DB reset needed). Stores line items per visit with `item` name, `status` (ok/issue/na), `note`, `sort_order`. `Visit.checklist_summary` property returns `{total, ok, issues}`. The admin visit form (`admin/visit.html`) has a dynamic checklist section: 12 standard fire safety items (smoke detectors, hydrant, extinguishers, alarm panel, etc.) as quick-add pills, plus free-add rows. Checklist rows are serialised to JSON and posted as `checklist_items_json`, deserialized server-side in `admin.visit`. Customer portal shows checklist summary per visit in the equipment detail page.

**Equipment detail portal page** (`/portal/equipment/<id>`, `portal.equipment_detail`): shows equipment header (name, type, location, S/N), key dates (installed, last refill, next due, interval), status alerts (overdue/due-soon banners), full refill history, and completed service visits with checklist summaries. Equipment names in the portal contract page now link to this page.

**QR code mobile page** (`/qr`, `public.qr`): auto-detects local network IP via socket, generates QR code **server-side** using the Python `qrcode` library (embedded as base64 PNG — no CDN dependency). Scan to open app on any phone on the same WiFi. Template: `public/qr.html`.

## Wave 3 features (added post-Wave 2)

**Admin visit calendar** (`/ops/calendar`, `admin.calendar`): monthly calendar grid view of all visits. Navigation arrows (prev/next month). Visits colour-coded by status (blue=scheduled, amber=in_progress, green=completed, grey=cancelled). Click any visit to go to its detail page. List view below the calendar shows all visits for the month. Links added to ops dashboard nav.

**Analytics dashboard** (`/ops/analytics`, `admin.analytics`): KPI cards (active contracts, total customers, visit completion rate, total contract value, open emergencies, overdue equipment, open refills). Bar chart of new contracts per month (last 6 months, pure CSS — no chart library). Quick links to calendar, technician performance, contracts. Links added to ops dashboard nav.

**New model**: `VisitChecklistItem` (Wave 2, in `nse/models.py`). Added to `Visit.checklist_items` relationship and imported in `admin.py`.

## Wave 4 features (quotation ↔ contract interlink + staff notifications)

The AMC apply flow already auto-creates a `ServiceQuotation` (status `sent`) linked to the new
pending `Contract` via `ServiceQuotation.contract_id`. Wave 4 makes that link **govern activation** so
staff cannot activate a contract — or change its price — until the client has accepted the quote.

**Contract↔quote helpers** (`Contract` in `nse/models.py`): a `service_quotations` backref relationship
(`foreign_keys="ServiceQuotation.contract_id"`) plus three properties — `amc_quote` (most recent linked
AMC quote, or None), `can_activate` (True only when there is no AMC quote **or** its status is
`accepted`), and `quote_locked_price` (the accepted quote's pre-GST **subtotal** as an int, else None).
`ServiceQuotation.is_editable` was broadened to `draft` **or** `negotiation_requested` so staff can
revise a quote a client is haggling over.

**Activation gating** (`admin.activate_contract`): rejects the POST with a flash when
`not c.can_activate` (contract stays `pending`); when activating, the price is taken from
`c.quote_locked_price` if present (the form's `price` field is ignored — the figure was fixed in the
quote). The contract template (`admin/contract.html`) reflects this: a colour-coded quote-status banner
with a "Review quotation →" link, then either the activation form (price input **read-only** and pulled
from the quote when locked, otherwise editable for quote-less custom contracts) or a "🔒 Activation
locked" panel when the client hasn't accepted.

**Revise & re-send** (`sq.detail_quotation`, new `revise_send` action): on a `negotiation_requested`
quote the staff `admin/sq_detail.html` page shows editable per-line-item rate inputs; saving updates the
rates, clears `negotiation_note`, sets status back to `sent`, re-emails + notifies the customer, and
logs a journey event. The client then re-accepts, which locks the new price.

**Staff notifications** (`notify_staff(title, body)` in `utils.py`): fans a `Notification` row out to
every admin+technician. Called from `portal.service_quotation_accept` / `_negotiate` so quote responses
surface in the Ops Console. Reuses the existing `Notification` model and the `unread_notifications`
count. A **notification bell** (native `<details>` popup, no JS) lives in the `base.html` nav for all
authenticated users, listing `recent_notifications` (last 8, injected by `inject_globals`) with an
unread badge and a "Mark all read" link (reuses `portal.mark_read`, which is `@login_required` only so
staff can call it). A full **`/ops/notifications`** page (`admin.notifications`, template
`admin/notifications.html`) lists the last 100. The Ops dashboard nav gained a 🔔 Notifications link and
a **Quotations badge** driven by `sq_action_count` (quotes in `negotiation_requested`, or `accepted`
**whose contract is not yet active** — once activated they need nothing more; injected globally for
staff), plus a "⚡ Quotations needing your action" panel (`sq_attention`, same filter, from the
dashboard route) and an awaiting-approval marker on each pending application card.

Note: dynamic Tailwind classes like `bg-{{ qcolor }}-50` work here because the app uses the **Tailwind
Play CDN** (runtime DOM scan), not a build-time purge — the fully-rendered class string is in the HTML.

## Wave 5 features (clickable notifications, PDF fix, on-site updates, rescheduling)

**Clickable notifications.** `Notification` gained a `link` column (where clicking navigates).
`notify(user_id, title, body, link=None)` and `notify_staff(title, body, link=None)` accept it; every
call site now passes a deep link (quote → portal/ops quote page, activation → portal contract, visit
completed/rescheduled → portal pages, etc.). The `base.html` bell renders each item as an `<a>` and the
"View all →" footer points to `admin.notifications` for staff or **`portal.notifications`** (new page,
`portal/notifications.html`) for customers. **Existing DBs need the column** —
`ALTER TABLE notifications ADD COLUMN link VARCHAR(255)` (fresh installs get it from `db.create_all`).
`inject_globals` now guards `current_user` being None so `render_template` works outside a request
(e.g. PDF generation in a job).

**Updates section moved.** The inline "Updates" list was removed from `portal/dashboard.html`; customer
notifications live only under the 🔔 bell (popup + `portal.notifications` page).

**Conditional portal buttons.** `portal.dashboard` passes `has_refills` / `has_emergencies`; the
dashboard hides the **Book refill** / **Emergency** quick-action buttons unless the customer has used
that service.

**Quotation PDF fixes.** (1) The portal "Download PDF" button hit the staff-only `/ops/sq/<id>/pdf`
(→ 403); customers now use **`portal.service_quotation_pdf`** (`/portal/service-quotation/<id>/pdf`,
ownership-checked). (2) The logo didn't embed because xhtml2pdf can't fetch `file://` URLs —
`pdf_generator._link_callback` now resolves `/static/...` URIs to real disk paths and is passed to
`pisa.CreatePDF(link_callback=...)`; the template logo `src` is `/static/img/logo-full.png`.

**Activation price is GST-inclusive.** `Contract.quote_locked_price` now returns the accepted quote's
**grand_total** (was subtotal), so the locked activation price includes GST. The contract template label
reads "🔒 locked, incl. GST" and shows the subtotal + GST% breakdown.

**Tara chatbot icon** redrawn as a friendly robot face (antenna + screen + eyes + smile) in
`_chat_widget.html` (toggle button + header avatar), replacing the 4-point star.

**Technician on-site updates** (`admin/visit.html`): the visit page now has a **payment status** form
(posts to `admin.contract_payment`, which redirects back via `request.referrer`) and a **material
quotations** card (lists `contract.quotations` + "Raise quotation" link to `admin.new_quotation`). All
`@staff_required`, so the assigned technician can update payment and raise quotes on site.

**Visit scheduling & rescheduling.** Activation logs a `contract_activated` journey event and sends the
client an "AMC activated — schedule confirmed" notification (with first-visit date + portal link). Each
visit row on `admin/contract.html` has an inline **date picker** that posts to
**`admin.reschedule_visit`**; the shared helper `_apply_reschedule(v, new_date)` (also used by
`admin.visit`) sets the date, writes a `visit_rescheduled` `CustomerJourneyEvent`, and notifies the
client. So date changes from either the visit page or the contract page are journaled + surfaced to the
customer.

## Wave 6 features (visit quotes from inventory, waiver, FSHCR, mandatory ratings, financials, roadmap, chime)

**New models** (`nse/models.py`): `InventoryItem` (spare-parts catalogue the technician picks from —
name/category/unit/rate/active; seeded in `seed.py` and inserted into existing DBs) and
`HealthCheckReport` (FSHCR). Existing-DB migration adds columns: `quotations.payment_status` /
`payment_date` / `rejection_acknowledged` / `waiver_text`; `visits.customer_approved` / `approved_at`;
`contracts.payment_date`. Fresh installs get these from `db.create_all`; existing DBs need the
`ALTER TABLE`s (run the one-off migration snippet — it's idempotent via `PRAGMA table_info`).

**Visit-linked material quotes** (`Quotation` now has a `visit` backref + `is_paid`). Technician raises
one on the visit page (`admin/visit.html`) from an **inventory picker** (datalist auto-fills the rate;
JS `invFill`/`qcalc`/`addQRow`) → `admin.visit_quotation` builds the `Quotation` (visit_id + contract_id)
and notifies the client. Client approves/declines in `portal/quotation.html`. **Declining requires a
liability waiver** — a modal shows `utils.WAIVER_TEXT`; on confirm (`waiver_accepted=1`)
`portal.quotation_decide` sets `rejection_acknowledged` + stores `waiver_text`, and the next visit
proceeds. Payment per quote is updated by the technician via `admin.quotation_payment`.

**Fire System Health Checkup Report (FSHCR)** mirrors NSE's printed form. Structure lives as constants
on `HealthCheckReport` (`SECTIONS`, `HYDRANT_ITEMS`, `FLOOR_COLUMNS`/`FLOOR_NAMES`, `PARTICULARS`,
`EXTRAS`); all answers serialise to the `data` JSON column (`answers`/`set_answers`). Form
`admin/health_report_form.html` (note: `{% set answers %}` is at **template top level**, not inside a
block, so the scripts block can read it). Floor table is a dynamic JS grid serialised to
`floors_json`. **Scan-and-upload fallback** (`scan_path`) for when the app can't be used on site. Routes:
`admin.health_reports` (list), `admin.health_report` (new/edit, one view, GET+POST), `admin.health_report_pdf`.
Can be **standalone** (non-AMC survey, no contract) or linked to a contract/visit; when `status=completed`
and linked, the client is notified and can download via `portal.health_report_pdf` (shown on
`portal/contract.html`). PDF: `pdf/health_report.html` via `generate_health_report_pdf`.

**Visit completion → branded report + mandatory rating.** On completed, the client gets a 🎉
congratulations notification. `portal/visit.html` shows a congrats banner, a **branded service-report
PDF** (`pdf/service_report.html` via `generate_service_report_pdf` / `portal.service_report_pdf` — header,
checklist, work done, photos), and an **Approve & rate** popup. Rating is compulsory (JS star widget
`nseStar` + `star_rating_inputs` macro, 5 dimensions) → `portal.visit_approve` writes `VisitFeedback`,
sets `Visit.customer_approved`, and notifies the technician + ops console. `generate_service_report_pdf`
and `generate_health_report_pdf` share `_company_ctx()` in `pdf_generator.py`.

**Financial dashboard + roadmap.** `admin.financials` (`/ops/financials`, template `admin/financials.html`)
is contract-wise: the AMC fee (paid y/n + date) plus every approved visit-linked quote (paid y/n + date),
with billed/received/outstanding KPIs. `admin.contract_payment` now stamps `payment_date` + logs a
`payment_received` journey event. **Workflow roadmap** macro `workflow_track` (`_macros.html`, driven by
`Contract.workflow_steps`: Quote → Contract → Visit 1…N, filling as steps complete) shown on
`portal/contract.html`.

**Chime.** Web-Audio two-note chime (`nseChime` in `base.html`, no asset file) fires when a flash uses a
`*_chime` category — the flash block strips the suffix for colour and drops a `[data-nse-chime]` marker
that the bottom-of-`base.html` script detects on load. Used on **client quote/material-quote acceptance**
and **ops contract activation** (`success_chime`). New Ops nav links: 💰 Financials, 🩺 Health Reports.

The quotation PDF (`pdf/quotation.html`) already matches NSE's real QUO format (Wave 1) — left unchanged.

## Post-Wave-6 bug-fixes (patch round)

**Waiver text.** `WAIVER_TEXT` in `utils.py` updated to exact client-approved wording: *"I do not wish to replace the mentioned spare parts/equipments/tools as mentioned by the company and hereby, I take full responsibility if any fire incident happens after this AMC visit, and Northern Star Engineering is not responsible."*

**Future-visit lock.** The admin visit page (`admin/visit.html`) now checks `is_future` (passed from `admin.visit` GET as `v.scheduled_date > date.today()`). When true: the main edit form, material-quotation picker, and checklist are hidden and replaced with a lock banner; only the scheduled-date reschedule and technician assignment remain editable. Prevents any backdating or pre-filling before the visit day arrives.

**Material-quotation PDF.** `Quotation` and `QuotationItem` gained PDF-compatible properties (`items_by_category`, `customer_name/phone/email/address`, `project_name`, `valid_days=30`, `gst_percent=18`, `subtotal`, `gst_amount`, `grand_total`; item aliases `rate`, `unit`, `total`) so the shared `pdf/quotation.html` template renders both `ServiceQuotation` and visit-linked `Quotation` without branching. `generate_material_quotation_pdf(q)` added to `pdf_generator.py`; `portal.quotation_pdf` (`/portal/quotation/<id>/pdf`) serves the client-side download (ownership-checked). A **Download PDF** button appears in `portal/quotation.html`. Also removed `letter-spacing: .03em/.04em` from `pdf/quotation.html` (xhtml2pdf rejects em units).

**Inventory from CSV.** `import_inventory.py` (project root) reads `~/Downloads/Invetory List.csv` and bulk-replaces all `InventoryItem` rows. Run: `.venv/bin/python import_inventory.py`. Imports 1,273 real NSE product codes (Code → hsn, Name → name, Group → category, Unit → unit, Amount → rate). The visit-linked quote picker and the **Service Quotation new form** (`sq_new.html` + `sq.new_quotation`) both now use this inventory via datalist + JS auto-fill (`sqFill`/`invFill` respectively).

**Notification deep links + congratulations chime.** Visit-completion `notify()` call now appends `?chime=1` to the portal visit URL. `base.html` JS extended: `DOMContentLoaded` plays `nseChime()` when `URLSearchParams.get('chime') === '1'` (in addition to the existing flash-based trigger). So clicking the 🎉 congratulations bell notification navigates to the visit page **and** plays the chime automatically.

## UX restructure (post-Wave-6 round 2)

**Contract number format.** `Contract.reference` now returns `NSE-AMC-{id:04d}` (was `AMC-{id:05d}`). No DB column — purely computed, so all existing contracts auto-display the new format.

**Ops Console restructure.** Dashboard (`admin/dashboard.html`) simplified: long tab-bar removed, replaced with four **Service Module cards** (AMC Contracts → `/ops/amc`, Emergency & NOC, Refill Orders, Enquiries). Upcoming visits section moved out of the dashboard.

**AMC Module hub** (`/ops/amc`, `admin.amc_module`, `admin/amc_module.html`): landing page for the AMC Contracts module with inner sub-nav tabs (Contracts, Quotations, Calendar, Analytics, Financials, Health Reports, Performance), AMC KPI strip, upcoming visits (relocated from dashboard), and pending applications.

**Client portfolio page** (`/ops/client/<user_id>`, `admin.client_portfolio`, `admin/client_portfolio.html`): per-customer aggregate view with four tabs — Contracts, Visits, Quotations, Payments. Calendar links (grid + list view) navigate to client portfolio when `contract.customer_id` exists; fall back to the contract page otherwise.

**Roadmap spacing fix.** `workflow_track` macro in `_macros.html`: step containers changed to `flex-1 min-w-[92px] px-2` with `w-full` on labels — prevents "Quote confirmedContract started" text collision.

**Service report PDF** (`pdf/service_report.html`): header redesigned as table layout (logo left, wordmark + tagline + contact right), single-page margins tightened, material-quote clause appended (shows approved/rejected decision per visit quotation). Renamed from "Branded PDF" to "Service Report" in all customer-facing copy.

**Download restrictions.** Clients can no longer download service quotation PDFs or material-quotation PDFs — the Download PDF buttons removed from `portal/sq_detail.html` and `portal/quotation.html`. Clients can still download Visit Service Reports and FSHCR reports.

**In-app report viewing.** `portal.service_report_pdf` and `portal.health_report_pdf` accept `?view=1` → served inline (`as_attachment=False`) so clients can preview in-browser without downloading. Both visit page and contract page now show a 👁 **View** button next to ⬇ Download.

**AMC Agreement (click-through T&C).** Clauses + version live in `utils.py` (`AMC_AGREEMENT_CLAUSES` list of (title, body) tuples, `AMC_AGREEMENT_VERSION`). `Contract` gained `agreement_accepted` / `agreement_accepted_at` / `agreement_version` columns (existing DBs: `ALTER TABLE contracts ADD COLUMN ...` — already applied to `nse_amc.db`; fresh installs get them from `db.create_all`). Routes: `portal.agreement` (view, `/portal/contract/<id>/agreement`) + `portal.agreement_accept` (POST). Template `portal/agreement.html` — scrollable clauses, JS gates the checkbox until the client scrolls to the bottom, then enables the **Agree & Continue** button. View-only (no download). The portal contract page shows an amber "accept your AMC agreement" banner when `c.status=='active'` and not yet accepted, green confirmation once accepted. Acceptance logs an `agreement_accepted` journey event, notifies staff, fires a `success_chime`. **To change clauses, edit `AMC_AGREEMENT_CLAUSES` and bump `AMC_AGREEMENT_VERSION`.**

**Personalised hero card.** `public.home` now computes `hero_contract` / `hero_hide` / `hero` (dict). Logged-in customer **with** a contract → the home hero dashboard card (`public/home.html`) renders their real data (site, AMC status, compliance %, visits done/total, next visit, equipment count, last report, open requests) with deep links into the portal; logged-in customer with **no** contract → card hidden (`hero_hide`); anonymous visitors / staff → the original marketing demo card. The compliance ring + progress bar JS reads `data-pct` / `data-fill` attributes (falls back to the demo's 98%/75%).

## Professional UI overhaul + PDF fixes (post-Wave-6 round 3)

**No emojis policy.** All emoji characters removed from every template (tabs, buttons, headings, module cards, status banners). Navigation uses SVG icons; tabs use underline-border text style. Keep this convention — do not re-introduce emoji in UI copy.

**Ops dashboard redesign.** Module cards changed from tall emoji-icon tiles to compact horizontal rows (border icon + text). Sub-navigation in `admin/amc_module.html` and `admin/client_portfolio.html` uses clean underline tabs with no emoji labels.

**FSHCR PDF checkbox fix.** `pdf/health_report.html` replaced `☑`/`☐` Unicode characters (not renderable by xhtml2pdf/ReportLab) with inline CSS pill indicators: navy fill = Yes, red fill = No, grey fill = N/A. Filled answers now visibly differ from blank ones in the downloaded PDF.

**PDF header standardised.** Both `pdf/quotation.html` and `pdf/health_report.html` now use the same horizontal table layout as `pdf/service_report.html`: logo on the left (60–70 px), company name + tagline + contact on the right, navy address bar spanning full width below. Do not revert to the old centred stacked layout.

**Visit completion overlay.** `portal/visit.html`: the duplicate "Service report" card (with a second set of View/Download buttons) was removed — the congratulations banner at the top is the single entry point. Added a JS-driven celebration overlay (`#celebrate-overlay`) that smoothly scales in on the first view of a completed (not-yet-rated) visit and is dismissed on button click. Uses `sessionStorage` so it only shows once per browser session per visit.

**Database reset.** `nse_amc.db` was wiped and re-seeded (June 2026). All test data cleared; seed state: 4 AMC plans, admin + technician staff, one demo customer (`9876543210`) with contract `NSE-AMC-0001`.

## Post-round-3 feature batch

**Feedback visible on Ops Console visit page.** `admin/visit.html` now shows a "Client Feedback" card after the photos section — 5-dimension star ratings with average + Excellent/Good/Needs improvement badge. Shows a "no feedback yet" placeholder for completed-but-unrated visits.

**FSHCR PDF fix (definitive).** `pdf/health_report.html` `yn()` macro rewritten: (1) `answers` is now passed **explicitly** as a parameter `yn(key, answers)` so Jinja2 macro scope is guaranteed; (2) uses CSS classes (`yn-yes`/`yn-no`/`yn-na`/`yn-off`) defined at `<style>` level with `background-color` (not `background` shorthand which xhtml2pdf ignores); (3) each indicator is a `<td>` in a 1-row mini `<table>` — block-level elements render reliably in xhtml2pdf, inline `<span>` elements do not. Header changed from CSS `display:table` divs to a real `<table>` element.

**Quotation PDF letterhead fix.** `pdf/quotation.html` header now uses a real `<table>` with `<td>` cells (logo left, wordmark right) instead of CSS `display:table-cell` divs. xhtml2pdf/ReportLab renders HTML `<table>` layout reliably; CSS display-table is not fully supported.

**Agreement status in Ops.** `admin/contract.html` shows an "Maintenance Agreement" section at the bottom of active contracts — green "Accepted" or amber "Pending". `admin/client_portfolio.html` Contracts tab row shows "Agreement accepted / pending" inline. A Referrals tab is also added listing all referrals per contract.

**Visit-linked material quote: re-open after rejection.** `Quotation` model gained `negotiation_note TEXT` column. `portal/quotation.html` shows a "Changed your mind?" section for rejected quotes — client writes a message and posts to `portal.quotation_reopen` which resets status back to `pending`, stores the note, and notifies staff. The rejected-quote view also shows payment status clearly when approved.

**Material quotes visible on portal visit page.** `portal/visit.html` shows a "Material Quotations" section listing all quotes linked to that visit with status, total, and payment status.

**Customer profile fields.** `User` model gained `company_name VARCHAR(200)`, `gst_number VARCHAR(50)`, `photo_path VARCHAR(255)`. Route `portal.profile` (`/portal/profile`, GET+POST) renders `portal/profile.html` — editable name, area, city, address, company name, GST number, and optional photo/ID card upload. "My Profile" link added to portal dashboard nav. Ops client portfolio header shows company name + GST number when set, and renders profile photo in the avatar.

**Referral system.** New `Referral` model (`nse/models.py`) — links `contract_id` + `submitted_by_id` to `referee_name`, `referee_phone`, `referee_company`, `referee_area`, `notes`, `status` (new/contacted/converted). `portal.submit_referral` (`POST /portal/contract/<id>/refer`) creates the row, logs a journey event, notifies staff. `portal/contract.html` has a permanent referral form on active contracts, plus a read-only list of submitted referrals. `admin/contract.html` and `admin/client_portfolio.html` (Referrals tab) show them to staff. **Referral prompt modal**: after `portal.visit_approve` saves a rating, if the customer has ≥ 2 visits rated ≥ 4/5 AND no existing referral for that contract, the redirect appends `?prompt_referral=1`; `portal/visit.html` renders a full-screen animated referral modal on that param.

**New DB columns (existing installs — run migration):**
```
ALTER TABLE users ADD COLUMN company_name VARCHAR(200);
ALTER TABLE users ADD COLUMN gst_number VARCHAR(50);
ALTER TABLE users ADD COLUMN photo_path VARCHAR(255);
ALTER TABLE quotations ADD COLUMN negotiation_note TEXT;
```
New table `referrals` created automatically by `db.create_all()` on next startup.

## Wave 7 features (quotation from backend, AMC certificate, custom plan)

**AMC Quotation from Ops backend.** Staff can now create a Service Quotation for a prospective AMC client directly from the AMC module hub — "New AMC Quotation" button links to `/ops/sq/new` with AMC pre-selected. The form accepts client name, phone, email, and line items. The quotation is sent via email and also auto-linked to the client's portal account (lookup by phone first, then email — `sq.customer_id` is set if a matching User exists). Previously the SQ creation only linked by email; now phone (the primary login credential) takes precedence. Quotes created before a customer registers still surface after login via the `or_(customer_id, customer_phone)` portal query.

**AMC Fire Safety Certificate.** A landscape, framed PDF certificate (`pdf/amc_certificate.html`, generated by `generate_amc_certificate_pdf()` in `pdf_generator.py`) is issued automatically when a contract's 4th completed visit is recorded. Logic: `_maybe_issue_certificate(visit)` (in `admin.py`, called from `admin.visit` POST alongside `_trigger_feedback`) checks `visit.visit_number >= 4` and `completed_visits >= 4`, then sets `Contract.certificate_issued / certificate_issued_at` (new Boolean/DateTime columns). Client is notified with a chime + `?celebrate=1` deep-link to `portal.amc_certificate` (`/portal/contract/<id>/certificate`). The portal contract page shows a navy/gold certificate card when issued (with View/Download), or a "coming after your 4th visit" placeholder for active contracts. A full-screen celebration overlay appears on `?celebrate=1`. **New DB columns (existing installs):**
```
ALTER TABLE contracts ADD COLUMN certificate_issued BOOLEAN DEFAULT 0;
ALTER TABLE contracts ADD COLUMN certificate_issued_at DATETIME;
```

**Custom / Tailored AMC Plan.** A 5th "Get a Customised Plan" card on `public/plans.html` (amber dashed border, edit-pen icon) links to `/custom-plan` (`public.custom_plan`). The form (`public/custom_plan.html`) collects: contact name + phone + email, property type (dropdown), floors, area sq ft, extinguishers on site, services required (8 checkboxes), and additional notes. Submission saves to the `Enquiry` model with `subject="Custom AMC Plan Enquiry"` and a formatted multi-line `message`. The ops console Enquiries section shows it like any other enquiry.

## Wave 7 patch (journey emojis, inventory import, quotation re-send, hero card)

**No emojis in customer journey.** `CustomerJourneyEvent.EVENT_ICONS` changed from emoji characters to named string keys (`"doc"`, `"send"`, `"eye"`, `"chat"`, `"check"`, `"x"`, `"shield"`, `"sign"`, `"calendar"`, `"coin"`, `"star"`, `"users"`, `"dot"`). `portal/journey.html` fully rewritten with a `journey_icon(key)` Jinja2 macro that renders the correct SVG for each key. Empty state also uses SVG instead of emoji. Added new event types: `agreement_accepted`, `visit_rescheduled`, `referral_submitted`.

**Inventory CSV imported.** Running `.venv/bin/python import_inventory.py` loaded 1,273 NSE product codes from `~/Downloads/Invetory List.csv` into the `inventory_items` table. The datalist (`<datalist id="sq-inv-list">` in `admin/sq_new.html` and `<datalist id="inv-list">` in `admin/visit.html`) now exposes all 1,273 items — type in the Description field to search, category + rate auto-fill via `sqFill()`/`invFill()` JS functions.

**Material quotation re-send/revise from Ops Console.** When a client re-opens a rejected material quotation (`portal.quotation_reopen`), the staff notification now carries a deep link to the admin visit page (already set). On that page, for `pending` quotes with a `negotiation_note`, a new amber action panel shows the client's message plus two options: (1) **Re-send original** — POST to `admin.quotation_resend` with no `revise` flag; (2) **Revise items & re-send** — inline `<details>` form with editable item desc/qty/rate fields, POST to same route with `revise=1`. The `admin.quotation_resend` route updates items if `revise=1`, clears `negotiation_note`, and notifies the client.

**Hero card phone-fallback.** `public.home` now runs a second lookup when no contract is found by `customer_id`: `Contract.query.filter_by(applicant_phone=current_user.phone, customer_id=None)`. This surfaces pending contracts applied before the customer registered. `open_reqs` count guards against `None` customer_id. Result: any logged-in customer with a contract (active or pending, linked or phone-matched) sees their real data in the hero card; new customers with no contract see the card hidden (`hero_hide=True`); anonymous visitors still see the demo marketing card.

**Video background slot.** The hero already has `<video id="heroBgVid">` loading `nse/static/video/hero.mp4` (autoplay, muted, loop, 42% opacity fade-in on `canplaythrough`). To swap in the maintenance department video: download the MP4 from Google Drive and place it at `nse/static/video/hero.mp4` — no code change needed. For a clip trimmed to 20–25 s, use `ffmpeg -i original.mp4 -ss 0 -t 25 -c copy nse/static/video/hero.mp4` (ffmpeg is not installed on this machine; do the trim on another device or use an online tool before dropping the file).

## Wave 8 features (no-login quotation link, hassle-free payment, reminders, WhatsApp share)

Built for **non-tech-savvy / elderly customers** (society presidents, 60-80 yr olds) who will
**not** download an app or log in. The engineer creates a quotation (office or on-site tablet) and
**WhatsApps a link**; the client opens it with **zero login/OTP/app**, sees a big-button page, and pays.

**New `ServiceQuotation` columns** (`nse/models.py`; existing DBs migrated in place — idempotent
`ALTER TABLE` via `PRAGMA table_info`, already applied to `nse_amc.db`; fresh installs get them from
`db.create_all`):
```
ALTER TABLE service_quotations ADD COLUMN public_token VARCHAR(40);
ALTER TABLE service_quotations ADD COLUMN payment_status VARCHAR(20) DEFAULT 'pending';
ALTER TABLE service_quotations ADD COLUMN payment_method VARCHAR(20);     -- upi/cash/cheque
ALTER TABLE service_quotations ADD COLUMN payment_reference VARCHAR(120);
ALTER TABLE service_quotations ADD COLUMN payment_marked_at DATETIME;
```
Helpers on `ServiceQuotation`: `ensure_token()` (idempotent `secrets.token_urlsafe(12)`), `is_paid`,
`payment_method_label`.

**No-login public quotation link** (`public.py`, all under `/q/<token>`, **no `@login_required`**):
- `public.public_quote` (`/q/<token>`) — senior-friendly standalone page (`public/quote_public.html`,
  self-contained HTML — **not** extending `base.html**, big fonts, 3 big buttons). Marks the quote viewed.
- `public.public_quote_pay` (`POST /q/<token>/pay`, `method=upi|cash|cheque`) — choosing a method
  **accepts** the quote, **auto-creates the customer account** (`_ensure_customer_account` — links by
  phone then email, creates a `customer` User keyed on the mobile number if none), logs a
  `quote_accepted` journey event, and `notify_staff`. UPI → redirect to the QR page; cash/cheque →
  thank-you page.
- `public.public_quote_upi` (`/q/<token>/upi`) — `public/quote_upi.html`: server-rendered **UPI QR**
  (base64 PNG via `utils.upi_qr_data_uri`, builds an `upi://pay?pa=…&am=…` deep link) + "I have paid"
  button. If `COMPANY_UPI_ID` is unset, shows a "UPI being set up" notice instead.
- `public.public_quote_paid_claim` (`POST /q/<token>/paid-claim`) — client taps "I have paid"; we do
  **not** auto-confirm (no gateway yet) — sets a `payment_reference` note + `notify_staff` to verify.
- `public.public_quote_thanks` (`/q/<token>/thanks?m=…`) — `public/quote_thanks.html`: friendly
  confirmation + "your account is ready, log in with your number anytime" (links to `auth.login`).

**Payment = manual now, gateway-ready.** Per the user's choice, there is **no payment gateway** — UPI
shows a QR and staff confirms receipt by hand. The flow/columns are structured so a Razorpay-style
webhook can later flip `payment_status='paid'` automatically. **Staff confirmation**: `sq.mark_paid`
(`POST /ops/sq/<id>/mark-paid`) sets `payment_status='paid'` + method + reference + `payment_marked_at`,
cascades to a linked `Contract.payment_status='paid'` (+`payment_date`), logs `payment_received`, and
notifies the customer. The `admin/sq_detail.html` page gained a **Share** card (copy-link + WhatsApp +
QR + preview) and a **Payment** panel (status badge + mark-paid form).

**WhatsApp click-to-send (free, no API).** `utils.whatsapp_url(phone, text)` builds a `wa.me` link with
a pre-filled message (the engineer taps send). `utils.normalise_phone` prepends `91` to 10-digit numbers.
`sq.detail_quotation` ensures the token, builds `share_link` (`_external=True`) + `whatsapp_share`, and
passes them to the template. **API-ready**: when a WhatsApp Business API provider (AiSensy/Interakt/
Twilio) is added later, reuse the same message-builders for automated sends. The public quote page's
"WhatsApp us" / "Call us" buttons use `COMPANY_PHONE`.

**Quotation share QR (post-Wave-11 patch).** `utils.qr_data_uri(data, fill="#16235b", box_size=8)` is a
generic base64-PNG QR helper (any string in, `data:image/png;base64,...` out, `None` on failure) —
`upi_qr_data_uri` now delegates to it, and `sq.detail_quotation` calls it on `share_link` to pass a
`share_qr` thumbnail into `admin/sq_detail.html`'s Share card. A dedicated route
**`sq.quotation_qr`** (`/ops/sq/<id>/qr`, template `admin/sq_qr.html`) renders a large, print-friendly
QR page (`window.print()` button, `@media print` hides the sidebar/header) — meant for handing a tablet
to a client on-site or printing and leaving it at the property. This was the one piece missing from the
Wave 8 no-login quotation-share feature (copy-link + WhatsApp existed; QR did not) — added on user
request when the feature was hard to discover.

**Payment reminders** (`nse/reminders.py`): `payment_reminders(customer_id=None)` returns dicts for
(1) AMC fee unpaid after **≥2 completed visits**, (2) visit-linked material quotes `approved` & unpaid,
(3) `ServiceQuotation` `accepted` & unpaid (de-duped against rule 1). Surfaced in: the Ops Console
**`/ops/reminders`** page (`admin.reminders`, `admin/reminders.html`, severity-sorted priority cards) +
a **Reminders tab** with a red count badge on `admin/amc_module.html` (driven by `payment_reminder_count`
injected for staff in `inject_globals`, wrapped in try/except), and the **customer profile**
(`portal.profile` passes `my_reminders`; `portal/profile.html` shows an amber "Payments due" card).

**Config**: `COMPANY_UPI_ID` / `COMPANY_UPI_NAME` (`config.py` + `inject_globals` exposes `COMPANY_UPI_ID`
+ `.env.example`). Set `COMPANY_UPI_ID` to the firm's real UPI handle to switch the UPI QR live.
**Deps**: `qrcode==8.2` + `Pillow==11.3.0` added to `requirements.txt` (were installed in `.venv` but
unpinned; the UPI QR and the `/qr` page both need them).

**`BASE_URL` config + `public_url()` helper** — shareable quotation links (WhatsApp, email) must be
clickable on a client's phone, so they cannot use `127.0.0.1`. `config.py` exposes `BASE_URL`
(`os.getenv("BASE_URL", "")`, also in `.env.example`). `utils.public_url(endpoint, **values)` builds
the external link with this priority: (1) `BASE_URL` env var (set to ngrok / Vercel domain in
production), (2) auto-detected LAN IP via `socket` (same-WiFi dev use-case — detects the Mac's
outbound IP automatically), (3) Flask `url_for(_external=True)` fallback. `sq.detail_quotation` calls
`public_url("public.public_quote", token=...)` instead of `url_for(..., _external=True)` so the
WhatsApp share link is always a reachable address. `create_app()` sets `SERVER_PORT` in app config
(from `PORT` env, default 5055) so `public_url` can construct the correct port in the LAN-IP path.
To add a new shareable link, call `public_url(endpoint, **values)` — never `url_for(_external=True)`
for customer-facing links.

**Design note**: the three `public/quote_*.html` templates are **deliberately self-contained** (own
`<html>`, inline CSS, ~19px base font, ≥56px tap targets, one task per screen) — they do **not** extend
`base.html`, so the senior-facing flow has no nav/footer/dark-mode clutter. They still receive the
`COMPANY_*` globals from `inject_globals` and use the `rupees` filter.

## Wave 9 features (Site System Checking List — floor-by-floor survey → quotation)

Digitises NSE's printed **"system checking list"** (`~/Downloads/system checking list.xlsx`): a
floor-by-floor inventory matrix an engineer fills on site (tablet) BEFORE raising a quote. The sheet is
a grid of **equipment items (rows) × floors (columns)** holding the quantity of each component per
floor, plus a separate **pump-room details** table.

**New model `SystemCheckList`** (`nse/models.py`, new table — `db.create_all` picks it up, no reset):
header fields (`site_name`, `site_address`, `client_name/phone/email`, `survey_date`, `surveyed_by_id`,
`status` draft/completed, optional `contract_id` + `service_quotation_id`, `general_remarks`) and a
single **`data` JSON column** holding everything else:
`{floors:[...], matrix:{item:{floor:qty}}, custom_items:[...], pumps:{pump:{col:val}}, item_remarks:{item:text}}`.
Class constants mirror the sheet **with additions**: `ITEMS` (the original 26 — Hydrant Valve, Hose Box,
Branch Pipe, RRL, Hose Reel, … Tanks — plus Heat Detector, Foam/Clean-Agent/Water-CO2 F.E, Landing Valve,
Emergency Light, Exit Signage, Fire Door, Fire Damper), `FLOORS` (Basement 3 → 24th Floor → Terrace, 29),
`PUMPS` (Hydrant/Sprinkler/Jockey 1/Jockey 2/Diesel Engine/Booster/Fire Electrical Panel) and
`PUMP_COLUMNS` (Qty/HP/LPM/Make/Condition/Area). Helpers: `reference` (`SCL-0001`), `get_data`/`set_data`,
`active_floors`, `all_items` (standard + custom), `matrix`, `pumps_data`, `item_remarks`, and
`item_totals` (per-item count across floors, skips tank litres — used to seed a quotation).

**Routes** (`admin.py`, all `@staff_required`): `admin.surveys` (`/ops/surveys` list),
`admin.survey` (`/ops/survey/new` + `/ops/survey/<id>`, one GET+POST view), `admin.survey_to_quote`
(`/ops/survey/<id>/quote`). Templates: `admin/surveys.html` (list) + `admin/survey_form.html`.

**Form UX** (`admin/survey_form.html`): tablet-friendly. Engineer ticks **which floors exist** → JS
renders a matrix showing **only those floor columns** (items as rows, a numeric cell per floor, a Remarks
column per item). `+ Add custom item` appends free rows. A static **pump-room table** below
(PUMPS × PUMP_COLUMNS). Everything serialises to one hidden `data_json` on submit (the matrix is
re-filtered to checked floors so unchecking a floor drops its values); a JS `MAT/REM/CUSTOM` cache
preserves entries across floor toggles. Server parses `data_json` → `set_data`. The model constants +
existing `data` are embedded as `window.SCL` for edit repopulation.

**Survey → quotation handoff:** After saving a survey, a "Create quotation from this survey" card
appears (`{% if s %}`). Clicking it calls `admin.survey_to_quote` which redirects to `sq.new_quotation`
with client details pre-filled via URL params (`prefill_name/phone/email/project/address`) — **no items
are auto-created**. The engineer fills items and rates manually from the inventory dropdown. A hidden
`from_survey_id` field links the new quotation back to the survey on save
(`survey.service_quotation_id`). This replaces the previous auto-populate approach which caused
mistakes. A **"Site Surveys" tab** was added to the AMC module sub-nav.

**Quotation re-editing** (`sq.edit_quotation`, `/ops/sq/<id>/edit`, template `admin/sq_edit.html`):
quotes are **always editable by staff** regardless of status (removed the `is_editable` draft-only
guard). The edit page pre-populates all existing items and client details. Every save logs a
`CustomerJourneyEvent` (event_type `quote_sent`) recording who revised it and the old→new total
change (e.g. "₹12,000 → ₹14,000"). The detail page (`admin/sq_detail.html`) always shows an
**Edit / Revise** button plus a **Re-send** button for sent/viewed/accepted quotes. The activity log
on the detail page shows the full revision history.

Note: this is distinct from the **FSHCR** `HealthCheckReport` (Wave 6), which is a Yes/No *condition*
checkup; the SystemCheckList is a *quantity inventory* survey. Both can stand alone or link to a contract.

## Wave 10 features (complaints, visit reminders, days-left, technician day plan)

**Support Tickets / Complaints** (`SupportTicket` + `TicketAttachment` models, new tables — `db.create_all` picks them up). Customer raises complaint from the portal contract page (modal) or from `/portal/tickets`. Fields: title, description, voice note, photos/videos, priority, linked contract/visit. Status flow: `open → acknowledged → resolved → closed`. If no staff reply in 24h, `is_overdue=True` and `can_retrigger=True` — client sees "Send reminder" button (`portal.retrigger_ticket`). Staff console: `/ops/tickets` (list with filter chips) and `/ops/ticket/<id>` (view + acknowledge + resolve + close). Acknowledging/resolving sends in-app notification to client. Open ticket count injected globally for staff (`open_ticket_count`) and shown as red badge on the "Complaints" tab in the AMC module sub-nav.

Portal: `portal.tickets` (list), `portal.raise_ticket` (POST modal), `portal.ticket_detail`, `portal.retrigger_ticket`. Admin: `admin.tickets`, `admin.ticket_detail`. "Raise a complaint" card + modal permanently visible on active/pending contract pages. "My Complaints" link added to portal mobile nav. Attachments saved via `save_upload("tickets")`.

**Visit reminders (1 month / 1 week / 1 day before)** — `VisitReminderLog` model (new table) tracks which (visit_id, reminder_type, sent_to) triples have been notified. `process_visit_reminders()` in `reminders.py` is called from `inject_globals` (idempotent — the unique constraint on `visit_reminder_logs` prevents duplicates). Fires in-app notifications to both the customer (portal contract page link) and the assigned technician (visit page link) when a visit is 30/7/1 days away. No SMS/email yet (in-app only, visible in the notification bell).

**Days-left countdown** — `Visit.days_until` property (returns int or None). Used in `portal/contract.html` as a colour-coded pill per visit row: red (0/overdue) → amber (≤7 days) → blue (>7 days). Also used in `admin/_day_plan_card.html` for the technician's day plan.

**Technician day plan** (`/ops/my-plan`, `admin.day_plan`): shows the logged-in technician's visits for today, tomorrow, and the next 7 days. Each visit card has an **Accept** button (marks `Visit.technician_confirmed=True`) and a **Flag conflict / reject** collapsible with a reason field (`Visit.technician_confirmed=False`, `technician_note`). Accept/reject both notify all staff via `notify_staff`. New columns on `Visit`: `technician_confirmed BOOLEAN` (nullable — None=pending), `technician_note TEXT`. Template: `admin/day_plan.html` + reusable partial `admin/_day_plan_card.html`. Route: `admin.confirm_visit` (POST `/ops/visit/<id>/confirm`). "Day Plan" tab added to AMC module sub-nav.

**Surveys list — quote status column** — `admin/surveys.html` now shows a "Quotation" column: "View quote" (blue, links to detail) if linked, "Create quote" (amber, links to `survey_to_quote`) if survey is completed but no quote made, "—" otherwise.

**New DB columns (existing installs):**
```
ALTER TABLE visits ADD COLUMN technician_confirmed BOOLEAN;
ALTER TABLE visits ADD COLUMN technician_note TEXT;
```
New tables `support_tickets`, `ticket_attachments`, `visit_reminder_logs` created automatically by `db.create_all()` on next startup.

## Wave 11 features (renewal, field ops, BI, comms, payments — "AMC module" expansion)

A large batch of AMC-module features. **Migration:** new COLUMNS on existing tables are added by the
idempotent **`migrate_wave11.py`** (root; `PRAGMA table_info`-guarded — already run against `nse_amc.db`);
new TABLES self-create via `db.create_all()`. Columns added: `contracts.renewed_from_id`;
`visits.checkin_at/checkout_at/checkin_note`; `amc_plans.sla_hours`;
`service_requests.sla_due_at/first_response_at`; `service_quotations.gateway_order_id/gateway_payment_id`.
New tables: `visit_defects`, `broadcasts`, `installments`, `milestone_logs`.
**Note: "mobile app" = the same responsive Flask templates — there is no separate native codebase.**
**Feature 16 (Compliance Document Vault) and the whole Inventory & Certificates set were intentionally
excluded** per the user.

**Model helpers (`nse/models.py`):**
- `Contract`: `renewed_from`/`renewals` self-relationship; `days_to_expiry`, `is_renewed`,
  `renewal_window` (`"90"`/`"60"`/`"30"`/`"expired"`/None — active & not-yet-renewed only),
  `anniversary_years`, `safety_score` (0-100 weighting visit compliance/equipment/defects/certificate/
  agreement), `safety_grade` → `(letter, color)`, `open_defects`, `installment_plan`.
- `User.health_score` (0-100 retention score: payments/visit-reliability/feedback/agreement/referrals) +
  `health_band` → `(label, color)`.
- `Visit`: `is_checked_in`, `onsite_minutes`, `onsite_duration_label`, `open_defects`.
- `ServiceRequest`: `sla_status` (`met`/`breached`/`at_risk`/`pending`/`none`) + `sla_label`.
- New models: `VisitDefect` (severity low/medium/high, status open→acknowledged→quoted→resolved,
  `reference` `DEF-0001`), `Broadcast` (saved WhatsApp broadcast + audience filter), `Installment`
  (EMI schedule, `is_overdue`/`days_to_due`), `MilestoneLog` (renewal/anniversary dedup, unique
  `(contract_id, milestone_type)`).

**Renewal pipeline & clone** (`admin.py`): `admin.renewals` (`/ops/renewals`, `admin/renewals.html`) lists
contracts in the 90/60/30/expired bands (from `reminders.renewal_reminders()`). `admin.renew_contract`
(`POST /ops/contract/<id>/renew`) calls helper `_clone_contract_for_renewal(src)` — copies site/plan/
equipment/contact into a new **pending** contract (`renewed_from_id` set) and auto-creates an AMC
`ServiceQuotation` (like the apply flow). Portal: `portal.request_renewal`
(`POST /portal/contract/<id>/renew`) lets the customer ask; a renewal banner shows on `portal/contract.html`.
`reminders.process_milestones()` (idempotent, called from `inject_globals`) fires renewal-band + service-
anniversary notifications de-duped via `MilestoneLog`. `renewal_reminder_count()` drives the red badge on the
sidebar **Renewals** link + AMC-module tab.

**Mid-contract upgrade** (`portal.upgrade_plan`, `/portal/contract/<id>/upgrade`, `portal/upgrade.html`):
customer picks a higher plan → auto-creates a `ServiceQuotation` with a single **prorated-difference**
line item (scaled by remaining contract days), notifies staff.

**Field ops** (`admin/visit.html`): check-in/out — `admin.visit_checkin` / `admin.visit_checkout`
(POST) stamp `checkin_at`/`checkout_at`; a status card sits under the visit `<h1>`. Photo-based **defect
reports** — `admin.visit_defect_add` (POST, photo via `save_upload("defects/…")`) logs a `VisitDefect`
and notifies the client; `admin.defect_status` (POST) changes status; the customer acknowledges via
`portal.acknowledge_defect` (`POST /portal/defect/<id>/acknowledge`). Defects surface on `admin/visit.html`,
`admin/contract.html` (open-defects card), and `portal/contract.html` (acknowledge buttons). **SLA**:
emergency `ServiceRequest`s get `sla_due_at = now + 4h` at creation (`public.emergency`); the admin request
route stamps `first_response_at` on first staff action; a colour-coded SLA badge shows on `admin/request.html`.

**Business insights** (`admin.insights`, `/ops/insights`, `admin/insights.html`): revenue forecast
(confirmed active fees vs open-quote pipeline vs renewals-at-stake), property **Fire Safety Scores**
(lowest-first, graded A–D), **Customer Health** scores (at-risk-first, banded), and an **area heat-map**
bar chart. Safety-score chips also appear on `admin/contract.html` + `portal/contract.html`.

**Bulk WhatsApp broadcast** (`admin.broadcasts` `/ops/broadcasts` composer + history;
`admin.broadcast_new` POST saves a `Broadcast`; `admin.broadcast_send` `/ops/broadcast/<id>/send` renders
one `wa.me` link per recipient — free, no API). Audiences resolved by `_broadcast_recipients(audience,
value)`: all / active / expiring / by-area / by-plan. `{name}` in the message is personalised per
recipient. Templates `admin/broadcasts.html` + `admin/broadcast_send.html`. **Loyalty/anniversary**
notifications are part of `process_milestones()` (see renewal above).

**Payments — Razorpay (gateway-ready) + EMI** (`nse/payments.py`): config-gated exactly like the AI layer
— `Config.razorpay_enabled()` is False (and the SDK import is lazy) until `RAZORPAY_KEY_ID` +
`RAZORPAY_KEY_SECRET` are set, so the manual UPI/cash flow stays the default. `create_order`,
`verify_payment_signature`, `verify_webhook_signature`, `mark_quote_paid` (flips a `ServiceQuotation` +
cascades to its contract). Routes: **no-login** `public.public_quote_pay_online` +
`…_verify` (senior flow, `public/quote_checkout.html`) and `public.razorpay_webhook` (`POST /pay/webhook`,
auto-confirms by matching `gateway_order_id`); **logged-in** `portal.service_quotation_pay_online` + `…_verify`
(`portal/checkout.html`). "Pay online" buttons appear on `public/quote_public.html` and `portal/sq_detail.html`
gated on `RAZORPAY_ENABLED` (injected by `inject_globals`). **EMI instalments**: `admin.contract_installments`
(POST) builds an N-part schedule (monthly/quarterly/half-yearly); `admin.installment_pay` (POST) toggles each
paid and marks the contract fee paid once all are settled. Shown on `admin/contract.html` (editable) +
`portal/contract.html` (read-only). Dep: `razorpay==1.4.2` in `requirements.txt`; env keys in `.env.example`.

**Nav wiring:** sidebar (`base_ops.html`) gained **Renewals** (AMC section, red badge), **Insights** +
**Broadcast** (Insights section). AMC-module sub-nav (`admin/amc_module.html`) gained Renewals / Insights /
Broadcast tabs. `inject_globals` exposes `renewal_reminder_count` + `RAZORPAY_ENABLED` and calls
`process_milestones()` alongside `process_visit_reminders()`.

## Wave 12 (post-launch-testing bug-fix batch)

**Auto-login on no-login quote acceptance.** `public._ensure_customer_account(sq)` now calls
`login_user(user, remember=True)` whenever it resolves/creates a customer account and nobody is
currently logged in — including the early-return path when `sq.customer_id` was already set (the
original bug: that branch returned before reaching the login call). So a customer who opens a `/q/<token>`
link, taps a payment method, and gets an account auto-created/linked is now **seamlessly logged into
their portal session** — no separate OTP round-trip needed to see their contract. `public/quote_thanks.html`
detects `current_user.is_authenticated` and shows "View my account" (→ `portal.dashboard`) instead of
the "log in with your mobile number" prompt.

**Renewals — tighter bands + one-click share.** `Contract.renewal_window` gained a `"7"` band (was only
`expired/30/60/90`) so contracts expiring within a week surface separately from the rest of "this month".
`admin/renewals.html` now groups the list into four labelled sections (Overdue / Due this week / Due this
month / Later) instead of one flat list. `admin.renew_contract` now redirects straight to
**`sq.detail_quotation`** (the copy-link/WhatsApp/QR share page) instead of the contract page, so raising
a renewal and sharing it with the client is a single staff action. `reminders.renewal_reminders()`,
`process_milestones()`, and the severity/order maps were all updated for the new band.

**Material-quotation rate autofill bug fixed.** `admin/visit.html`'s inventory-driven quote picker built
its JS lookup (`INV`) by hand-interpolating Jinja into a JS object literal
(`"{{ it.name|replace('"','\\"') }}": {{ it.rate }}`) — Jinja's HTML autoescaping runs *before* that
`replace`, so any item name containing a literal `"` (e.g. `G.I. "C" Class Pipe`) got turned into
`&#34;` in the JS source, silently breaking the lookup key for exactly those items (~a few dozen out of
the 1,273 imported SKUs). Fixed by porting the same safe pattern `sq_new.html` already used: the datalist
`<option>` now carries a `data-rate` attribute, and `INV` is built at runtime in JS via
`document.querySelectorAll('#inv-list option')` reading `o.dataset.rate` — the browser's own HTML
entity decoding sidesteps the Jinja-escaping problem entirely. No template-string JS object literals for
inventory data going forward — use the datalist `data-*` attribute + DOM-read pattern instead.

**Visit "locked until scheduled date" feature removed.** The `is_future` guard in `admin/visit.html`
(read-only fields + reschedule-only form for future-dated visits) is gone — technicians can now complete
a visit early (client asked to move it up, technician free ahead of schedule, etc.). In its place: (1) a
non-blocking informational banner on future-dated visits explaining early completion is fine, and (2) in
`admin.visit()`, when a visit is saved with `status=completed` and its `scheduled_date` is still in the
future, the route calls `_apply_reschedule(v, date.today())` automatically — snapping the date to today,
logging a `visit_rescheduled` journey event, and notifying the client, exactly like an explicit
reschedule. `is_future` is still computed and passed to the template (now only for that info banner) —
no other section of the visit page is gated by it any more.

**Check-in / check-out now notify the client and appear in their records.** `admin.visit_checkin` and
`admin.visit_checkout` (`nse/blueprints/admin.py`) now call `notify()` on the contract's customer — "Your
technician has arrived" on check-in, "Your technician has left the site" (with on-site duration) on
check-out, both linking to `portal.contract`. `portal/visit.html` shows an on-site status line (arrival/
departure time + duration) when `v.checkin_at` is set. `pdf/service_report.html`'s meta table gained an
"On-site time" row for the same data, so it's part of the permanent record the client can download.

**Removed from the UI (staff request — not what a fire-safety AMC customer needs to see):**
- **EMI / instalment plan** — the whole card removed from `admin/contract.html` and
  `portal/contract.html`. `Installment` model, `admin.contract_installments` / `admin.installment_pay`
  routes are untouched (harmless, just unreferenced) in case this needs reviving later.
- **Equipment & refill register** — removed from both `admin/contract.html` (add-equipment form + table)
  and `portal/contract.html` (the register table + link to `portal.equipment_detail`). Routes
  (`admin.add_equipment`, `admin.add_refill`, `portal.equipment_detail`) are untouched but now
  unreachable from the UI. Note: `Contract.safety_score`'s "equipment health" component (25 of 100 pts)
  reads `self.equipment` — with no UI to add equipment going forward it defaults to full marks (empty
  list → `else: score += 25`), so this doesn't break the score, it just stops being a differentiator for
  new contracts.
- **Photo/video attachment upload on "Raise a complaint"** — removed the `<input type="file"
  name="attachments">` block from both `portal/tickets.html` and the complaint modal on
  `portal/contract.html` (large video uploads over the phone's connection were reported hanging the page).
  Voice note stays. `portal.raise_ticket` already handled a missing `attachments` field gracefully
  (`request.files.getlist` on an absent field just returns `[]`), so no backend change was needed —
  `TicketAttachment`/`SupportTicket` and the upload-handling code are untouched for if this needs
  re-enabling (e.g. with client-side video compression) later.

**Email still needs a real mailbox password.** `send_quotation_email` (and all other
`nse/email_service.py` senders) were reported as "not working" — this isn't a code bug: `.env` has no
`MAIL_PASSWORD` and doesn't set `MAIL_SUPPRESS_SEND`, so it defaults to `true` and every send is logged
to console only (`nse/config.py` / `nse/email_service.py._send`). To actually send: add
`MAIL_PASSWORD=<the mailbox password>` and `MAIL_SUPPRESS_SEND=false` to `.env` — **but note Microsoft
has been deprecating Basic Auth SMTP for Office 365** in many tenants, so a plain password may not work
even once added; if so, either enable "Authenticated SMTP" for that mailbox in the Exchange admin center
+ generate an app password (if MFA is on), or switch to a transactional email API (SendGrid/Mailgun/SES)
instead of Office365 SMTP.

## Mobile app (JSON API + React Native / Expo)

The web app is **entirely server-rendered** (Jinja + Flask-Login **session cookies**) — it had **no
JSON API**, which is why a native mobile app could not be built against it directly. Added a thin JSON
API layer on the same Flask backend, plus an Expo React Native client. **The website is unchanged** — the
API is additive and reuses the same models/DB.

**Backend API** (`nse/blueprints/api.py`, prefix **`/api/v1`**, registered in `nse/__init__.py`):
stateless **bearer-token auth** signed with `itsdangerous` (already a dep — no new table, works on
serverless Vercel). `issue_token(user)` mints a 30-day token carrying `{uid, role}`; decorators
`token_required` / `customer_only` gate routes and expose `g.current_user`. Serializers (`contract_json`,
`visit_json`, `service_quote_json`, `material_quote_json`, `ticket_json`, `request_json`, `journey_json`,
`feedback_json`, etc.) mirror what the portal templates show. A blueprint-scoped `after_request` adds
permissive CORS (native apps ignore CORS; Expo web + browsers need it) — no `flask-cors` dep. **Dev OTP:**
`otp_request` returns `dev_code` in the JSON while no SMS gateway exists — before production, wire
`generate_otp()` (in `utils.py`) to an SMS provider and **remove `dev_code` from the response**.

The API covers the **full customer portal**. Endpoints: `GET /health`, `POST /auth/otp/request`,
`POST /auth/otp/verify`, `GET/PUT /me`, `GET /dashboard` (profile+contracts+badge counts, one call for
Home), `GET /contracts`, `GET /contracts/<id>` (full), `POST /contracts/<id>/renew` · `/refer`,
`GET /contracts/<id>/agreement` (+`/accept`), `GET /visits/<id>`, `POST /visits/<id>/rate` (writes
`VisitFeedback` + `customer_approved`), `GET /service-quotations` (+`/<id>`, `/accept`, `/negotiate`),
`GET /quotations/<id>` (+`/decide` — reject needs `waiver_accepted`, returns `WAIVER_TEXT`),
`GET /requests` (ServiceRequests + RefillOrders), `GET/POST /tickets` (+`/<id>`, `/retrigger`),
`GET /journey`, `GET /notifications` (+`/read`), `GET /plans`. Writes reuse `notify`/`notify_staff` and
log `CustomerJourneyEvent`s like the web. When adding an endpoint, reuse `portal.py`'s queries and the
`_own_*` ownership helpers — never trust a client id without checking `customer_id == g.current_user.id`.

**Mobile client** (`mobile/`): **React Native via Expo SDK 51** — the **full customer app** (one codebase,
Android + iOS). React Navigation **bottom-tabs** (Home · Quotations · Notifications · Complaints · Profile)
with detail screens on a native-stack above the tabs. `expo-secure-store` for the token, plain `fetch`
(wrapper in `src/api/client.js` — set `API_BASE` there: LAN IP / `10.0.2.2` emulator / Vercel URL).
`AuthContext` restores the token on launch + exposes `refresh()`. `theme.js` mirrors the web brand
tokens/status pills and adds `rupees()`/`fmtDate()`/`fmtDateTime()`. `src/components/ui.js` is a shared UI
kit (`Card`, `Pill`, `Btn`, `Row`, `Field`, `Loading`, `Empty`, `ErrorNote`, `SectionTitle`) — **build new
screens from these, don't re-style from scratch**. Screens: Login/Verify, Home, Contract, Visit (+5-star
rating), Quotations/QuotationDetail, MaterialQuote (approve/decline-with-waiver), Requests, Tickets/
RaiseTicket/TicketDetail, Notifications, Journey, Profile, Agreement (scroll-to-accept), Refer.
`@expo/vector-icons` (Ionicons) for all icons — **no emoji** (same policy as web). **Node/npm are only
needed on the mobile developer's machine**, not the server/build machine. Extend by copying the recipe:
JSON route in `api.py` → method in `src/api/client.js` → screen in `src/screens/` registered in `App.js`.
Before stores: EAS Build (`eas build`); bundle IDs `com.northernstar.amc` set in `app.json`. **Staff/
technician features stay on the web Ops Console** — the app is customer-facing only.

**Handover doc:** `MOBILE_APP.md` (root) — for the external developer: why there was "no mobile code,"
the tech stack, architecture diagram, run steps, and the build-out roadmap.

## Known dev-grade pieces (not yet production)

OTP is a dev flow (code shown on screen, not SMS). Payments: manual cash/UPI **intent** by default; a
**gateway-ready Razorpay** integration (Wave 11) auto-confirms when `RAZORPAY_*` keys are set (SDK +
webhook). DB is SQLite (swap to Postgres via `DATABASE_URL`). These are deliberate; harden on request.
