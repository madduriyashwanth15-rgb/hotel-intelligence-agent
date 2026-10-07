from __future__ import annotations

import re
from pathlib import Path

import pytest
from pydantic import ValidationError

from hotel_agent.config import Settings

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def clean_env(monkeypatch: pytest.MonkeyPatch) -> pytest.MonkeyPatch:
    for field in Settings.model_fields:
        monkeypatch.delenv(field.upper(), raising=False)
    return monkeypatch


def test_required_credentials_have_no_defaults(clean_env: pytest.MonkeyPatch) -> None:
    with pytest.raises(ValidationError) as excinfo:
        Settings(_env_file=None)  # type: ignore[call-arg]
    missing = {err["loc"][0] for err in excinfo.value.errors()}
    assert missing == {"postgres_user", "postgres_password"}


def test_database_url_escapes_password(clean_env: pytest.MonkeyPatch) -> None:
    clean_env.setenv("POSTGRES_USER", "admin")
    clean_env.setenv("POSTGRES_PASSWORD", "p@ss:w/rd%#")
    clean_env.setenv("POSTGRES_PORT", "5544")
    s = Settings(_env_file=None)  # type: ignore[call-arg]
    url = s.database_url()
    assert url.password == "p@ss:w/rd%#"
    assert url.port == 5544
    assert url.drivername == "postgresql+psycopg"
    assert s.database_url("other").database == "other"


def test_secrets_are_not_leaked_in_repr(clean_env: pytest.MonkeyPatch) -> None:
    clean_env.setenv("POSTGRES_USER", "admin")
    clean_env.setenv("POSTGRES_PASSWORD", "super-secret-value")
    clean_env.setenv("READONLY_DB_PASSWORD", "another-secret")
    s = Settings(_env_file=None)  # type: ignore[call-arg]
    assert "super-secret-value" not in repr(s)
    assert "another-secret" not in repr(s)
    assert "super-secret-value" not in s.database_url().render_as_string(hide_password=True)


def test_readonly_url_requires_password(clean_env: pytest.MonkeyPatch) -> None:
    clean_env.setenv("POSTGRES_USER", "admin")
    clean_env.setenv("POSTGRES_PASSWORD", "x")
    s = Settings(_env_file=None)  # type: ignore[call-arg]
    with pytest.raises(ValueError, match="READONLY_DB_PASSWORD"):
        s.readonly_database_url()


def test_invalid_values_rejected(clean_env: pytest.MonkeyPatch) -> None:
    clean_env.setenv("POSTGRES_USER", "admin")
    clean_env.setenv("POSTGRES_PASSWORD", "x")
    clean_env.setenv("POSTGRES_PORT", "70000")
    with pytest.raises(ValidationError):
        Settings(_env_file=None)  # type: ignore[call-arg]


def test_env_example_documents_every_setting() -> None:
    documented = set(re.findall(r"^([A-Z_]+)=", (ROOT / ".env.example").read_text(), re.M))
    assert {f.upper() for f in Settings.model_fields} <= documented


def test_env_file_is_git_ignored() -> None:
    patterns = (ROOT / ".gitignore").read_text().splitlines()
    assert ".env" in patterns
    assert "!.env.example" in patterns
