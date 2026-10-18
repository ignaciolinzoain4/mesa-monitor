import pytest
from pydantic import ValidationError

from mesa_monitor.config import Settings
from mesa_monitor.core.ids import new_id, ulid


def test_ulid_format_and_time_order() -> None:
    a = ulid(1_000)
    b = ulid(2_000)
    assert len(a) == 26
    assert a < b


def test_new_id_has_prefix() -> None:
    assert new_id("alr").startswith("alr_")


def test_cors_origins_from_comma_separated_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CORS_ORIGINS", "http://a.local, http://b.local,")
    assert Settings(_env_file=None).cors_origins == [  # type: ignore[call-arg]
        "http://a.local",
        "http://b.local",
    ]


def test_invalid_timezone_is_rejected() -> None:
    with pytest.raises(ValidationError):
        Settings(timezone="Marte/Olympus", _env_file=None)  # type: ignore[call-arg]
