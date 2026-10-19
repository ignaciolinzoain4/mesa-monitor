from datetime import UTC, datetime, time
from zoneinfo import ZoneInfo

import pytest

from mesa_monitor.core.enums import ThresholdScope
from mesa_monitor.core.schedule import (
    OpeningSpan,
    ShiftSpan,
    business_date,
    is_open,
    operating_hours_between,
    shift_of,
)
from mesa_monitor.core.thresholds import (
    DEFAULTS,
    ThresholdRow,
    engine_params,
    resolve,
    validate_value,
)

TZ = ZoneInfo("America/Argentina/Cordoba")  # UTC-3
# viernes (4) abre 08:00 y cierra 02:00 del sábado
FRIDAY = [OpeningSpan(4, time(8), time(2))]


def local(y: int, m: int, d: int, hh: int, mm: int = 0) -> datetime:
    return datetime(y, m, d, hh, mm, tzinfo=TZ)


def test_business_date_after_midnight_counts_for_previous_day() -> None:
    assert business_date(local(2026, 10, 17, 1, 30), TZ).isoformat() == "2026-10-16"
    assert business_date(local(2026, 10, 17, 7, 0), TZ).isoformat() == "2026-10-17"
    assert business_date(datetime(2026, 10, 17, 4, 0, tzinfo=UTC), TZ).isoformat() == "2026-10-16"


@pytest.mark.parametrize(
    ("ts", "expected"),
    [
        (local(2026, 10, 16, 7, 59), False),  # viernes antes de abrir
        (local(2026, 10, 16, 12), True),
        (local(2026, 10, 17, 1, 59), True),  # sábado 01:59 = todavía el viernes
        (local(2026, 10, 17, 2, 0), False),
        (local(2026, 10, 17, 12), False),  # sábado sin horario
    ],
)
def test_is_open_crossing_midnight(ts: datetime, expected: bool) -> None:
    assert is_open(ts, FRIDAY, TZ) is expected


def test_no_hours_means_always_open() -> None:
    assert is_open(local(2026, 10, 17, 4), [], TZ)


def test_operating_hours_between() -> None:
    assert operating_hours_between(local(2026, 10, 16, 0), local(2026, 10, 18, 0), FRIDAY, TZ) == 18


def test_shifts() -> None:
    shifts = [ShiftSpan("mañana", time(8), time(13)), ShiftSpan("noche", time(19), time(2))]
    assert shift_of(local(2026, 10, 16, 9), shifts, TZ) == "mañana"
    assert shift_of(local(2026, 10, 17, 1), shifts, TZ) == "noche"
    assert shift_of(local(2026, 10, 16, 15), shifts, TZ) is None


def row(
    scope: ThresholdScope, ref: str | None, key: str, value: object, **kw: object
) -> ThresholdRow:
    return ThresholdRow(scope, ref, key, value, **kw)  # type: ignore[arg-type]


def test_threshold_resolution_priority() -> None:
    rows = [
        row(ThresholdScope.GLOBAL, None, "alert_sin_atencion_warning_s", 200),
        row(ThresholdScope.SECTOR, "galeria", "alert_sin_atencion_warning_s", 300),
        row(ThresholdScope.TABLE, "t9", "alert_sin_atencion_warning_s", 60),
    ]
    assert resolve(rows)["alert_sin_atencion_warning_s"] == 200
    assert resolve(rows, sector="salon", table_id="t1")["alert_sin_atencion_warning_s"] == 200
    assert resolve(rows, sector="galeria", table_id="t1")["alert_sin_atencion_warning_s"] == 300
    assert resolve(rows, sector="galeria", table_id="t9")["alert_sin_atencion_warning_s"] == 60
    assert resolve([])["vacate_confirm_s"] == DEFAULTS["vacate_confirm_s"] == 120


def test_time_band_override() -> None:
    rows = [
        row(ThresholdScope.GLOBAL, None, "alert_sin_atencion_warning_s", 180),
        row(
            ThresholdScope.GLOBAL,
            None,
            "alert_sin_atencion_warning_s",
            300,
            valid_from=time(21),
            valid_to=time(1),
        ),
    ]
    assert resolve(rows, local_time=time(20))["alert_sin_atencion_warning_s"] == 180
    assert resolve(rows, local_time=time(23))["alert_sin_atencion_warning_s"] == 300
    assert resolve(rows, local_time=time(0, 30))["alert_sin_atencion_warning_s"] == 300


def test_engine_params_from_thresholds() -> None:
    p = engine_params({**DEFAULTS, "occupy_confirm_s": 30})
    assert p.occupy_confirm_s == 30
    assert p.vacate_confirm_s == 120


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("nope", 1),
        ("occupy_confirm_s", -1),
        ("occupy_confirm_s", "x"),
        ("alert_estadia_larga_enabled", 1),
    ],
)
def test_validate_rejects(key: str, value: object) -> None:
    with pytest.raises(ValueError):  # noqa: PT011
        validate_value(key, value)
