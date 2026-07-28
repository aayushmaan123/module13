# Module 13 — JWT Login/Registration with Client-Side Validation & Playwright E2E

A FastAPI application with JWT-based registration and login, server-rendered
front-end pages with client-side validation, Playwright browser end-to-end
tests, and a GitHub Actions pipeline that tests, scans, and publishes a Docker
image.

**Docker Hub:** https://hub.docker.com/r/aayushrox007/module13_is601

```bash
docker pull aayushrox007/module13_is601:latest
```

---

## What's in here

| Area | Location |
|------|----------|
| Registration endpoint | `POST /auth/register` — `app/main.py` |
| Login endpoint (JSON) | `POST /auth/login` — `app/main.py` |
| Login endpoint (form, for Swagger) | `POST /auth/token` — `app/main.py` |
| JWT creation / verification | `app/auth/jwt.py` |
| Password hashing (bcrypt via passlib) | `app/auth/jwt.py`, `app/models/user.py` |
| Pydantic validation schemas | `app/schemas/user.py`, `app/schemas/token.py` |
| Registration page | `templates/register.html` |
| Login page | `templates/login.html` |
| Playwright E2E auth tests | `tests/e2e/test_auth_playwright.py` |
| API E2E tests | `tests/e2e/test_fastapi_calculator.py` |
| CI/CD pipeline | `.github/workflows/test.yml` |

### Auth behaviour

- `POST /auth/register` — validates with Pydantic (`UserCreate`), rejects
  duplicate username/email, hashes the password with bcrypt, stores the user,
  returns `201` with the created user.
- `POST /auth/login` — verifies the hashed password and returns `200` with an
  access token, refresh token, expiry, and user fields. Bad credentials return
  `401 Invalid username or password`.
- The front-end stores `access_token`, `refresh_token`, `token_expires`,
  `user_id`, and `username` in `localStorage` and redirects to `/dashboard`.

### Password rules (client and server agree)

At least 8 characters, one uppercase, one lowercase, one digit, one special
character. Enforced in `app/schemas/user.py` (`validate_password_strength`) and
mirrored in the browser by `isValidPassword()` in `templates/register.html`.

---

## Prerequisites

- Python 3.10+
- Docker Desktop (for the database and for building the image)

---

## Running the front-end

### Option A — Docker Compose (everything at once)

```bash
docker compose up --build
```

Then open:

| Page | URL |
|------|-----|
| Home | http://localhost:8000/ |
| Register | http://localhost:8000/register |
| Login | http://localhost:8000/login |
| Dashboard | http://localhost:8000/dashboard |
| Swagger docs | http://localhost:8000/docs |
| pgAdmin | http://localhost:5050 (admin@example.com / admin) |

Stop with `docker compose down`.

### Option B — Local Python, Postgres in Docker

```bash
# 1. Start only the database
docker compose up -d db

# 2. Create a virtual environment and install dependencies
python -m venv venv
source venv/bin/activate        # Mac/Linux
venv\Scripts\activate           # Windows
pip install -r requirements.txt

# 3. Point the app at the database and run it
export DATABASE_URL="postgresql://postgres:postgres@localhost:5432/fastapi_db"
uvicorn app.main:app --reload --port 8000
```

On Windows PowerShell use
`$env:DATABASE_URL = "postgresql://postgres:postgres@localhost:5432/fastapi_db"`.

Try it: open http://localhost:8000/register, create an account with a password
such as `SecurePass123!`, then log in at http://localhost:8000/login. After a
successful login the JWT is visible in DevTools under
**Application → Local Storage → access_token**.

---

## Running the tests

Install the browser Playwright needs (once per machine):

```bash
playwright install chromium
```

The test suite starts its own uvicorn server, so `uvicorn` must be on `PATH` —
activate the virtual environment first. The database must be running
(`docker compose up -d db`) and `DATABASE_URL` must be set as above.

```bash
# Everything (unit + integration + e2e) with coverage
pytest

# Unit tests only
pytest tests/unit/

# Integration tests only
pytest tests/integration/

# API end-to-end tests
pytest tests/e2e/test_fastapi_calculator.py

# Playwright browser end-to-end tests
pytest tests/e2e/test_auth_playwright.py -m e2e
```

To watch the browser instead of running headless, set `headless=False` in the
`browser_context` fixture in `tests/conftest.py`.

The test server's output is written to `test-server.log` in the project root,
which is useful when a test fails for a server-side reason.

### What the Playwright tests cover

**Positive**

- Register page and login page render their forms.
- Register with valid data → success alert, then redirect to `/login`.
- Login with correct credentials → success alert, JWT stored in
  `localStorage`, then redirect to `/dashboard`.

**Negative**

- Short password → client-side error, and no request is sent to
  `/auth/register`.
- Password with no special character → client-side error, no request sent.
- Invalid email format → client-side error.
- Mismatched confirm password → client-side error.
- Username under 3 characters → client-side error.
- Duplicate registration → server returns `400`, UI shows the error alert.
- Wrong password → server returns `401`, UI shows "Invalid username or
  password", and nothing is stored in `localStorage`.
- Unknown username → server returns `401`, UI shows the error.
- Empty login fields → client-side error, no request sent.

---

## CI/CD pipeline

`.github/workflows/test.yml` runs on every push and pull request to `main`:

1. **test** — spins up a Postgres service, installs dependencies, installs
   Chromium (`playwright install --with-deps chromium`), then runs unit,
   integration, API E2E, and Playwright E2E tests.
2. **security** — builds the image and scans it with Trivy; the job fails on
   any `CRITICAL` or `HIGH` vulnerability that has a fix available.
3. **deploy** — only on `main`, and only if the first two jobs pass: logs in to
   Docker Hub and pushes `aayushrox007/module13_is601:latest` plus a
   commit-SHA tag, for `linux/amd64` and `linux/arm64`.

### Required GitHub repository secrets

| Secret | Value |
|--------|-------|
| `DOCKERHUB_USERNAME` | `aayushrox007` |
| `DOCKERHUB_TOKEN` | A Docker Hub access token (Account Settings → Personal access tokens) |

The `deploy` job uses the `production` environment, so add the secrets there if
that environment has its own secret scope.

---

## Configuration

Settings are read by `app/core/config.py` from the environment (or a `.env`
file), with local-development defaults:

| Variable | Default | Purpose |
|----------|---------|---------|
| `DATABASE_URL` | `postgresql://postgres:postgres@localhost:5432/fastapi_db` | Postgres connection |
| `JWT_SECRET_KEY` | dev placeholder | Signs access tokens |
| `JWT_REFRESH_SECRET_KEY` | dev placeholder | Signs refresh tokens |
| `ALGORITHM` | `HS256` | JWT algorithm |
| `ACCESS_TOKEN_EXPIRE_MINUTES` | `30` | Access token lifetime |
| `REFRESH_TOKEN_EXPIRE_DAYS` | `7` | Refresh token lifetime |
| `BCRYPT_ROUNDS` | `12` | Password hashing cost |

The defaults are for local development only. Set real secrets through the
environment in any deployed setting; `.env` is gitignored.

---

## Troubleshooting

| Symptom | Cause and fix |
|---------|---------------|
| `ServerStartupError: Failed to start test server` | `uvicorn` is not on `PATH`. Activate the virtual environment before running pytest. |
| `connection refused` on port 5432 | The database container is not running — `docker compose up -d db`. |
| `duplicate base class TimeoutError` | The unmaintained `aioredis` package on Python 3.11+. This repo uses `redis.asyncio` instead; reinstall from `requirements.txt`. |
| Playwright errors about a missing browser | Run `playwright install chromium`. |
| `422` on register with a valid-looking password | The password needs a special character, e.g. `SecurePass123!`. |
