# Caffio — backend

Django REST API for **Caffio**, a multi-branch coffee shop management system: point of sale,
inventory, cash sessions, expenses, reports, notifications and an audit log. The web app is
in [`coffee_shop_frontend`](https://github.com/Ariel-Aganze/coffee_shop_frontend).

**Stack:** Python 3.12 · Django 5.0 · Django REST Framework · SimpleJWT · PostgreSQL ·
drf-spectacular (OpenAPI) · ReportLab (PDF exports)

## Getting started

```sh
python3.12 -m venv venv
venv/bin/pip install -r requirements.txt
cp .env.example .env            # then adjust DB_* to your local Postgres
venv/bin/python manage.py migrate
venv/bin/python manage.py seed_demo   # optional: a demo business with 4 weeks of activity
venv/bin/python manage.py runserver
```

- API docs (Swagger): http://localhost:8000/api/docs/ (OpenAPI schema at `/api/schema/`)
- Health check: `/api/health/`
- Demo accounts from `seed_demo`: `demo_owner`, `demo_admin`, `demo_manager1`,
  `demo_cashier1a`, `demo_inventory`, … with password `Demo1234!`. The command refuses to
  run when `DEBUG` is off unless you pass `--force`.

## Tests

```sh
venv/bin/python manage.py test
```

`apps/sales/test_critical_scenarios.py` maps each critical scenario in SRS §13.3 to the
tests that cover it. The frontend repository also has end-to-end tests (Playwright),
which run against this API on a separate database, `coffee_shop_e2e`.

## Configuration

All settings come from environment variables (or `.env` locally). See
[`.env.example`](.env.example).

| Variable                 | Notes                                                                 |
| ------------------------ | --------------------------------------------------------------------- |
| `DJANGO_SECRET_KEY`      | Required. Long and random in production.                              |
| `DJANGO_DEBUG`           | `False` in production. This turns on HTTPS redirects, HSTS and secure cookies. |
| `DJANGO_ALLOWED_HOSTS`   | Comma-separated. On Render the service's own host is added automatically. |
| `DATABASE_URL`           | Used if set (Render). Otherwise `DB_NAME`, `DB_USER`, `DB_PASSWORD`, `DB_HOST` and `DB_PORT`. |
| `CORS_ALLOWED_ORIGINS`   | The web app's origin(s), e.g. `https://caffio-web.onrender.com`.       |
| `FRONTEND_URL`           | Used in password-reset links.                                         |
| `EMAIL_*`                | SMTP for password-reset emails. The console backend only logs them.   |

## Files

Uploads are stored under `MEDIA_ROOT`. Product photos and business logos are public at
`/media/…`. **Expense receipts are private.** They are never served under `/media/`, only
through `GET /api/expenses/{id}/attachment/`, to users who can see that expense.

## Deployment (Render)

| Service      | Type            | Settings                                                                                  |
| ------------ | --------------- | ----------------------------------------------------------------------------------------- |
| `caffio-db`  | PostgreSQL 18   | Frankfurt                                                                                 |
| `caffio-api` | Web service     | Build: `pip install -r requirements.txt && python manage.py collectstatic --noinput`. Start: `python manage.py migrate --noinput && gunicorn config.wsgi:application --bind 0.0.0.0:$PORT --workers 2 --timeout 60`. Health check: `/api/health/` |
| `caffio-web` | Static site     | From the frontend repository. See its README.                                             |

The API's environment: `DATABASE_URL` (the database's *internal* URL), `DJANGO_SECRET_KEY`,
`DJANGO_DEBUG=False`, `CORS_ALLOWED_ORIGINS` and `FRONTEND_URL` (the static site's URL),
`PYTHON_VERSION=3.12.10`.

Migrations run on every start, because free instances have no pre-deploy step. Pushing to
`main` redeploys.

**Limits of the free plan:**
- The API sleeps after 15 minutes without traffic, and the first request after that takes
  about a minute.
- The free database expires 30 days after it was created.
- **The disk is temporary.** Uploaded photos and receipts are lost whenever the service
  restarts or redeploys. Before real use, move `STORAGES["default"]` to object storage
  (S3, Cloudflare R2 and so on) or switch to a paid instance with a persistent disk.
