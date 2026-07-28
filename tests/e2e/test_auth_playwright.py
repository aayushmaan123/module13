"""
Playwright end-to-end tests for the JWT registration and login pages.

These tests drive a real Chromium browser against the live FastAPI server
(started by the `fastapi_server` fixture in tests/conftest.py). They cover both
the client-side validation that runs entirely in the browser and the
server-backed flows that hit /auth/register and /auth/login.

Positive cases
  - register with valid data  -> success alert is shown
  - login with correct creds  -> success alert + JWT stored in localStorage

Negative cases
  - register with a short password  -> client-side error, no request sent
  - register with a bad email       -> client-side error
  - register with mismatched confirm-> client-side error
  - register with a duplicate user  -> server 400, error alert shown
  - login with the wrong password   -> server 401, "invalid credentials" alert
"""
from uuid import uuid4

import pytest
from playwright.sync_api import Page, expect

# Must satisfy UserCreate.validate_password_strength in app/schemas/user.py:
# 8+ chars, upper, lower, digit, special character.
VALID_PASSWORD = "SecurePass123!"


@pytest.fixture
def base_url(fastapi_server: str) -> str:
    """FastAPI server base URL without a trailing slash."""
    return fastapi_server.rstrip("/")


def make_user() -> dict:
    """Build a unique, valid registration payload."""
    suffix = uuid4().hex[:10]
    return {
        "username": f"user_{suffix}",
        "email": f"user_{suffix}@example.com",
        "first_name": "Test",
        "last_name": "User",
        "password": VALID_PASSWORD,
        "confirm_password": VALID_PASSWORD,
    }


def fill_registration_form(page: Page, user: dict) -> None:
    """Type a registration payload into the form fields."""
    page.fill("#username", user["username"])
    page.fill("#email", user["email"])
    page.fill("#first_name", user["first_name"])
    page.fill("#last_name", user["last_name"])
    page.fill("#password", user["password"])
    page.fill("#confirm_password", user["confirm_password"])


def register_via_ui(page: Page, base_url: str, user: dict) -> None:
    """Register a user through the browser and wait for the success alert."""
    page.goto(f"{base_url}/register")
    fill_registration_form(page, user)
    page.click("#registrationForm button[type='submit']")
    expect(page.locator("#successAlert")).to_be_visible()


# ---------------------------------------------------------------------------
# Page rendering
# ---------------------------------------------------------------------------
@pytest.mark.e2e
def test_register_page_renders_form(page: Page, base_url: str):
    page.goto(f"{base_url}/register")
    expect(page.locator("#registrationForm")).to_be_visible()
    for field in ("#username", "#email", "#password", "#confirm_password"):
        expect(page.locator(field)).to_be_visible()


@pytest.mark.e2e
def test_login_page_renders_form(page: Page, base_url: str):
    page.goto(f"{base_url}/login")
    expect(page.locator("#loginForm")).to_be_visible()
    expect(page.locator("#username")).to_be_visible()
    expect(page.locator("#password")).to_be_visible()


# ---------------------------------------------------------------------------
# Positive: registration
# ---------------------------------------------------------------------------
@pytest.mark.e2e
def test_register_with_valid_data_shows_success(page: Page, base_url: str):
    user = make_user()

    page.goto(f"{base_url}/register")
    fill_registration_form(page, user)
    page.click("#registrationForm button[type='submit']")

    expect(page.locator("#successAlert")).to_be_visible()
    expect(page.locator("#successMessage")).to_contain_text("Registration successful")
    expect(page.locator("#errorAlert")).to_be_hidden()


@pytest.mark.e2e
def test_register_redirects_to_login(page: Page, base_url: str):
    """After a successful registration the page redirects to /login."""
    user = make_user()

    page.goto(f"{base_url}/register")
    fill_registration_form(page, user)
    page.click("#registrationForm button[type='submit']")

    page.wait_for_url("**/login", timeout=10_000)
    expect(page.locator("#loginForm")).to_be_visible()


# ---------------------------------------------------------------------------
# Positive: login
# ---------------------------------------------------------------------------
@pytest.mark.e2e
def test_login_with_valid_credentials_stores_jwt(page: Page, base_url: str):
    user = make_user()
    register_via_ui(page, base_url, user)

    page.goto(f"{base_url}/login")
    page.fill("#username", user["username"])
    page.fill("#password", user["password"])
    page.click("#loginForm button[type='submit']")

    expect(page.locator("#successAlert")).to_be_visible()
    expect(page.locator("#successMessage")).to_contain_text("Login successful")

    access_token = page.evaluate("() => localStorage.getItem('access_token')")
    refresh_token = page.evaluate("() => localStorage.getItem('refresh_token')")
    stored_username = page.evaluate("() => localStorage.getItem('username')")

    assert access_token, "access_token was not stored in localStorage"
    assert refresh_token, "refresh_token was not stored in localStorage"
    assert stored_username == user["username"]
    # A JWT is three dot-separated segments.
    assert access_token.count(".") == 2, f"Stored token is not a JWT: {access_token}"


@pytest.mark.e2e
def test_login_redirects_to_dashboard(page: Page, base_url: str):
    user = make_user()
    register_via_ui(page, base_url, user)

    page.goto(f"{base_url}/login")
    page.fill("#username", user["username"])
    page.fill("#password", user["password"])
    page.click("#loginForm button[type='submit']")

    page.wait_for_url("**/dashboard", timeout=10_000)


# ---------------------------------------------------------------------------
# Negative: client-side validation (no request should reach the server)
# ---------------------------------------------------------------------------
@pytest.mark.e2e
def test_register_short_password_shows_client_error(page: Page, base_url: str):
    user = make_user()
    user["password"] = "Ab1"
    user["confirm_password"] = "Ab1"

    page.goto(f"{base_url}/register")

    requests_sent = []
    page.on("request", lambda req: requests_sent.append(req.url))

    fill_registration_form(page, user)
    page.click("#registrationForm button[type='submit']")

    expect(page.locator("#errorAlert")).to_be_visible()
    expect(page.locator("#errorMessage")).to_contain_text("at least 8 characters")
    expect(page.locator("#successAlert")).to_be_hidden()

    assert not any("/auth/register" in url for url in requests_sent), (
        "Client-side validation should have blocked the request to /auth/register"
    )


@pytest.mark.e2e
def test_register_password_without_special_char_shows_client_error(page: Page, base_url: str):
    """The client mirrors the server rule that a special character is required."""
    user = make_user()
    user["password"] = "SecurePass123"
    user["confirm_password"] = "SecurePass123"

    page.goto(f"{base_url}/register")

    requests_sent = []
    page.on("request", lambda req: requests_sent.append(req.url))

    fill_registration_form(page, user)
    page.click("#registrationForm button[type='submit']")

    expect(page.locator("#errorAlert")).to_be_visible()
    expect(page.locator("#errorMessage")).to_contain_text("special character")
    assert not any("/auth/register" in url for url in requests_sent)


@pytest.mark.e2e
def test_register_invalid_email_shows_client_error(page: Page, base_url: str):
    user = make_user()
    # Passes the browser's native type=email check but fails the page's regex,
    # which requires a dot in the domain.
    user["email"] = "bad@example"

    page.goto(f"{base_url}/register")
    fill_registration_form(page, user)
    page.click("#registrationForm button[type='submit']")

    expect(page.locator("#errorAlert")).to_be_visible()
    expect(page.locator("#errorMessage")).to_contain_text("valid email address")


@pytest.mark.e2e
def test_register_password_mismatch_shows_client_error(page: Page, base_url: str):
    user = make_user()
    user["confirm_password"] = VALID_PASSWORD + "different1"

    page.goto(f"{base_url}/register")
    fill_registration_form(page, user)
    page.click("#registrationForm button[type='submit']")

    expect(page.locator("#errorAlert")).to_be_visible()
    expect(page.locator("#errorMessage")).to_contain_text("do not match")


@pytest.mark.e2e
def test_register_short_username_shows_client_error(page: Page, base_url: str):
    user = make_user()
    user["username"] = "ab"

    page.goto(f"{base_url}/register")
    fill_registration_form(page, user)
    page.click("#registrationForm button[type='submit']")

    expect(page.locator("#errorAlert")).to_be_visible()
    expect(page.locator("#errorMessage")).to_contain_text("at least 3 characters")


# ---------------------------------------------------------------------------
# Negative: server-side rejection surfaced in the UI
# ---------------------------------------------------------------------------
@pytest.mark.e2e
def test_register_duplicate_user_shows_server_error(page: Page, base_url: str):
    user = make_user()
    register_via_ui(page, base_url, user)

    # Register the same username/email a second time -> 400 from the API.
    page.goto(f"{base_url}/register")
    fill_registration_form(page, user)

    with page.expect_response("**/auth/register") as response_info:
        page.click("#registrationForm button[type='submit']")
    assert response_info.value.status == 400, (
        f"Expected 400 for duplicate registration, got {response_info.value.status}"
    )

    expect(page.locator("#errorAlert")).to_be_visible()


@pytest.mark.e2e
def test_login_wrong_password_shows_401_error(page: Page, base_url: str):
    user = make_user()
    register_via_ui(page, base_url, user)

    page.goto(f"{base_url}/login")
    page.fill("#username", user["username"])
    page.fill("#password", "WrongPassword999")

    with page.expect_response("**/auth/login") as response_info:
        page.click("#loginForm button[type='submit']")
    assert response_info.value.status == 401, (
        f"Expected 401 for a bad password, got {response_info.value.status}"
    )

    expect(page.locator("#errorAlert")).to_be_visible()
    expect(page.locator("#errorMessage")).to_contain_text("Invalid username or password")
    expect(page.locator("#successAlert")).to_be_hidden()

    assert page.evaluate("() => localStorage.getItem('access_token')") is None, (
        "No token should be stored after a failed login"
    )


@pytest.mark.e2e
def test_login_unknown_user_shows_401_error(page: Page, base_url: str):
    page.goto(f"{base_url}/login")
    page.fill("#username", f"nobody_{uuid4().hex[:8]}")
    page.fill("#password", VALID_PASSWORD)

    with page.expect_response("**/auth/login") as response_info:
        page.click("#loginForm button[type='submit']")
    assert response_info.value.status == 401

    expect(page.locator("#errorAlert")).to_be_visible()
    expect(page.locator("#errorMessage")).to_contain_text("Invalid username or password")


@pytest.mark.e2e
def test_login_empty_fields_shows_client_error(page: Page, base_url: str):
    """Submitting a blank login form is caught client-side before any fetch."""
    page.goto(f"{base_url}/login")

    requests_sent = []
    page.on("request", lambda req: requests_sent.append(req.url))

    page.fill("#username", "   ")
    page.fill("#password", "")
    page.locator("#loginForm").evaluate(
        "form => form.dispatchEvent(new Event('submit', {cancelable: true}))"
    )

    expect(page.locator("#errorAlert")).to_be_visible()
    expect(page.locator("#errorMessage")).to_contain_text("fill in all fields")
    assert not any("/auth/login" in url for url in requests_sent)
