"""session metrics view

Revision ID: 1a3af06fdffe
Revises: 137b2f4a518f
Create Date: 2026-10-18 23:08:39.181090

"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "1a3af06fdffe"
down_revision: str | Sequence[str] | None = "137b2f4a518f"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# Métricas por sesión (SPEC §9). G_max = mayor intervalo entre [primera atención, visitas…,
# se fueron]. Cumplimiento (Anexo A): sin ninguna columna que identifique personas.
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
    s.vacated_at,
    s.cleaned_at,
    s.closed_at,
    s.close_reason,
    s.max_people,
    s.vision_lost_seconds,
    EXTRACT(EPOCH FROM s.first_attention_at - s.seated_at) AS t_esp,
    EXTRACT(EPOCH FROM s.vacated_at - s.seated_at) AS t_est,
    EXTRACT(EPOCH FROM s.cleaned_at - s.vacated_at) AS t_lim,
    COALESCE(v.n_vis, 0) AS n_vis,
    CASE WHEN s.vacated_at IS NOT NULL THEN g.g_max END AS g_max,
    (s.closed_at IS NOT NULL AND s.close_reason IS DISTINCT FROM 'vision_lost') AS complete
FROM table_sessions s
JOIN tables t ON t.id = s.table_id
LEFT JOIN gaps g ON g.session_id = s.id
LEFT JOIN visits v ON v.session_id = s.id
"""


def upgrade() -> None:
    """Upgrade schema."""
    op.execute(VIEW)


def downgrade() -> None:
    """Downgrade schema."""
    op.execute("DROP VIEW IF EXISTS session_metrics")
