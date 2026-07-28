"""
Edge-case tests for the models, schemas, and the database session helper.

These cover the guard clauses and failure branches that the route-level tests
never reach, because FastAPI's validation rejects bad input long before it gets
near the model.
"""
import uuid
from datetime import timedelta

import pytest
from jose import jwt as jose_jwt
from pydantic import ValidationError

from app.core.config import get_settings
from app.database import get_db
from app.models.calculation import (
    Addition,
    Calculation,
    Division,
    Multiplication,
    Subtraction,
)
from app.models.user import User, utcnow
from app.schemas.calculation import CalculationBase, CalculationUpdate
from app.schemas.user import PasswordUpdate, UserCreate

settings = get_settings()

VALID_USER = {
    "first_name": "Edge",
    "last_name": "Case",
    "email": "edge.case@example.com",
    "username": "edgecase",
    "password": "SecurePass123!",
    "confirm_password": "SecurePass123!",
}


# ---------------------------------------------------------------------------
# app/database.py
# ---------------------------------------------------------------------------
def test_get_db_yields_a_session_and_closes_it():
    generator = get_db()
    session = next(generator)

    assert session.is_active

    with pytest.raises(StopIteration):
        next(generator)


# ---------------------------------------------------------------------------
# app/models/calculation.py
# ---------------------------------------------------------------------------
def test_base_calculation_has_no_result():
    calc = Calculation(user_id=uuid.uuid4(), inputs=[1, 2])

    with pytest.raises(NotImplementedError):
        calc.get_result()


def test_calculation_repr_includes_type_and_inputs():
    calc = Addition(user_id=uuid.uuid4(), inputs=[1, 2])

    text = repr(calc)

    assert "Calculation(" in text
    assert "[1, 2]" in text


def test_create_rejects_an_unsupported_type():
    with pytest.raises(ValueError, match="Unsupported calculation type"):
        Calculation.create("modulo", uuid.uuid4(), [4, 2])


@pytest.mark.parametrize(
    "calculation_class", [Addition, Subtraction, Multiplication, Division]
)
def test_inputs_must_be_a_list(calculation_class):
    calc = calculation_class(user_id=uuid.uuid4(), inputs="not a list")

    with pytest.raises(ValueError, match="must be a list of numbers"):
        calc.get_result()


@pytest.mark.parametrize(
    "calculation_class", [Addition, Subtraction, Multiplication, Division]
)
def test_inputs_need_at_least_two_numbers(calculation_class):
    calc = calculation_class(user_id=uuid.uuid4(), inputs=[42])

    with pytest.raises(ValueError, match="at least two numbers"):
        calc.get_result()


def test_division_by_zero_is_rejected():
    calc = Division(user_id=uuid.uuid4(), inputs=[10, 0])

    with pytest.raises(ValueError, match="Cannot divide by zero"):
        calc.get_result()


# ---------------------------------------------------------------------------
# app/models/user.py
# ---------------------------------------------------------------------------
def test_hashed_password_keyword_populates_the_password_column():
    user = User(
        first_name="Hash",
        last_name="Kwarg",
        email="hash.kwarg@example.com",
        username="hashkwarg",
        hashed_password="already-hashed",
    )

    assert user.password == "already-hashed"
    assert user.hashed_password == "already-hashed"


def test_user_str_describes_the_user():
    user = User(
        first_name="Str",
        last_name="Repr",
        email="str.repr@example.com",
        username="strrepr",
        password="hashed",
    )

    assert str(user) == "<User(name=Str Repr, email=str.repr@example.com)>"


def test_update_sets_attributes_and_refreshes_the_timestamp():
    user = User(
        first_name="Before",
        last_name="Update",
        email="before@example.com",
        username="beforeupdate",
        password="hashed",
    )
    user.updated_at = utcnow() - timedelta(days=1)
    previous_updated_at = user.updated_at

    returned = user.update(first_name="After", last_name="Updated")

    assert returned is user
    assert user.first_name == "After"
    assert user.last_name == "Updated"
    assert user.updated_at > previous_updated_at


def test_register_rejects_a_short_password(db_session):
    with pytest.raises(ValueError, match="at least 6 characters"):
        User.register(db_session, {**VALID_USER, "password": "abc"})


def test_authenticate_returns_none_for_an_unknown_user(db_session):
    assert User.authenticate(db_session, "nobody-at-all", "SecurePass123!") is None


def test_authenticate_returns_none_for_a_wrong_password(db_session):
    suffix = uuid.uuid4().hex[:8]
    user_data = {
        "first_name": "Edge",
        "last_name": "Auth",
        "email": f"edge_{suffix}@example.com",
        "username": f"edge_{suffix}",
        "password": "SecurePass123!",
    }
    User.register(db_session, user_data)
    db_session.commit()

    assert User.authenticate(db_session, user_data["username"], "WrongPass123!") is None


def test_verify_token_returns_none_without_a_subject():
    token = jose_jwt.encode(
        {"no_sub": True}, settings.JWT_SECRET_KEY, algorithm=settings.ALGORITHM
    )

    assert User.verify_token(token) is None


def test_verify_token_returns_none_for_a_non_uuid_subject():
    token = jose_jwt.encode(
        {"sub": "not-a-uuid"}, settings.JWT_SECRET_KEY, algorithm=settings.ALGORITHM
    )

    assert User.verify_token(token) is None


def test_verify_token_returns_none_for_a_malformed_token():
    assert User.verify_token("not-a-jwt") is None


# ---------------------------------------------------------------------------
# app/schemas/user.py
# ---------------------------------------------------------------------------
def test_user_create_rejects_mismatched_passwords():
    with pytest.raises(ValidationError, match="Passwords do not match"):
        UserCreate(**{**VALID_USER, "confirm_password": "DifferentPass123!"})


@pytest.mark.parametrize(
    "password,message",
    [
        ("nouppercase1!", "uppercase"),
        ("NOLOWERCASE1!", "lowercase"),
        ("NoDigitsHere!", "digit"),
        ("NoSpecialChar1", "special character"),
    ],
)
def test_user_create_enforces_password_strength(password, message):
    with pytest.raises(ValidationError, match=message):
        UserCreate(**{**VALID_USER, "password": password, "confirm_password": password})


def test_password_update_rejects_a_mismatched_confirmation():
    with pytest.raises(ValidationError, match="do not match"):
        PasswordUpdate(
            current_password="OldPass123!",
            new_password="NewPass123!",
            confirm_new_password="OtherPass123!",
        )


def test_password_update_rejects_reusing_the_current_password():
    with pytest.raises(ValidationError, match="must be different"):
        PasswordUpdate(
            current_password="SamePass123!",
            new_password="SamePass123!",
            confirm_new_password="SamePass123!",
        )


def test_password_update_accepts_a_valid_change():
    updated = PasswordUpdate(
        current_password="OldPass123!",
        new_password="NewPass123!",
        confirm_new_password="NewPass123!",
    )

    assert updated.new_password == "NewPass123!"


# ---------------------------------------------------------------------------
# app/schemas/calculation.py
# ---------------------------------------------------------------------------
def test_calculation_base_requires_two_inputs():
    with pytest.raises(ValidationError, match="at least 2 items"):
        CalculationBase(type="addition", inputs=[1])


def test_calculation_base_rejects_a_non_list_input():
    with pytest.raises(ValidationError, match="valid list"):
        CalculationBase(type="addition", inputs="1,2")


def test_calculation_base_rejects_an_unknown_type():
    with pytest.raises(ValidationError, match="Type must be one of"):
        CalculationBase(type="modulo", inputs=[4, 2])


def test_calculation_base_rejects_division_by_zero():
    with pytest.raises(ValidationError, match="Cannot divide by zero"):
        CalculationBase(type="division", inputs=[10, 0])


def test_calculation_base_allows_a_zero_numerator_for_division():
    calc = CalculationBase(type="division", inputs=[0, 10])

    assert calc.inputs == [0, 10]


def test_calculation_update_requires_two_inputs():
    with pytest.raises(ValidationError, match="at least 2 items"):
        CalculationUpdate(inputs=[1])


def test_calculation_update_allows_omitting_inputs():
    assert CalculationUpdate().inputs is None
