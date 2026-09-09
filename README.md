# CitizenConnect — deployment-ready Flask app

## What was fixed
- Removed OTP from registration completely.
- Removed OTP/Twilio password-reset flow; password reset is phone + new password as requested.
- Login is phone + password for citizen/staff.
- Fixed staff proof upload handling.
- Added persistent, deterministic Track IDs (`CZN-000001`) so IDs remain valid after restarts.
- Citizen complaint list and live tracking now read from the database.
- Staff dashboard now reads real complaints and can claim/update them.
- Staff can mark complaints resolved/rejected and attach resolution proof/comments.
- Citizen profile API added.
- Added input validation, upload limits, safe filenames, session hardening and production secret enforcement.
- Added SQLite migration for the supplied database.
- Added Gunicorn/requirements and environment example.

## Project layout
```
pri_pwd/
├── main.py
├── requirements.txt
├── Procfile              # for gunicorn on Render/Railway/Heroku-style hosts
├── runtime.txt
├── .env.example          # copy to .env, never commit .env itself
├── templates/             # Flask/Jinja templates (must live here, not project root)
└── static/js/animations.js
```

## Run locally
```bash
python -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env        # then edit SECRET_KEY etc.
python main.py
```
Open `http://127.0.0.1:5003`.

## Deploy (Render / Railway / any Docker-less PaaS)
1. Push this repo to GitHub.
2. Create a new web service pointing at the repo.
3. Build command: `pip install -r requirements.txt`
4. Start command: `gunicorn main:app --bind 0.0.0.0:$PORT --workers 2 --timeout 60` (already in `Procfile`, so Render/Heroku pick it up automatically).
5. Set environment variables in the host's dashboard:
   - `SECRET_KEY` — required, long random string (app **refuses to start** in production without it)
   - `FLASK_ENV=production`
   - `COOKIE_SECURE=1` (once you're on HTTPS, which PaaS platforms give you by default)
   - `DATABASE_URL` — optional, only if you're moving off SQLite (e.g. Postgres)
   - `GOOGLE_API_KEY` — optional, enables AI complaint descriptions/image analysis; app degrades gracefully without it
6. **SQLite + ephemeral disks**: platforms like Render/Railway wipe local disk on redeploy unless you attach a persistent volume. If you stick with SQLite, mount a persistent disk at the app directory (or point `DATABASE_URL` at a managed Postgres instance instead — recommended for anything beyond a demo).
7. Uploaded files (`uploads/`) live on local disk too — same persistence caveat applies. For production-grade file storage, swap `save_upload()` in `main.py` for S3/Cloud Storage later; not required to get the app running.

## What was verified working end-to-end
- Citizen: register → login → lodge complaint → view complaint list → track by Track ID
- Staff: register → login → view stats/dashboard → claim complaint → update status + comments
- Password reset (phone + new password, no OTP)
- Static assets and all page templates serve correctly under the standard Flask `templates/` + `static/` layout
- Runs correctly under both `python main.py` (dev) and `gunicorn main:app` (prod)
