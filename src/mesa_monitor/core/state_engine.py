"""Máquina de estados de mesa (SPEC §8). Pura: sin IO, sin visión, sin reloj propio.

Recibe observaciones con timestamp (una por mesa y por tick del pipeline) y devuelve eventos:
transiciones, apertura/cierre de sesiones y visitas del personal. La persistencia la hace otra
capa (`vision.persistence`), que también reconstruye el estado tras un reinicio (`restore`).

Reglas principales (todas con antirrebote configurable en `EngineParams`):

- LIBRE → OCUPADA_SIN_ATENDER: ≥ 1 cliente sentado durante `occupy_confirm_s`.
  `seated_at` = inicio de esa ventana.
- OCUPADA_SIN_ATENDER → ATENDIDA: visita de personal (presencia sostenida ≥ `visit_min_s`).
  `first_attention_at` = inicio de la visita. Las pasadas más cortas no cuentan.
  La visita se confirma al cumplir `visit_min_s` (no al terminar) para que el cambio se vea en
  segundos. Si la presencia supera `visit_max_s`, la visita se cierra en inicio + `visit_max_s`
  y no se cuenta otra hasta que la persona se vaya.
- OCUPADA_* → DESOCUPADA_SUCIA: 0 sentados durante `vacate_confirm_s` (tolera ir al baño).
  Detecciones sueltas de "sentado" más cortas que `reseat_confirm_s` no cortan la cuenta.
  `vacated_at` = inicio del vacío.
- DESOCUPADA_SUCIA → LIBRE: visita de personal posterior al vacío **y** objetos ≤
  `clean_objects_max` sostenido `clean_confirm_s` (modo `objects_and_staff`); en modo
  `staff_visit_only` (o sin detección de objetos), al terminar esa visita. `cleaned_at` = fin de la
  confirmación.
- DESOCUPADA_SUCIA → OCUPADA_SIN_ATENDER: llegan clientes antes de limpiar: se cierra la sesión
  (`cleaned_at` = NULL, motivo `reoccupied`) y se abre otra.
- Cualquier estado → SIN_VISION (vista no sana sostenida `vision_loss_confirm_s`) y vuelta al
  estado anterior (vista sana sostenida `vision_recover_s`): la sesión sigue y los contadores de
  alerta se pausan (`paused_s`). Si la falta de visión supera `vision_loss_close_s`, la sesión se
  cierra con motivo `vision_lost` y al volver la mesa arranca LIBRE.

Cumplimiento (SPEC Anexo A): la presencia de personal es un booleano; no se identifica quién.
"""

from collections.abc import Callable
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta

from mesa_monitor.core.enums import CleanMode, SessionCloseReason, TableState
from mesa_monitor.core.ids import new_id
from mesa_monitor.core.sustain import Interval, Sustain

OCCUPIED = (TableState.OCUPADA_SIN_ATENDER, TableState.ATENDIDA)


@dataclass(frozen=True)
class EngineParams:
    occupy_confirm_s: float = 20
    seat_gap_s: float = 4  # huecos tolerados en la detección de sentados al ocupar
    vacate_confirm_s: float = 120
    reseat_confirm_s: float = 6  # "sentado" debe sostenerse esto para cortar el vaciado
    visit_min_s: float = 4
    visit_max_s: float = 600
    visit_gap_s: float = 2
    clean_confirm_s: float = 15
    clean_objects_max: int = 0
    clean_mode: CleanMode = CleanMode.OBJECTS_AND_STAFF
    vision_loss_confirm_s: float = 5
    vision_recover_s: float = 3
    vision_loss_close_s: float = 15 * 60


@dataclass(frozen=True)
class Observation:
    table_id: str
    ts: datetime
    seated_customers: int
    staff_present: bool  # personal parado dentro del polígono de asientos ampliado
    object_count: int | None  # None = la vista no detecta objetos de forma confiable
    view_healthy: bool = True


# --------------------------------------------------------------------------- eventos


@dataclass(frozen=True)
class Transition:
    table_id: str
    ts: datetime
    from_state: TableState | None
    to_state: TableState
    reason: str
    session_id: str | None
    confidence: float | None = None


@dataclass(frozen=True)
class SessionOpened:
    table_id: str
    session_id: str
    seated_at: datetime


@dataclass(frozen=True)
class SessionUpdated:
    table_id: str
    session_id: str
    first_attention_at: datetime | None
    vacated_at: datetime | None
    max_people: int
    vision_lost_seconds: float


@dataclass(frozen=True)
class SessionClosed:
    table_id: str
    session_id: str
    closed_at: datetime
    reason: SessionCloseReason
    cleaned_at: datetime | None


@dataclass(frozen=True)
class VisitStarted:
    table_id: str
    session_id: str
    visit_id: str
    started_at: datetime
    confidence: float


@dataclass(frozen=True)
class VisitEnded:
    table_id: str
    session_id: str
    visit_id: str
    ended_at: datetime


Event = Transition | SessionOpened | SessionUpdated | SessionClosed | VisitStarted | VisitEnded


# --------------------------------------------------------------------------- estado


@dataclass
class SessionState:
    id: str
    seated_at: datetime
    first_attention_at: datetime | None = None
    vacated_at: datetime | None = None
    last_visit_at: datetime | None = None
    max_people: int = 0
    vision_lost_seconds: float = 0.0
    visits: int = 0


@dataclass
class VisitState:
    id: str | None  # None mientras no se confirma (o si no hay sesión a la que asignarla)
    started_at: datetime
    closed: bool = False  # cerrada por visit_max_s (la presencia sigue)


@dataclass(frozen=True)
class TableSnapshot:
    """Estado vigente de una mesa: lo que se persiste en `table_state` y expone la API."""

    table_id: str
    state: TableState
    state_since: datetime | None
    paused_s: float  # segundos sin visión acumulados en el estado actual
    resume_state: TableState | None
    vision_lost_since: datetime | None
    session_id: str | None
    seated_at: datetime | None
    first_attention_at: datetime | None
    vacated_at: datetime | None
    last_visit_at: datetime | None
    max_people: int
    vision_lost_seconds: float
    people_now: int
    staff_present: bool
    updated_at: datetime | None


IdFactory = Callable[[str], str]


class TableMachine:
    def __init__(self, table_id: str, params: EngineParams, ids: IdFactory = new_id) -> None:
        self.table_id = table_id
        self.params = params
        self._ids = ids
        self.state = TableState.LIBRE
        self.state_since: datetime | None = None
        self.paused_s = 0.0
        self.resume_state: TableState | None = None
        self.vision_lost_since: datetime | None = None
        self.session: SessionState | None = None
        self.visit: VisitState | None = None
        self.people_now = 0
        self.staff_now = False
        self.updated_at: datetime | None = None
        self._cleaning_from: datetime | None = None  # inicio de la visita de limpieza válida
        # Visita (inicio, fin o None si sigue) ocurrida mientras se confirmaba la ocupación.
        self._unassigned_visit: tuple[datetime, datetime | None] | None = None
        self._last_objects: int | None = 0
        self._seated = Sustain(params.seat_gap_s)
        self._empty = Sustain(params.reseat_confirm_s)
        self._staff = Sustain(params.visit_gap_s)
        self._clean = Sustain(0)
        self._vision_bad = Sustain(0)
        self._vision_good = Sustain(0)

    # ----------------------------------------------------------------- API pública

    def observe(self, obs: Observation) -> list[Event]:
        events: list[Event] = []
        self.updated_at = obs.ts
        if self.state is TableState.DESHABILITADA:
            return events
        if self._handle_vision(obs, events):
            return events
        self.people_now = obs.seated_customers
        self.staff_now = obs.staff_present
        self._last_objects = obs.object_count
        self._handle_staff(obs, events)
        self._handle_occupancy(obs, events)
        return events

    def close_session(self, ts: datetime, reason: SessionCloseReason) -> list[Event]:
        """Cierre forzado (horario de cierre). La mesa queda LIBRE."""
        events: list[Event] = []
        self._end_visit(ts, events)
        if self.session is not None:
            self._close(ts, reason, None, events)
        target = TableState.LIBRE
        if self.state is not target:
            self._transition(ts, target, reason.value, events)
        self._reset_detectors()
        self.resume_state = None
        self.vision_lost_since = None
        return events

    def set_enabled(self, enabled: bool, ts: datetime) -> list[Event]:
        events: list[Event] = []
        if not enabled and self.state is not TableState.DESHABILITADA:
            self._end_visit(ts, events)
            if self.session is not None:
                self._close(ts, SessionCloseReason.DISABLED, None, events)
            self._transition(ts, TableState.DESHABILITADA, "disabled", events)
            self._reset_detectors()
        elif enabled and self.state is TableState.DESHABILITADA:
            self._transition(ts, TableState.LIBRE, "enabled", events)
        return events

    def update_params(self, params: EngineParams) -> None:
        """Aplica umbrales nuevos en caliente sin perder el estado."""
        self.params = params
        self._seated.gap_s = params.seat_gap_s
        self._empty.gap_s = params.reseat_confirm_s
        self._staff.gap_s = params.visit_gap_s

    def snapshot(self) -> TableSnapshot:
        s = self.session
        return TableSnapshot(
            table_id=self.table_id,
            state=self.state,
            state_since=self.state_since,
            paused_s=self.paused_s,
            resume_state=self.resume_state,
            vision_lost_since=self.vision_lost_since,
            session_id=s.id if s else None,
            seated_at=s.seated_at if s else None,
            first_attention_at=s.first_attention_at if s else None,
            vacated_at=s.vacated_at if s else None,
            last_visit_at=s.last_visit_at if s else None,
            max_people=s.max_people if s else 0,
            vision_lost_seconds=s.vision_lost_seconds if s else 0.0,
            people_now=self.people_now,
            staff_present=self.staff_now,
            updated_at=self.updated_at,
        )

    @classmethod
    def restore(
        cls, snap: TableSnapshot, params: EngineParams, ids: IdFactory = new_id
    ) -> "TableMachine":
        """Reconstruye la máquina desde el estado persistido (reinicio del worker).

        Los antirrebotes en curso se pierden (se vuelven a medir); las sesiones no.
        """
        m = cls(snap.table_id, params, ids)
        m.state = snap.state
        m.state_since = snap.state_since
        m.paused_s = snap.paused_s
        m.resume_state = snap.resume_state
        m.vision_lost_since = snap.vision_lost_since
        m.updated_at = snap.updated_at
        if snap.session_id and snap.seated_at:
            m.session = SessionState(
                id=snap.session_id,
                seated_at=snap.seated_at,
                first_attention_at=snap.first_attention_at,
                vacated_at=snap.vacated_at,
                last_visit_at=snap.last_visit_at,
                max_people=snap.max_people,
                vision_lost_seconds=snap.vision_lost_seconds,
            )
        if m.state is TableState.DESOCUPADA_SUCIA and m.session and m.session.vacated_at:
            # Una visita de limpieza ya registrada se respeta tras el reinicio.
            lv = m.session.last_visit_at
            if lv and lv >= m.session.vacated_at:
                m._cleaning_from = lv
        return m

    # ----------------------------------------------------------------- visión

    def _handle_vision(self, obs: Observation, events: list[Event]) -> bool:
        """Devuelve True si la observación no se debe procesar más (sin visión)."""
        p = self.params
        bad = not obs.view_healthy
        self._vision_bad.update(bad, obs.ts)
        self._vision_good.update(not bad, obs.ts)

        if self.state is not TableState.SIN_VISION:
            if not bad:
                return False
            if not self._vision_bad.held(p.vision_loss_confirm_s):
                return True  # dato no confiable: no se usa, pero todavía no es "sin visión"
            since = self._vision_bad.start or obs.ts
            self._end_visit(self._staff.last_true or since, events)
            self.resume_state = self.state
            self.vision_lost_since = since
            prev_since, prev_paused = self.state_since, self.paused_s
            self._transition(obs.ts, TableState.SIN_VISION, "vision_lost", events, since=since)
            # Al volver se retoma el estado anterior con su `state_since` original.
            self._resume_since, self._resume_paused = prev_since, prev_paused
            self._reset_detectors()
            return True

        # En SIN_VISION
        lost_since = self.vision_lost_since or obs.ts
        if bad:
            lost_for = (obs.ts - lost_since).total_seconds()
            if self.session is not None and lost_for >= p.vision_loss_close_s:
                self.session.vision_lost_seconds += lost_for
                self._close(obs.ts, SessionCloseReason.VISION_LOST, None, events)
                self.resume_state = TableState.LIBRE
                self._resume_since, self._resume_paused = None, 0.0
                self.vision_lost_since = obs.ts  # lo que siga no pertenece a ninguna sesión
            return True
        if not self._vision_good.held(p.vision_recover_s):
            return True
        recovered_at = self._vision_good.start or obs.ts
        lost = max(0.0, (recovered_at - lost_since).total_seconds())
        target = self.resume_state or TableState.LIBRE
        if self.session is not None:
            self.session.vision_lost_seconds += lost
            events.append(self._session_updated())
        restored_since = getattr(self, "_resume_since", None)
        restored_paused = getattr(self, "_resume_paused", 0.0)
        self._transition(obs.ts, target, "vision_recovered", events)
        if restored_since is not None:
            self.state_since = restored_since
            self.paused_s = restored_paused + lost
        else:
            self.state_since = obs.ts
            self.paused_s = 0.0
        self.resume_state = None
        self.vision_lost_since = None
        return False

    # ----------------------------------------------------------------- personal

    def _handle_staff(self, obs: Observation, events: list[Event]) -> None:
        p = self.params
        ended = self._staff.update(obs.staff_present, obs.ts)
        if ended is not None:
            self._on_presence_end(ended, events)
            return
        if not self._staff.active or self._staff.start is None:
            return
        start = self._staff.start
        if self.visit is None and self._staff.held(p.visit_min_s):
            self.visit = VisitState(id=None, started_at=start)
            self._on_visit_confirmed(start, events)
        if (
            self.visit is not None
            and not self.visit.closed
            and self._staff.held_for() > p.visit_max_s
        ):
            end = start + timedelta(seconds=p.visit_max_s)
            self._emit_visit_end(end, events)
            self.visit.closed = True

    def _on_visit_confirmed(self, start: datetime, events: list[Event]) -> None:
        assert self.visit is not None
        s = self.session
        if s is None or self.state not in (*OCCUPIED, TableState.DESOCUPADA_SUCIA):
            # Mesa libre: puede ser que se estén sentando (ocupación aún sin confirmar).
            self._unassigned_visit = (start, None)
            return
        if self.state is TableState.DESOCUPADA_SUCIA:
            assert s.vacated_at is not None
            if self._staff.last_true is not None and self._staff.last_true >= s.vacated_at:
                self._cleaning_from = max(start, s.vacated_at)
                self._clean.reset()
        self._register_visit(start, events)
        if self.state is TableState.OCUPADA_SIN_ATENDER:
            s.first_attention_at = start
            events.append(self._session_updated())
            self._transition(
                start if start > (self.state_since or start) else self.updated_at or start,
                TableState.ATENDIDA,
                "staff_visit",
                events,
                confidence=self._staff.ratio,
            )

    def _register_visit(self, start: datetime, events: list[Event]) -> None:
        assert self.visit is not None
        assert self.session is not None
        self.visit.id = self._ids("vis")
        self.session.visits += 1
        self.session.last_visit_at = start
        events.append(
            VisitStarted(
                self.table_id,
                self.session.id,
                self.visit.id,
                start,
                round(self._staff.ratio, 3),
            )
        )

    def _on_presence_end(self, interval: Interval, events: list[Event]) -> None:
        visit = self.visit
        self.visit = None
        if visit is None:
            return
        if visit.id is None and self._unassigned_visit is not None:
            self._unassigned_visit = (self._unassigned_visit[0], interval.end)
        if visit.id is not None and not visit.closed and self.session is not None:
            events.append(VisitEnded(self.table_id, self.session.id, visit.id, interval.end))
        if (
            self.state is TableState.DESOCUPADA_SUCIA
            and self._cleaning_from is not None
            and self._staff_only_cleaning(objects=self._last_objects)
        ):
            self._finish_cleaning(interval.end, events)

    def _emit_visit_end(self, ts: datetime, events: list[Event]) -> None:
        if self.visit and self.visit.id and self.session and not self.visit.closed:
            events.append(VisitEnded(self.table_id, self.session.id, self.visit.id, ts))

    def _end_visit(self, ts: datetime, events: list[Event]) -> None:
        if self.visit is not None:
            self._emit_visit_end(ts, events)
            self.visit = None
        self._staff.reset()

    # ----------------------------------------------------------------- ocupación

    def _handle_occupancy(self, obs: Observation, events: list[Event]) -> None:
        p = self.params
        seated = obs.seated_customers > 0
        self._seated.update(seated, obs.ts)

        if self.state is TableState.LIBRE:
            self._empty.reset()
            if self._seated.held(p.occupy_confirm_s):
                self._open_session(obs, events, reason="customers_seated")
            elif not self._seated.active:
                self._unassigned_visit = None
            return

        if self.state in OCCUPIED:
            s = self.session
            assert s is not None
            if obs.seated_customers > s.max_people:
                s.max_people = obs.seated_customers
                events.append(self._session_updated())
            self._empty.update(not seated, obs.ts)
            # Un "sentado" sostenido corta el vaciado; los parpadeos cortos no.
            if seated and self._seated.held(p.reseat_confirm_s):
                self._empty.reset()
            if (
                self._empty.active
                and (obs.ts - (self._empty.start or obs.ts)).total_seconds() >= p.vacate_confirm_s
            ):
                vacated_at = self._empty.start or obs.ts
                s.vacated_at = vacated_at
                events.append(self._session_updated())
                self._transition(
                    obs.ts,
                    TableState.DESOCUPADA_SUCIA,
                    "customers_left",
                    events,
                    since=vacated_at,
                    confidence=self._empty.ratio,
                )
                self._seated.reset()
                self._empty.reset()
                self._clean.reset()
                self._cleaning_from = None
                # Si el mozo ya estaba en la mesa cuando se vació, esa visita cuenta.
                if (
                    self.visit is not None
                    and self._staff.last_true
                    and self._staff.last_true >= vacated_at
                ):
                    self._cleaning_from = max(self.visit.started_at, vacated_at)
            return

        if self.state is TableState.DESOCUPADA_SUCIA:
            if self._seated.held(p.occupy_confirm_s):
                assert self.session is not None
                self._end_visit(obs.ts, events)
                self._close(obs.ts, SessionCloseReason.REOCCUPIED, None, events)
                self._open_session(obs, events, reason="customers_seated_before_cleaning")
                return
            if self._cleaning_from is None or self._staff_only_cleaning(objects=obs.object_count):
                return
            if obs.ts < self._cleaning_from:
                return
            ok = obs.object_count is not None and obs.object_count <= p.clean_objects_max
            self._clean.update(ok, obs.ts)
            if not ok:
                self._clean.reset()
            elif self._clean.held(p.clean_confirm_s):
                self._finish_cleaning(obs.ts, events)

    def _staff_only_cleaning(self, *, objects: int | None) -> bool:
        """Sin detección confiable de objetos, la limpieza se confirma con la visita sola."""
        return self.params.clean_mode is CleanMode.STAFF_VISIT_ONLY or objects is None

    def _finish_cleaning(self, ts: datetime, events: list[Event]) -> None:
        self._end_visit(ts, events)
        self._close(ts, SessionCloseReason.CLEANED, ts, events)
        self._transition(ts, TableState.LIBRE, "cleaned", events, since=ts)
        self._cleaning_from = None
        self._clean.reset()
        self._seated.reset()

    def _open_session(self, obs: Observation, events: list[Event], reason: str) -> None:
        seated_at = self._seated.start or obs.ts
        sid = self._ids("ses")
        self.session = SessionState(id=sid, seated_at=seated_at, max_people=obs.seated_customers)
        events.append(SessionOpened(self.table_id, sid, seated_at))
        self._transition(
            obs.ts,
            TableState.OCUPADA_SIN_ATENDER,
            reason,
            events,
            since=seated_at,
            confidence=self._seated.ratio,
        )
        self._empty.reset()
        pending = self._unassigned_visit
        self._unassigned_visit = None
        # El mozo llegó mientras se confirmaba la ocupación: la visita cuenta como atención.
        if pending is not None and pending[0] >= seated_at:
            start, end = pending
            ongoing = self.visit if end is None else None
            self.visit = ongoing or VisitState(id=None, started_at=start)
            self._register_visit(start, events)
            if end is not None:
                events.append(VisitEnded(self.table_id, self.session.id, self.visit.id or "", end))
                self.visit = None
            self.session.first_attention_at = start
            events.append(self._session_updated())
            self._transition(obs.ts, TableState.ATENDIDA, "staff_visit", events)

    def _close(
        self,
        ts: datetime,
        reason: SessionCloseReason,
        cleaned_at: datetime | None,
        events: list[Event],
    ) -> None:
        s = self.session
        if s is None:
            return
        events.append(self._session_updated())
        events.append(SessionClosed(self.table_id, s.id, ts, reason, cleaned_at))
        self.session = None
        self._cleaning_from = None

    # ----------------------------------------------------------------- helpers

    def _transition(
        self,
        ts: datetime,
        to: TableState,
        reason: str,
        events: list[Event],
        *,
        since: datetime | None = None,
        confidence: float | None = None,
    ) -> None:
        events.append(
            Transition(
                self.table_id,
                ts,
                self.state,
                to,
                reason,
                self.session.id if self.session else None,
                None if confidence is None else round(confidence, 3),
            )
        )
        self.state = to
        self.state_since = since or ts
        self.paused_s = 0.0

    def _session_updated(self) -> SessionUpdated:
        s = self.session
        assert s is not None
        return SessionUpdated(
            self.table_id,
            s.id,
            s.first_attention_at,
            s.vacated_at,
            s.max_people,
            round(s.vision_lost_seconds, 1),
        )

    def _reset_detectors(self) -> None:
        for d in (self._seated, self._empty, self._staff, self._clean):
            d.reset()
        self.visit = None
        self._unassigned_visit = None


@dataclass
class StateEngine:
    """Conjunto de máquinas, una por mesa."""

    default_params: EngineParams = field(default_factory=EngineParams)
    ids: IdFactory = new_id
    machines: dict[str, TableMachine] = field(default_factory=dict)

    def machine(self, table_id: str, params: EngineParams | None = None) -> TableMachine:
        m = self.machines.get(table_id)
        if m is None:
            m = TableMachine(table_id, params or self.default_params, self.ids)
            self.machines[table_id] = m
        return m

    def observe(self, obs: Observation) -> list[Event]:
        return self.machine(obs.table_id).observe(obs)

    def close_all(self, ts: datetime, reason: SessionCloseReason) -> list[Event]:
        events: list[Event] = []
        for m in self.machines.values():
            if m.session is not None or m.state not in (TableState.LIBRE, TableState.DESHABILITADA):
                events.extend(m.close_session(ts, reason))
        return events

    def restore(self, snaps: list[TableSnapshot], params: dict[str, EngineParams]) -> None:
        for snap in snaps:
            self.machines[snap.table_id] = TableMachine.restore(
                snap, params.get(snap.table_id, self.default_params), self.ids
            )


def with_clean_mode(params: EngineParams, mode: CleanMode) -> EngineParams:
    return replace(params, clean_mode=mode)
