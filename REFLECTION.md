# Module 13 Reflection

## What I set out to do

Add JWT registration and login with working front-end pages, prove both flows
with Playwright browser tests, and keep the Docker-based CI/CD pipeline pushing
a verified image to Docker Hub.

The starter repository already contained a lot of this: the `/auth/register`
and `/auth/login` endpoints, the Pydantic schemas, bcrypt hashing, and the
`register.html` / `login.html` templates with client-side validation. My work
concentrated on the parts that were missing or broken — the browser tests, the
pipeline, and three real defects that only surfaced once the tests ran.

## Challenges

### 1. The "E2E tests" were not end-to-end through the browser

`tests/e2e/test_fastapi_calculator.py` uses `requests` to call the API. That is
a useful integration test, but it never loads a page, so it cannot catch a
broken form, a typo in a field id, or a token that is never written to
`localStorage`. The one Playwright example in the repo (`tests/e2e/test_e2e.bk`)
targeted a calculator UI with `#a` and `#b` inputs that does not exist in these
templates, and the `.bk` extension meant pytest never collected it.

I wrote `tests/e2e/test_auth_playwright.py` with 16 tests that drive real
Chromium. The distinction I cared about most was *where* a rejection happens.
For client-side rules I attach a `page.on("request", ...)` listener and assert
that no request to `/auth/register` was ever made — that proves the browser
blocked it rather than the server. For server-side rules I use
`page.expect_response()` and assert the actual status code (400 for a duplicate
user, 401 for a bad password) *and* that the UI surfaces the message. Asserting
only on visible text would pass even if the status code were wrong.

### 2. Client and server disagreed about what a valid password is

My first run failed on every registration test. The browser accepted the
password and sent the request; the server returned `422`. The front-end's
`isValidPassword()` checked for 8 characters, an uppercase, a lowercase, and a
digit — but `UserCreate.validate_password_strength` in `app/schemas/user.py`
*also* requires a special character.

A real user typing `SecurePass123` would pass every visible check, submit, and
get an unhelpful error. Worse, FastAPI returns `detail` as a *list of error
objects* for a 422, while the page's handler did `new Error(data.detail)` — so
the alert would have read `[object Object]`.

I fixed both: `isValidPassword()` now mirrors the server rule exactly (with a
comment pointing at the schema so the two stay linked), and a new
`extractErrorMessage()` helper normalizes both the string form and the list
form of `detail` into readable text.

The lesson is that duplicated validation logic is duplicated *drift*. The two
copies exist for good reasons — the client copy gives instant feedback, the
server copy is the one that actually protects the database — but nothing forces
them to agree, so the tests have to.

### 3. The test server froze partway through the suite

The most interesting bug. With all 15 tests running, the last two failed with
`Page.goto: Timeout 30000ms exceeded` — but only the last two, and only when
the whole file ran. Running them alone passed.

The `fastapi_server` fixture in `tests/conftest.py` launched uvicorn with
`stdout=subprocess.PIPE, stderr=subprocess.PIPE` and then never read from those
pipes. Every request writes an access-log line. Once the OS pipe buffer filled,
uvicorn blocked on its next write and stopped serving. The server had not
crashed — it was stuck, which is why the symptom was a navigation timeout
rather than a connection error.

I redirected the server's output to `test-server.log` instead, since writing to
a file never blocks, and kept the log readable so a startup failure still
reports the underlying uvicorn error. The whole file then passed in 31 seconds
instead of timing out at 92.

This one was worth the time it took: the failure looked like flaky Playwright
timing, and the tempting "fix" was to raise the timeout. That would have hidden
a genuine bug that gets worse as the suite grows.

### 4. A form that refused to submit and said nothing

While capturing screenshots I found a third front-end defect. The confirm
password field called `setCustomValidity("Passwords don't match")`, and the
listener that cleared it fired on `keyup` only. Any value that arrives without
a keystroke — a paste, a password manager, a script — left the message set. The
browser then blocked the submit natively: no request, no error alert, no
explanation. The form simply stopped working.

My first fix was to listen on `input` instead, which does fire for pastes. That
cleared the stale state, but it exposed a second problem: when the passwords
genuinely differ, native validation blocks the submit before the page's own
handler runs, so the mismatch is reported by a browser tooltip while every
other validation failure uses the page's red alert box. Two different reporting
mechanisms for the same class of error, only one of which a test can see.

I removed the custom validity entirely. The submit handler already checks for a
mismatch and calls `showError('Passwords do not match')`, so there is now one
path, it is consistent with every other rule, and it is assertable. I added
`test_register_succeeds_after_correcting_a_mismatch` to pin the behaviour: fail
the match, fix it, submit successfully.

Worth noting that this bug was invisible to my own test suite before I hit it —
my tests happened to fill the password field before the confirm field, the one
order in which the stale message never got set. I found it only because the
screenshot script filled the fields in a different order. Passing tests are
evidence about the paths you thought of.

### 5. Environment and pipeline details

- **`aioredis` on modern Python.** The pinned `aioredis==2.0.1` fails to import
  on Python 3.11+ with `duplicate base class TimeoutError`. Because
  `app/auth/jwt.py` imports `app/auth/redis.py`, this breaks the entire app,
  not just token blacklisting. `redis-py` ships the same asyncio client as
  `redis.asyncio`, so aliasing it kept the module's API identical.
- **`bcrypt` was not pinned.** `passlib` needs a backend; without it, hashing
  fails at runtime rather than at install time.
- **`uvicorn` resolution.** My first local run failed with
  `ServerStartupError` because the fixture shells out to `uvicorn` by name, and
  PATH resolved it to a different Python installation. Worth remembering that a
  subprocess does not inherit "which interpreter is running pytest".
- **Pipeline corrections.** The workflow measured coverage against a `src`
  directory that does not exist (`--cov=src` → `--cov=app`), and installed
  Playwright without system dependencies (`playwright install` →
  `playwright install --with-deps chromium`, which the GitHub runner needs). I
  also pointed the deploy tags at my own Docker Hub repository and added a
  `.dockerignore` so the local `venv/` and coverage output stay out of the
  image.

### 6. Getting to 100% coverage

Coverage started at 68%, with `app/main.py` reporting 0% — not because it was
untested, but because the E2E tests exercise it in a *subprocess* that
`pytest-cov` cannot instrument. Adding `tests/integration/test_main_api.py`,
which drives the same routes in-process with `TestClient`, took `main.py` from
0% to 100% on its own and runs in a fraction of the time a browser takes.

The rest came from `tests/integration/test_auth_internals.py` (token decoding,
expiry, revocation, the Redis blacklist behind a fake client) and
`tests/unit/test_model_schema_edges.py` (the model guard clauses that FastAPI's
validation normally rejects long before they run). The suite is now 204 tests,
and CI fails the build below 100% via `--cov-fail-under=100`.

Two things were worth more than the number itself.

First, chasing the last few lines found **dead code**. Three checks could never
execute: `UserCreate.validate_password_strength` re-checked a length the
field's `min_length=8` had already enforced, and both calculation schemas
re-checked a list length already enforced by `min_items=2`. Pydantic runs field
constraints before model validators, so those branches were unreachable. I
removed them rather than marking them `# pragma: no cover` — a coverage tool
that reports 100% while the source contains lines that cannot run is telling a
comfortable lie. Notably, one of them raised a *different* message than the one
users actually receive, so the dead code was also misleading documentation.

Second, the new async tests failed only in the full suite, passing in
isolation: `asyncio.run() cannot be called from a running event loop`.
Playwright's session-scoped `sync_playwright` keeps a loop running on the main
thread. Running those coroutines on a worker thread with its own loop fixed it.
That is the second time this module that a test failed purely because of what
*else* was running — a good argument for never trusting a green run of a single
file.

### 7. The pipeline failed three times for three different reasons

Getting all three jobs green took four runs, and no two failures had the same
cause. Working through them in order was the only way; each one hid the next.

**Trivy blocked the build on seven vulnerable packages.** Two mattered directly
here: `python-jose` 3.3.0 (CVE-2024-33663, CRITICAL, algorithm confusion) is
the library signing this application's JWTs, and `h11` 0.14.0 (CVE-2025-43859,
CRITICAL) is request smuggling in the HTTP stack. The rest were a path
traversal in `python-multipart`, SSRF via UNC paths in `starlette`'s
`StaticFiles`, and decompression bombs in `urllib3`. It would have been easy to
add an ignore file and move on. Patching them was the point of having the scan.

**Patching broke the app.** `starlette` had to go from 0.45.3 to 1.3.1, and
Starlette 1.0 removed the legacy `TemplateResponse(name, {"request": request})`
signature that all four page routes used. Every HTML route started returning
500, which cascaded into all fifteen Playwright tests. The fix was one argument
order per route - but I found it immediately only because the browser tests
existed. An API-only suite would have stayed green while every page was broken.

**Then the base image was too old.** The patched `cryptography` and `cffi`
releases publish no wheels for Python 3.10, so `pip install` failed inside the
Docker build with `ResolutionImpossible`. The Dockerfile base and the CI runner
both moved to 3.12. Worth noting the failure mode: the build failed but the
scan step still ran, so Trivy rescanned the *previous* image and reported the
old findings. For a moment it looked like the upgrade had changed nothing.
Always confirm that the thing you scanned is the thing you just built.

**Finally, credentials.** The deploy job failed twice more. First
`401 Unauthorized: access token has insufficient scopes` - the Docker Hub token
was read-only, and notably `docker login` *succeeded* with it, because login
needs less than push does. A green login step is not proof that a push will
work. Then `malformed HTTP Authorization header`, a formatting problem in the
stored secret rather than a wrong token.

The general lesson: a CI pipeline that has never gone green is not one problem,
it is a stack of them, and each layer stays invisible until the one above it
passes.

## What I would do differently

I would consider deriving the client-side password rules from a single source
rather than hand-copying them into JavaScript — serving the rules from an
endpoint, or generating the regex — so the drift I hit in challenge 2 cannot
happen again.

I would also treat 100% as a floor rather than a finish line. Statement
coverage says every line ran, not that every line was *checked*: none of it
would have caught the password-rule mismatch if I had not asserted on the
specific behaviour. The bugs in this module were found by tests that asserted
what users see, not by the coverage percentage.

## Takeaway

Every defect I found this module was invisible to the API-level tests and
visible the moment a browser was involved: a validator that disagreed with its
server, an error path that would have rendered `[object Object]`, and a server
that quietly wedged itself under sustained load. End-to-end tests earn their
cost by failing in ways unit tests structurally cannot.
