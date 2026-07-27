# Northern Star — Mobile App (developer handover)

This document is for the developer building the mobile app. It explains **why the
web app alone can't power a mobile app**, the **tech stack** chosen, what has
**already been built here** (a working first module), and exactly **what to do next**.

---

## 1. Why "there was no mobile code" — and what actually needs to happen

The web app (`nse/`) is a **Flask app that renders HTML pages on the server**
(Jinja templates) and logs users in with **session cookies**. That is perfect for a
website in a browser, but a native mobile app (iOS/Android) **cannot use HTML pages
or session cookies** — it needs a **JSON API** to talk to.

So a mobile app is two parts:

```
┌─────────────────────────┐        JSON over HTTPS        ┌──────────────────────────┐
│  Mobile app (this repo:  │  ───────────────────────────▶ │  Flask backend (existing) │
│  /mobile, React Native)  │   Bearer token auth           │  + new JSON API layer     │
│  runs on the phone       │  ◀─────────────────────────── │  (nse/blueprints/api.py)  │
└─────────────────────────┘                               └──────────────────────────┘
                                                                      │
                                                            same models & database
                                                            as the website (one source
                                                            of truth — no duplication)
```

The website is **not** rewritten or replaced. We **add** a JSON API to the same
Flask backend, and the mobile app calls it. Both surfaces share the same database,
models, and business logic.

**This has already been started for you.** The API layer + the first mobile module
(customer login + contracts) are built and tested in this repo — see below.

---

## 2. Tech stack (share this list with the team)

### Mobile app
| Piece | Choice | Why |
|---|---|---|
| Framework | **React Native** via **Expo (SDK 51)** | One codebase → **both iOS and Android**. Expo removes native-build pain (no Xcode/Android Studio needed to start), has the largest hiring pool in India, and supports OTA updates. |
| Language | **JavaScript** (TypeScript optional) | Fast to start; can migrate to TS later. |
| Navigation | **React Navigation** (native-stack) | The standard for RN screens. |
| Secure token storage | **expo-secure-store** | Stores the auth token in the device Keychain / Keystore, not plain storage. |
| Networking | **fetch** (thin wrapper in `src/api/client.js`) | No heavy dependency; easy to swap for axios/react-query later. |
| Push notifications (next phase) | **expo-notifications** | Reuses the existing `Notification` model. |

### Backend API (added to the existing Flask app — no new server)
| Piece | Choice | Why |
|---|---|---|
| API framework | **Flask blueprint** (`nse/blueprints/api.py`, prefix `/api/v1`) | Reuses the existing app, models, and database. Nothing new to deploy. |
| Auth | **Signed bearer tokens** via `itsdangerous` (already a dependency) | Stateless, no new table, works on serverless Vercel. Token carries the user id + role, expires in 30 days. |
| Data format | **JSON** | Standard for mobile. |
| Hosting | **Same Vercel deployment** as the website | The API is served at `https://<your-vercel-domain>/api/v1/...` automatically. |

> **No new infrastructure.** The API rides on the Flask app you already deploy to
> Vercel. No separate backend, no new database.

---

## 3. What is already built in this repo

### A. Backend JSON API — `nse/blueprints/api.py`  ✅ built & tested
Registered in `nse/__init__.py`. All endpoints are under `/api/v1`. The **full customer portal** is
now exposed as JSON (not just login + contracts).

| Method & path | Auth | Returns / does |
|---|---|---|
| `GET  /health` | none | connectivity ping |
| `POST /auth/otp/request` | none | sends OTP (dev: returns the code) |
| `POST /auth/otp/verify` | none | `{ token, user }` — the token to store |
| `GET/PUT /me` | Bearer | read / update customer profile |
| `GET  /dashboard` | Bearer | profile + contracts + badge counts (one call for Home) |
| `GET  /contracts` · `/contracts/<id>` | Bearer | contracts list · full detail (visits + roadmap) |
| `POST /contracts/<id>/renew` · `/refer` | Bearer | request renewal · submit a referral |
| `GET  /contracts/<id>/agreement` (+`/accept`) | Bearer | AMC agreement clauses · accept |
| `GET  /visits/<id>` · `POST /visits/<id>/rate` | Bearer | visit detail (checklist, quotes) · 5-star rating |
| `GET  /service-quotations` (+`/<id>`, `/accept`, `/negotiate`) | Bearer | quotations list/detail + respond |
| `GET  /quotations/<id>` (+`/decide`) | Bearer | material quote + approve / decline-with-waiver |
| `GET  /requests` | Bearer | emergency/NOC requests + refill orders |
| `GET/POST /tickets` (+`/<id>`, `/retrigger`) | Bearer | complaints list/create/detail/re-trigger |
| `GET  /journey` | Bearer | customer journey timeline |
| `GET  /notifications` (+`/read`) | Bearer | notifications + mark-all-read |
| `GET  /plans` | none | active AMC plans |

Auth model: the app sends `Authorization: Bearer <token>` on every logged-in request. The token is
minted in `issue_token()` and checked by the `@token_required` / `@customer_only` decorators. Every
route reuses the same models and helpers the web portal (`portal.py`) uses — one source of truth.

**Verified working** against the real database — the demo customer (`9876543210`) logs in and
exercises reads (dashboard, contracts, quotations, journey) and writes (raise ticket, rate visit,
update profile, accept agreement).

### B. Mobile app — `mobile/`  ✅ built (full customer app)
Bottom-tab navigation (Home · Quotations · Notifications · Complaints · Profile) with detail screens
pushed on a stack above the tabs.
```
mobile/
├── App.js                        # tab + stack navigation, auth-gated
├── package.json                  # dependencies (see stack table)
├── app.json                      # Expo config (name, colors, bundle IDs)
└── src/
    ├── api/client.js             # fetch wrapper + token storage + ALL endpoints
    ├── context/AuthContext.js    # session state (restore token, refresh)
    ├── theme.js                  # brand tokens, status pills, ₹/date formatters
    ├── components/ui.js          # shared kit: Card, Pill, Btn, Row, Field, Loading, Empty…
    └── screens/
        ├── LoginScreen · VerifyScreen           # phone → OTP → token
        ├── HomeScreen                            # dashboard: counts, quick actions, contracts
        ├── ContractScreen                        # facts, safety score, roadmap, visits, actions
        ├── VisitScreen                           # detail + checklist + material quotes + rating
        ├── QuotationsScreen · QuotationDetailScreen   # list + accept / negotiate
        ├── MaterialQuoteScreen                   # approve / decline-with-waiver
        ├── RequestsScreen                        # emergency / NOC / refills
        ├── TicketsScreen · RaiseTicketScreen · TicketDetailScreen   # complaints
        ├── NotificationsScreen · JourneyScreen   # alerts + timeline
        ├── ProfileScreen                         # view/edit + sign out
        ├── AgreementScreen · ReferScreen         # scroll-to-accept T&C · refer a friend
```

This is the **complete Customer Portal** as a native app. The staff Ops Console stays on the web
(contract activation, visit management, financials, broadcasts) — the mobile app is customer-facing,
the same split as any consumer app vs. its internal admin dashboard.

---

## 4. How to run it (developer instructions)

### Run the backend API (local)
```bash
# from the project root
.venv/bin/python run.py          # serves http://127.0.0.1:5055  (API at /api/v1)
```
Quick test:
```bash
curl -s -X POST http://127.0.0.1:5055/api/v1/auth/otp/request \
  -H "Content-Type: application/json" -d '{"phone":"9876543210"}'
```

### Run the mobile app
```bash
cd mobile
npm install          # needs Node.js (install on the DEVELOPER's machine)
npx expo start       # opens Expo — scan the QR with the Expo Go app on a phone
```
Point the app at your backend by editing **`mobile/src/api/client.js`** →
`API_BASE`:
- Physical phone on same Wi-Fi → `http://<your-mac-LAN-IP>:5055/api/v1`
- Android emulator → `http://10.0.2.2:5055/api/v1`
- Production → `https://app-amc-three.vercel.app/api/v1` (already the default)

> Node.js/npm are **not** installed on the original build machine — that is fine,
> they are only needed on the mobile developer's machine, not on the server.

---

## 5. Roadmap — what to build next (same pattern each time)

Every remaining feature follows the **same two-step recipe**:
1. **Add a JSON route** to `nse/blueprints/api.py` (copy an existing one; reuse the
   model — the web blueprints in `nse/blueprints/portal.py` show the exact queries).
2. **Add a screen** in `mobile/src/screens/` that calls it via `src/api/client.js`.

Suggested order (all models already exist in `nse/models.py`):
- **Notifications screen** + `expo-notifications` push (model: `Notification`).
- **Service requests** — emergency / NOC (model: `ServiceRequest`).
- **Refill orders** (model: `RefillOrder`).
- **Quotations** — view / accept / pay (models: `ServiceQuotation`, `Quotation`);
  reuse the existing Razorpay flow in `nse/payments.py`.
- **Visit detail + rating** (models: `Visit`, `VisitFeedback`).
- **Complaints / tickets** (model: `SupportTicket`).
- **Profile edit** (model: `User`).

### Before production — two things to wire up
1. **Real SMS OTP.** Today the OTP is returned in the API response for testing
   (`dev_code`). Plug an SMS gateway into `generate_otp()` in `nse/utils.py` and
   **remove `dev_code` from the API response** (`otp_request` in `api.py`).
2. **App store builds.** Use **EAS Build** (`eas build`) to produce the `.ipa`
   (App Store) and `.aab` (Play Store). Bundle IDs are already set in `app.json`
   (`com.northernstar.amc`).

---

## 6. Summary for the client (non-technical)

- The website is done and stays as-is.
- To get a mobile app we add a small "data doorway" (API) to the same backend, and
  build the phone app that talks to it — **one app for both iPhone and Android**.
- The **first module (login + view contracts) is already built and working**; the
  rest of the screens reuse the same recipe.
- No new servers or databases — it all runs on the existing Vercel deployment.
