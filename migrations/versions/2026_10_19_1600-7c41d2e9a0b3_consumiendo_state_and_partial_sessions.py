"""consumiendo state, served_at and partial sessions

Revision ID: 7c41d2e9a0b3
Revises: 1a3af06fdffe
Create Date: 2026-10-19 16:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "7c41d2e9a0b3"
down_revision: str | Sequence[str] | None = "1a3af06fdffe"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

OLD_STATES = (
    "'LIBRE', 'OCUPADA_SIN_ATENDER', 'ATENDIDA', 'DESOCUPADA_SUCIA', 'SIN_VISION', 'DESHABILITADA'"
)
NEW_STATES = (
    "'LIBRE', 'OCUPADA_SIN_ATENDER', 'ATENDIDA', 'CONSUMIENDO', 'DESOCUPADA_SUCIA', 'SIN_VISION',"
    " 'DESHABILITADA'"
)
# (tabla, constraint, columna) de todos los CHECK de estado de mesa.
STATE_CHECKS = (
    ("table_state", "ck_table_state_current_state", "state"),
    ("table_state", "ck_table_state_resume_state", "resume_state"),
    ("alerts", "ck_alerts_alert_table_state", "state"),
    ("state_transitions", "ck_state_transitions_from_state", "from_state"),
    ("state_transitions", "ck_state_transitions_to_state", "to_state"),
)

# Igual que 1a3af06fdffe, más `served_at`, `partial` y t_serv (sentarse → comida en la mesa).
# Las sesiones parciales (ya estaban sentados al arrancar) no son "completas": no entran en las
# métricas porque no se sabe cuándo se sentaron.
VIEW = """
CREATE VIEW session_metrics AS
WITH marks AS (
    SELECT s.id AS session_id, s.first_attention_at AS ts
    FROM table_sessions s WHERE s.first_attention_at IS NOT NULL
    UNION ALL
    SELECT v.session_id, v.started_at FROM staff_visits v
    UNION ALL
    SELECT s.id, s.vacated_at FROM table_sessions s
    WHERE s.vacated_at IS NOT NULL AND s.first_attention_at IS NOT NULL
),
gaps AS (
    SELECT session_id, max(gap) AS g_max
    FROM (
        SELECT session_id,
               EXTRACT(EPOCH FROM ts - lag(ts) OVER (PARTITION BY session_id ORDER BY ts)) AS gap
        FROM marks
    ) q
    GROUP BY session_id
),
visits AS (
    SELECT session_id, count(*) AS n_vis FROM staff_visits GROUP BY session_id
)
SELECT
    s.id AS session_id,
    s.table_id,
    t.label AS table_label,
    t.sector,
    s.business_date,
    s.seated_at,
    s.first_attention_at,
    s.served_at,
    s.vacated_at,
    s.cleaned_at,
    s.closed_at,
    s.close_reason,
    s.max_people,
    s.vision_lost_seconds,
    s.partial,
    EXTRACT(EPOCH FROM s.first_attention_at - s.seated_at) AS t_esp,
    EXTRACT(EPOCH FROM s.served_at - s.seated_at) AS t_serv,
    EXTRACT(EPOCH FROM s.vacated_at - s.seated_at) AS t_est,
    EXTRACT(EPOCH FROM s.cleaned_at - s.vacated_at) AS t_lim,
    COALESCE(v.n_vis, 0) AS n_vis,
    CASE WHEN s.vacated_at IS NOT NULL THEN g.g_max END AS g_max,
    (
        s.closed_at IS NOT NULL
        AND s.close_reason IS DISTINCT FROM 'vision_lost'
        AND NOT s.partial
    ) AS complete
FROM table_sessions s
JOIN tables t ON t.id = s.table_id
LEFT JOIN gaps g ON g.session_id = s.id
LEFT JOIN visits v ON v.session_id = s.id
"""


def _set_state_checks(states: str) -> None:
    for table, name, column in STATE_CHECKS:
        op.drop_constraint(op.f(name), table, type_="check")
        op.create_check_constraint(op.f(name), table, f"{column} IN ({states})")


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        "table_sessions", sa.Column("served_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column(
        "table_sessions",
        sa.Column("partial", sa.Boolean(), server_default=sa.text("false"), nullable=False),
    )
    _set_state_checks(NEW_STATES)
    op.execute("DROP VIEW IF EXISTS session_metrics")
    op.execute(VIEW)


def downgrade() -> None:
    """Downgrade schema."""
    import importlib.util
    from pathlib import Path

    path = Path(__file__).with_name("2026_10_18_2308-1a3af06fdffe_session_metrics_view.py")
    spec = importlib.util.spec_from_file_location("previous_session_metrics", path)
    assert spec is not None
    assert spec.loader is not None
    previous = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(previous)
    op.execute("DROP VIEW IF EXISTS session_metrics")
    op.execute(previous.VIEW)
    for table, _name, column in STATE_CHECKS:
        op.execute(
            f"UPDATE {table} SET {column} = 'ATENDIDA' WHERE {column} = 'CONSUMIENDO'"  # noqa: S608
        )
    _set_state_checks(OLD_STATES)
    op.drop_column("table_sessions", "partial")
    op.drop_column("table_sessions", "served_at")
