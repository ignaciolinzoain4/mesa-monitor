"""Tests del state engine con líneas de tiempo sintéticas (SPEC §8.4 + casos borde)."""

from collections.abc import Iterator
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from itertools import count, pairwise

import pytest

from mesa_monitor.core.enums import CleanMode, SessionCloseReason, TableState
from mesa_monitor.core.state_engine import (
    EngineParams,
    Event,
    Observation,
    SessionClosed,
    SessionOpened,
    StateEngine,
    TableMachine,
    Transition,
    VisitEnded,
    VisitStarted,
)

T0 = datetime(2026, 10, 18, 20, 0, tzinfo=UTC)
FPS = 2.0
L, OC, A, S, V = (
    TableState.LIBRE,
    TableState.OCUPADA_SIN_ATENDER,
    TableState.ATENDIDA,
    TableState.DESOCUPADA_SUCIA,
    TableState.SIN_VISION,
)


@dataclass
class Seg:
    """Tramo de la línea de tiempo: `secs` segundos con estas condiciones constantes."""

    secs: float
    seated: int = 0
    staff: bool = False
    objects: int | None = 0
    healthy: bool = True


def ids() -> "callable[[str], str]":  # type: ignore[valid-type]
    c = count(1)
    return lambda prefix: f"{prefix}_{next(c)}"


class Sim:
    def __init__(
        self,
        params: EngineParams | None = None,
        machine: TableMachine | None = None,
        *,
        fresh: bool = False,
    ) -> None:
        # Por defecto la mesa arranca vacía y vigilada (sin sesiones parciales de arranque).
        params = replace(params or EngineParams(), startup_grace_s=-1) if not fresh else params
        self.m = machine or TableMachine("t1", params or EngineParams(), ids())
        self.t = 0.0
        self.events: list[Event] = []

    def ts(self, t: float | None = None) -> datetime:
        return T0 + timedelta(seconds=self.t if t is None else t)

    def run(self, *segs: Seg) -> "Sim":
        for seg in segs:
            for _ in self._ticks(seg.secs):
                obs = Observation("t1", self.ts(), seg.seated, seg.staff, seg.objects, seg.healthy)
                self.events.extend(self.m.observe(obs))
                self.t += 1 / FPS
        return self

    @staticmethod
    def _ticks(secs: float) -> Iterator[int]:
        return iter(range(round(secs * FPS)))

    def transitions(self) -> list[tuple[TableState | None, TableState]]:
        return [(e.from_state, e.to_state) for e in self.events if isinstance(e, Transition)]

    def of(self, kind: type) -> list:  # type: ignore[type-arg]
        return [e for e in self.events if isinstance(e, kind)]


def at(s: float) -> datetime:
    return T0 + timedelta(seconds=s)


# --------------------------------------------------------------------------- SPEC §8.4


def test_normal_arrival_attention_departure_and_cleaning() -> None:
    sim = Sim().run(
        Seg(30),  # mesa libre
        Seg(60, seated=3),  # se sientan en t=30
        Seg(6, seated=3, staff=True),  # mozo t=90..96
        Seg(600, seated=3),
        Seg(130, seated=0, objects=4),  # se van en t=696 → vacío confirmado a los 120 s
        Seg(6, staff=True, objects=4),  # mozo levanta la mesa
        Seg(20, objects=0),  # mesa limpia sostenida 15 s
    )
    assert sim.transitions() == [(L, OC), (OC, A), (A, S), (S, L)]
    (opened,) = sim.of(SessionOpened)
    assert opened.seated_at == at(30)  # inicio de la ventana, no el final
    visits = sim.of(VisitStarted)
    assert [v.started_at for v in visits] == [at(90), at(826)]
    assert sim.m.state is L
    (closed,) = sim.of(SessionClosed)
    assert closed.reason is SessionCloseReason.CLEANED
    assert closed.cleaned_at is not None
    # la confirmación arranca cuando ya no hay objetos (t=832) y dura 15 s
    assert at(832 + 15) <= closed.cleaned_at <= at(832 + 16)
    attended = next(e for e in sim.of(Transition) if e.to_state is A)
    assert attended.confidence == pytest.approx(1.0)


def test_customer_goes_to_bathroom_table_stays_occupied() -> None:
    sim = Sim().run(Seg(30, seated=1), Seg(90, seated=0), Seg(60, seated=1))
    assert sim.transitions() == [(L, OC)]
    assert sim.m.session is not None
    assert sim.m.session.vacated_at is None


def test_one_person_staying_keeps_table_occupied() -> None:
    sim = Sim().run(Seg(30, seated=2), Seg(400, seated=1))
    assert sim.transitions() == [(L, OC)]
    assert sim.m.session is not None
    assert sim.m.session.max_people == 2


def test_waiter_passing_by_does_not_count() -> None:
    sim = Sim().run(Seg(30, seated=2), Seg(3, seated=2, staff=True), Seg(60, seated=2))
    assert sim.transitions() == [(L, OC)]
    assert sim.of(VisitStarted) == []


def test_waiter_chatting_counts_once_and_long_presence_is_capped() -> None:
    sim = Sim().run(Seg(30, seated=2), Seg(700, seated=2, staff=True), Seg(10, seated=2))
    assert sim.transitions() == [(L, OC), (OC, A)]
    (visit,) = sim.of(VisitStarted)
    (end,) = sim.of(VisitEnded)
    assert visit.started_at == at(30)
    assert end.ended_at == at(30 + 600)  # cerrada en visit_max_s


def test_table_reoccupied_before_cleaning() -> None:
    sim = Sim().run(
        Seg(30, seated=2),
        Seg(130, seated=0, objects=3),  # se van → sucia
        Seg(30, seated=4, objects=3),  # llegan otros antes de que pase el mozo
    )
    assert sim.transitions() == [(L, OC), (OC, S), (S, OC)]
    first, second = sim.of(SessionOpened)
    (closed,) = sim.of(SessionClosed)
    assert closed.session_id == first.session_id
    assert closed.reason is SessionCloseReason.REOCCUPIED
    assert closed.cleaned_at is None
    assert sim.m.session is not None
    assert sim.m.session.id == second.session_id


def test_camera_down_mid_session_pauses_and_resumes() -> None:
    sim = Sim().run(Seg(30, seated=2), Seg(60, seated=2))
    since_before = sim.m.state_since
    sim.run(Seg(60, healthy=False), Seg(10, seated=2))
    assert sim.transitions() == [(L, OC), (OC, V), (V, OC)]
    assert sim.m.state is OC
    assert sim.m.state_since == since_before  # el contador sigue desde que se sentaron
    assert sim.m.paused_s == pytest.approx(60, abs=1)  # pero descuenta el tiempo sin visión
    assert sim.m.session is not None
    assert sim.m.session.vision_lost_seconds == pytest.approx(60, abs=1)
    assert sim.of(SessionClosed) == []  # la sesión no se cierra


def test_long_vision_loss_closes_session() -> None:
    sim = Sim().run(Seg(30, seated=2), Seg(16 * 60, healthy=False), Seg(10, seated=0))
    (closed,) = sim.of(SessionClosed)
    assert closed.reason is SessionCloseReason.VISION_LOST
    assert sim.transitions() == [(L, OC), (OC, V), (V, L)]
    assert sim.m.session is None


def test_short_glitch_does_not_trigger_sin_vision() -> None:
    sim = Sim().run(Seg(30, seated=2), Seg(3, healthy=False), Seg(10, seated=2))
    assert sim.transitions() == [(L, OC)]


def test_worker_restart_mid_session_restores_from_snapshot() -> None:
    sim = Sim().run(Seg(30, seated=2), Seg(40, seated=2))
    snap = sim.m.snapshot()
    assert snap.state is OC
    # el worker se reinicia: máquina nueva desde lo persistido
    restored = TableMachine.restore(snap, EngineParams(), ids())
    sim2 = Sim(machine=restored)
    sim2.t = sim.t + 20  # 20 s caído
    sim2.run(Seg(10, seated=2, staff=True))
    assert sim2.transitions() == [(OC, A)]
    (visit,) = sim2.of(VisitStarted)
    assert visit.session_id == snap.session_id  # misma sesión
    assert restored.session is not None
    assert restored.session.seated_at == at(0)


def test_closing_time_closes_open_sessions() -> None:
    engine = StateEngine(ids=ids())
    for s in range(70):
        engine.observe(Observation("t1", at(s), 2, False, 0))
        engine.observe(Observation("t2", at(s), 0, False, 0))
    events = engine.close_all(at(100), SessionCloseReason.CLOSING_TIME)
    closed = [e for e in events if isinstance(e, SessionClosed)]
    assert [(c.table_id, c.reason) for c in closed] == [("t1", SessionCloseReason.CLOSING_TIME)]
    assert engine.machines["t1"].state is L
    assert engine.machines["t2"].state is L


# --------------------------------------------------------------------------- casos borde


def test_occupancy_tolerates_detection_flicker() -> None:
    segs = []
    for _ in range(8):  # sentados con huecos de 2 s de no-detección
        segs += [Seg(3, seated=2), Seg(2, seated=0)]
    sim = Sim().run(*segs)
    assert sim.transitions() == [(L, OC)]
    assert sim.of(SessionOpened)[0].seated_at == at(0)


def test_brief_false_seated_does_not_reset_vacate_timer() -> None:
    sim = Sim().run(
        Seg(30, seated=2),
        Seg(60, seated=0),
        Seg(2, seated=1),  # detección falsa breve (< reseat_confirm_s)
        Seg(70, seated=0),
    )
    assert sim.transitions() == [(L, OC), (OC, S)]
    session_vacated = sim.of(SessionOpened)[0]
    assert session_vacated.seated_at == at(0)
    assert sim.m.session is not None
    assert sim.m.session.vacated_at == at(30)


def test_short_occupancy_is_not_a_session() -> None:
    sim = Sim().run(Seg(10, seated=1), Seg(60, seated=0))
    assert sim.transitions() == []


def test_waiter_arriving_while_occupancy_is_confirming_counts_as_attention() -> None:
    sim = Sim().run(Seg(8, seated=2), Seg(6, seated=2, staff=True), Seg(20, seated=2))
    assert sim.transitions() == [(L, OC), (OC, A)]
    (visit,) = sim.of(VisitStarted)
    assert visit.started_at == at(8)
    assert sim.m.session is not None
    assert sim.m.session.first_attention_at == at(8)


def test_cleaning_requires_a_staff_visit() -> None:
    sim = Sim().run(Seg(30, seated=2), Seg(130, seated=0, objects=0), Seg(120, objects=0))
    assert sim.m.state is S  # sin objetos pero sin visita: sigue "sucia"


def test_cleaning_requires_objects_gone() -> None:
    sim = Sim().run(
        Seg(30, seated=2),
        Seg(130, seated=0, objects=3),
        Seg(6, staff=True, objects=3),
        Seg(60, objects=3),
    )
    assert sim.m.state is S


def test_staff_visit_only_mode_cleans_when_waiter_leaves() -> None:
    params = EngineParams(clean_mode=CleanMode.STAFF_VISIT_ONLY)
    sim = Sim(params).run(
        Seg(30, seated=2),
        Seg(130, seated=0, objects=5),
        Seg(6, staff=True, objects=5),
        Seg(5, objects=5),
    )
    assert sim.transitions()[-1] == (S, L)
    (closed,) = sim.of(SessionClosed)
    assert closed.cleaned_at == at(30 + 130 + 5.5)  # fin de la visita


def test_no_object_detection_falls_back_to_staff_visit() -> None:
    sim = Sim().run(
        Seg(30, seated=2, objects=None),
        Seg(130, seated=0, objects=None),
        Seg(6, staff=True, objects=None),
        Seg(5, objects=None),
    )
    assert sim.m.state is L


def test_visit_on_free_table_is_ignored() -> None:
    sim = Sim().run(Seg(30, staff=True), Seg(30))
    assert sim.events == []


def test_multiple_visits_update_last_visit() -> None:
    sim = Sim().run(
        Seg(30, seated=2),
        Seg(6, seated=2, staff=True),
        Seg(300, seated=2),
        Seg(6, seated=2, staff=True),
        Seg(10, seated=2),
    )
    assert len(sim.of(VisitStarted)) == 2
    assert sim.m.session is not None
    assert sim.m.session.last_visit_at == at(336)
    assert sim.transitions() == [(L, OC), (OC, A)]


def test_disable_and_enable_table() -> None:
    sim = Sim().run(Seg(30, seated=2))
    events = sim.m.set_enabled(False, at(100))
    assert any(
        isinstance(e, SessionClosed) and e.reason is SessionCloseReason.DISABLED for e in events
    )
    assert sim.m.state is TableState.DESHABILITADA
    assert sim.m.observe(Observation("t1", at(101), 3, False, 0)) == []
    sim.m.set_enabled(True, at(200))
    assert sim.m.state is L


def test_params_update_in_place() -> None:
    sim = Sim().run(Seg(10, seated=2))
    sim.m.update_params(EngineParams(occupy_confirm_s=5))
    sim.run(Seg(1, seated=2))
    assert sim.transitions() == [(L, OC)]


@pytest.mark.parametrize("seed", range(20))
def test_random_timelines_keep_invariants(seed: int) -> None:
    import random

    rng = random.Random(seed)  # noqa: S311  (no es criptografía)
    sim = Sim(EngineParams(vision_loss_close_s=120))
    for _ in range(60):
        sim.run(
            Seg(
                rng.choice([1, 3, 10, 30, 90, 150]),
                seated=rng.choice([0, 0, 1, 3]),
                staff=rng.random() < 0.3,
                objects=rng.choice([0, 0, 2, None]),
                healthy=rng.random() > 0.08,
            )
        )
        m = sim.m
        has_session = m.session is not None
        if m.state in (OC, A, S):
            assert has_session, m.state
        if m.state is L:
            assert not has_session
        if m.state is V:
            assert m.resume_state is not None
    # cada transición parte del estado en que quedó la anterior
    trans = sim.of(Transition)
    for prev, cur in pairwise(trans):
        assert cur.from_state is prev.to_state
    # sesiones: cada apertura se cierra a lo sumo una vez, nunca se cierra algo no abierto
    opened = {e.session_id for e in sim.of(SessionOpened)}
    closed = [e.session_id for e in sim.of(SessionClosed)]
    assert len(closed) == len(set(closed))
    assert set(closed) <= opened
    # visitas: cada una termina a lo sumo una vez y pertenece a una sesión abierta
    started = {e.visit_id for e in sim.of(VisitStarted)}
    ended = [e.visit_id for e in sim.of(VisitEnded)]
    assert len(ended) == len(set(ended))
    assert set(ended) <= started


# --------------------------------------------------------------------------- consumo y arranque

C = TableState.CONSUMIENDO


def test_food_on_table_moves_attended_table_to_consuming() -> None:
    sim = Sim().run(
        Seg(10),
        Seg(30, seated=2),
        Seg(6, seated=2, staff=True),  # toma el pedido
        Seg(60, seated=2),
        Seg(30, seated=2, objects=3),  # llega la comida
    )
    assert sim.transitions() == [(L, OC), (OC, A), (A, C)]
    assert sim.m.session is not None
    assert sim.m.session.served_at == at(106)
    assert sim.m.session.first_attention_at == at(40)  # la visita, no la comida


def test_food_without_detected_visit_counts_as_attention() -> None:
    sim = Sim().run(Seg(10), Seg(60, seated=2), Seg(30, seated=2, objects=2))
    assert sim.transitions() == [(L, OC), (OC, C)]
    assert sim.m.session is not None
    assert sim.m.session.first_attention_at == at(70)
    reasons = [e.reason for e in sim.of(Transition)]
    assert reasons[-1] == "food_served_without_visit"


def test_flickering_object_does_not_mean_food() -> None:
    sim = Sim().run(Seg(10), Seg(30, seated=2))
    for _ in range(20):  # aparece 1 s cada 6 s
        sim.run(Seg(1, seated=2, objects=1), Seg(5, seated=2))
    assert sim.transitions() == [(L, OC)]


def test_leftovers_from_previous_customers_are_not_consumption() -> None:
    sim = Sim().run(
        Seg(10),
        Seg(30, seated=2),
        Seg(130, seated=0, objects=3),  # se van y dejan la mesa sucia
        Seg(60, seated=2, objects=3),  # se sientan otros con la mesa sin levantar
    )
    assert sim.transitions() == [(L, OC), (OC, S), (S, OC)]
    sim.run(Seg(20, seated=2, objects=5))  # les traen lo suyo
    assert sim.m.state is C


def test_table_already_eating_at_startup_starts_consuming() -> None:
    sim = Sim(fresh=True).run(Seg(30, seated=3, objects=4))
    assert sim.transitions() == [(L, C)]
    (opened,) = sim.of(SessionOpened)
    assert opened.partial
    assert sim.m.session is not None
    assert sim.m.session.first_attention_at is None  # no se sabe: no entra en las métricas
    assert [e.reason for e in sim.of(Transition)] == ["initial_state"]


def test_table_occupied_at_startup_without_food_is_partial_unattended() -> None:
    sim = Sim(fresh=True).run(Seg(30, seated=2))
    assert sim.transitions() == [(L, OC)]
    assert sim.m.session is not None
    assert sim.m.session.partial
    # La comida que llega después no inventa un tiempo de espera para una sesión parcial.
    sim.run(Seg(20, seated=2, objects=2))
    assert sim.m.state is C
    assert sim.m.session.first_attention_at is None


def test_customers_arriving_after_startup_are_a_normal_session() -> None:
    sim = Sim(fresh=True).run(Seg(30), Seg(30, seated=2))
    (opened,) = sim.of(SessionOpened)
    assert not opened.partial


def test_customers_seen_standing_keep_the_table_occupied() -> None:
    sim = Sim().run(Seg(10), Seg(30, seated=2))
    for _ in range(round(300 * FPS)):  # la cámara los ve "parados" (postura dudosa)
        obs = Observation("t1", sim.ts(), 0, False, 0, present=2)
        sim.events.extend(sim.m.observe(obs))
        sim.t += 1 / FPS
    assert sim.transitions() == [(L, OC)]


def test_group_size_ignores_one_frame_duplicates() -> None:
    sim = Sim().run(Seg(10), Seg(30, seated=2))
    for i in range(round(60 * FPS)):
        n = 3 if i % 5 == 0 else 2  # una caja duplicada cada tanto
        obs = Observation("t1", sim.ts(), n, False, 0)
        sim.events.extend(sim.m.observe(obs))
        sim.t += 1 / FPS
    assert sim.m.session is not None
    assert sim.m.session.max_people == 2
    assert sim.m.snapshot().people_now == 2


def test_flickering_food_at_startup_still_means_consuming() -> None:
    sim = Sim(fresh=True)
    for _ in range(10):  # los platos se detectan 1 de cada 2 segundos
        sim.run(Seg(1, seated=3, objects=2), Seg(2, seated=3, objects=0))
    assert sim.transitions() == [(L, C)]
