from datetime import UTC, datetime, timedelta

from mesa_monitor.core.alerts import TableAlertInput, camera_alert, table_alerts
from mesa_monitor.core.enums import AlertType, Severity, TableState
from mesa_monitor.core.thresholds import DEFAULTS

T0 = datetime(2026, 10, 18, 21, 0, tzinfo=UTC)


def inp(state: TableState, **kw: object) -> TableAlertInput:
    base: dict[str, object] = {
        "table_id": "t1",
        "state": state,
        "state_since": T0,
        "paused_s": 0.0,
        "seated_at": T0,
        "first_attention_at": None,
        "last_visit_at": None,
    }
    base.update(kw)
    return TableAlertInput(**base)  # type: ignore[arg-type]


def at(s: float) -> datetime:
    return T0 + timedelta(seconds=s)


def kinds(alerts: list) -> list[tuple[AlertType, Severity]]:  # type: ignore[type-arg]
    return [(a.alert_type, a.severity) for a in alerts]


def test_sin_atencion_warning_then_critical() -> None:
    t = inp(TableState.OCUPADA_SIN_ATENDER)
    assert table_alerts(t, DEFAULTS, at(179)) == []
    assert kinds(table_alerts(t, DEFAULTS, at(180))) == [(AlertType.SIN_ATENCION, Severity.WARNING)]
    assert kinds(table_alerts(t, DEFAULTS, at(360))) == [
        (AlertType.SIN_ATENCION, Severity.CRITICAL)
    ]


def test_vision_pause_is_discounted() -> None:
    t = inp(TableState.OCUPADA_SIN_ATENDER, paused_s=100)
    assert table_alerts(t, DEFAULTS, at(250)) == []  # 250 − 100 = 150 < 180


def test_sin_visita_counts_from_last_visit() -> None:
    t = inp(TableState.ATENDIDA, first_attention_at=at(60), last_visit_at=at(600))
    assert table_alerts(t, DEFAULTS, at(600 + 899)) == []
    (a,) = table_alerts(t, DEFAULTS, at(600 + 900))
    assert a.alert_type is AlertType.SIN_VISITA
    assert a.since == at(600)


def test_mesa_sucia() -> None:
    t = inp(TableState.DESOCUPADA_SUCIA)
    assert kinds(table_alerts(t, DEFAULTS, at(300))) == [(AlertType.MESA_SUCIA, Severity.WARNING)]


def test_no_alerts_without_vision_or_when_free() -> None:
    for s in (TableState.SIN_VISION, TableState.LIBRE, TableState.DESHABILITADA):
        assert table_alerts(inp(s), DEFAULTS, at(10_000)) == []


def test_estadia_larga_only_when_enabled() -> None:
    t = inp(TableState.ATENDIDA, first_attention_at=at(10), last_visit_at=at(7100))
    assert table_alerts(t, DEFAULTS, at(7300)) == []
    values = {**DEFAULTS, "alert_estadia_larga_enabled": True}
    assert kinds(table_alerts(t, values, at(7300))) == [(AlertType.ESTADIA_LARGA, Severity.INFO)]


def test_camera_down_after_threshold() -> None:
    assert camera_alert("cam1", None, DEFAULTS, at(100)) is None
    assert camera_alert("cam1", at(0), DEFAULTS, at(29)) is None
    a = camera_alert("cam1", at(0), DEFAULTS, at(30))
    assert a is not None
    assert (a.alert_type, a.severity, a.camera_id) == (
        AlertType.CAMARA_CAIDA,
        Severity.CRITICAL,
        "cam1",
    )
