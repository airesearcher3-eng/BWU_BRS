# BRS Automation System

Bank Reconciliation Statement automation for **Brainware University**. Ingests
the bank statement, the ERP bank-book, and the prior-month BRS, runs a
multi-pass matching engine, surfaces unmatched items as exceptions with SLAs,
and produces an auditable signed-off BRS workbook.

Built with FastAPI + SQLite + a vanilla-JS SPA. Deployable via systemd or
Docker.

---

## Architecture at a glance

| Layer | Path | Notes |
| --- | --- | --- |
| HTTP API | [app.py](app.py), [routes/](routes/) | FastAPI with auth, rate limiting, security headers |
| Matching engine | [engine/matching/](engine/matching/) | 5-pass orchestrator (exact → aggregate → rules → FD → fallback) |
| Parsers | [engine/parsers/](engine/parsers/) | ICICI bank statement, ERP bank book, prior BRS |
| RAG (optional) | [engine/rag/](engine/rag/) | Gemini-backed assistant |
| DB | [models/database.py](models/database.py), [db/schema.sqlite.sql](db/schema.sqlite.sql) | SQLite (WAL) with audit log |
| Frontend | [templates/](templates/), [static/](static/) | Operator portal + admin portal |

---

## Quick start (development)

```bash
python -m venv .venv
. .venv/Scripts/activate           # Windows PowerShell: .venv\Scripts\Activate.ps1
pip install -r requirements-dev.txt

cp .env.example .env               # then edit secrets
python app.py                      # http://127.0.0.1:8000
```

A first-run admin user is created by [db/schema.sqlite.sql](db/schema.sqlite.sql).
Sign in, change the password, and create real users from the admin portal.

> **Sensitive data warning.** `.gitignore` now excludes `*.xlsx`, `*.xls`,
> `*.csv`, and `*.db` from the working tree. The bank workbooks and SQLite
> databases that previously sat at the repo root must **not** be committed.
> Move them outside the repo (or under `tests/fixtures/` for fixtures) and
> rotate any credentials they may have exposed.

---

## Configuration

All runtime configuration is environment-driven. See [.env.example](.env.example)
for the full list. Required in production:

- `ENV=production`
- `SECRET_KEY`, `JWT_SECRET` — generate with
  `python -c "import secrets; print(secrets.token_urlsafe(48))"`
- `CORS_ORIGINS` — explicit origins (wildcard rejected)
- `ALLOWED_HOSTS` — trusted Host header values

The app **fails fast on boot** if any of these are missing or unsafe.

---

## Running in production

### Option A — systemd

```bash
# As root:
useradd --system --create-home --home-dir /opt/brs brs
git clone <repo> /opt/brs && cd /opt/brs
sudo -u brs python -m venv .venv
sudo -u brs .venv/bin/pip install -r requirements.txt
sudo -u brs cp .env.example .env && sudo -u brs $EDITOR .env

cp systemd/brs-system.service /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now brs-system
systemctl status brs-system
```

The unit ([systemd/brs-system.service](systemd/brs-system.service)) runs
gunicorn with uvicorn workers under a non-root user, with `ProtectSystem=strict`
and a tight write-path allowlist. Front it with nginx/Caddy for TLS.

### Option B — Docker

```bash
cp .env.example .env  # fill in secrets
docker compose up -d
docker compose logs -f brs
```

The container ([Dockerfile](Dockerfile)) runs as UID 1000, drops all
capabilities, mounts the root filesystem read-only, and persists `db/`,
`uploads/`, `output/`, and `logs/` to named volumes.

### Reverse proxy (nginx)

```nginx
server {
    listen 443 ssl http2;
    server_name brs.example.edu;
    client_max_body_size 30M;          # match MAX_UPLOAD_BYTES + headroom

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_read_timeout 120s;
    }
}
```

---

## Operations

| Concern | Where |
| --- | --- |
| Liveness | `GET /healthz` (200 = process up) |
| Readiness | `GET /readyz` (200 = DB reachable, 503 otherwise) |
| Logs | JSON to stdout + rotating file at `logs/brs.log` (10 MB × 5) |
| Request IDs | `X-Request-ID` echoed on every response |
| Rate limiting | 120/min default, 5/min on `/api/auth/*` (sliding window, in-process) |
| Backups | `db/brs.db` is the only stateful artifact — back up the `db/` volume |
| Upload retention | Files older than `UPLOAD_MAX_AGE_DAYS` are pruned at startup |

For multi-host deployments, swap the in-process limiter for a Redis-backed
one (see [middleware.py](middleware.py)).

---

## Security model

- JWT auth (HS256) with bcrypt password hashes.
- All `/api/*` routes except `/api/auth/login` require a valid bearer token.
- CORS is locked to the configured origins; credentials are allowed only
  against an explicit list.
- Body-size cap at the middleware layer; per-route streaming caps in
  [routes/upload.py](routes/upload.py).
- Security headers: `X-Content-Type-Options`, `X-Frame-Options: DENY`,
  `Referrer-Policy`, `Permissions-Policy`, and HSTS in production.
- Production builds disable `/docs`, `/redoc`, and `/openapi.json`.

Audit log entries are written for uploads, run creation, approvals, and
admin actions ([models/database.py:insert_audit_log](models/database.py#L276)).

---

## Testing

```bash
pip install -r requirements-dev.txt
pytest -q
```

The API regression test ([tests/test_api_reports.py](tests/test_api_reports.py))
expects sample workbooks under `uploads/`. It is skipped in CI because those
files are intentionally not committed.

---

## Development workflow

```bash
ruff check .          # lint
ruff format .         # format
pytest                # tests
```

CI runs lint, tests, and a Docker build on every push
([.github/workflows/ci.yml](.github/workflows/ci.yml)).

---

## Project layout

```text
.
├── app.py                  # FastAPI entrypoint + middleware stack
├── config.py               # Env-driven settings, fail-fast checks
├── logging_config.py       # JSON logging with rotation
├── middleware.py           # Request ID, security headers, rate limit, body cap
├── gunicorn.conf.py        # Production WSGI/ASGI config
├── routes/                 # Auth, admin, upload, reconciliation, exceptions, approval, audit
├── engine/                 # Parsers, matching passes, BRS output, RAG
├── models/database.py      # SQLite helpers
├── db/schema.sqlite.sql    # Schema bootstrap
├── templates/, static/     # SPA assets
├── tests/                  # pytest suite
├── systemd/                # Service unit
├── Dockerfile, docker-compose.yml
└── .env.example
```
