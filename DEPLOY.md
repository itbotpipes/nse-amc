# Deploying to Vercel

The code is already Vercel-ready (`api/index.py`, `vercel.json`, Postgres + Blob support).
What remains needs **your accounts** — follow these steps in order.

## 1. Push to GitHub

The Vercel CLI needs Node (not installed here), so we deploy via Git integration. The repo
is already initialised and committed locally.

```bash
# create an empty repo on github.com first (no README), then:
git remote add origin https://github.com/<you>/nse-amc.git
git push -u origin main
```

## 2. Provision a Postgres database (Neon — free tier)

SQLite cannot persist on Vercel. Create a Postgres DB and copy its connection string.

1. Sign up at https://neon.tech → create a project.
2. Copy the connection string (looks like
   `postgresql://user:pass@ep-xxx.region.aws.neon.tech/dbname?sslmode=require`).
   Use the **pooled** ("-pooler") host if Neon offers one — better for serverless.

## 3. Create a Vercel Blob store (for uploaded photos/reports)

1. In the Vercel dashboard → **Storage** → **Create** → **Blob**.
2. Open the store → **Tokens** → copy the `BLOB_READ_WRITE_TOKEN`.

## 4. Import the project into Vercel

1. https://vercel.com → **Add New… → Project** → import your GitHub repo.
2. Framework preset: **Other** (the `vercel.json` already configures the Python build).
3. Before deploying, add **Environment Variables** (Settings → Environment Variables):

   | Key | Value |
   |-----|-------|
   | `SECRET_KEY` | a long random string (`python -c "import secrets;print(secrets.token_hex(32))"`) |
   | `DATABASE_URL` | the Neon connection string from step 2 |
   | `BLOB_READ_WRITE_TOKEN` | the token from step 3 |
   | `OPENROUTER_API_KEY` | your OpenRouter key |
   | `OPENROUTER_MODEL` | `anthropic/claude-haiku-4.5` |
   | `EMERGENCY_HOTLINE` | `1800-891-8565` |
   | `COMPANY_PHONE` | `9687266625` |
   | `COMPANY_EMAIL` | `info@northernstarengineering.com` |
   | `COMPANY_ADDRESS` | `521-522, Western Business Park, opp. S.D. Jain School, Surat, Gujarat 395007` |

4. Click **Deploy**. Tables auto-create on first cold start (`db.create_all()`).

## 5. Seed the database (once)

Run locally against the **remote** DB so the demo plans/staff/customer exist:

```bash
DATABASE_URL='postgresql://...neon...?sslmode=require' .venv/bin/python seed.py
```

(Install the Postgres driver locally first if needed: `.venv/bin/pip install psycopg2-binary`.)

## Notes & caveats

- **OTP is still a dev flow** (code shown on screen). For a public deployment, wire `generate_otp`
  in `nse/utils.py` to a real SMS gateway, or anyone can log in as any phone number.
- **Vercel Blob REST API:** `save_upload` calls the Blob REST endpoint directly (no official Python
  SDK). If uploads ever fail, check the `x-api-version` header in `_save_to_blob` against Vercel's
  current Blob API version.
- **Cold starts** re-run `db.create_all()` (cheap, idempotent) and reconnect to Postgres (NullPool).
- To change branding/contact later, update the Vercel env vars — not the templates.
