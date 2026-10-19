"""no_service session close reason

Revision ID: 4b8e2f6a1c90
Revises: 7c41d2e9a0b3
Create Date: 2026-10-20 10:00:00.000000

"""

import importlib.util
from collections.abc import Sequence
from pathlib import Path
from types import ModuleType

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "4b8e2f6a1c90"
down_revision: str | Sequence[str] | None = "7c41d2e9a0b3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

OLD = "'cleaned', 'reoccupied', 'vision_lost', 'closing_time', 'disabled'"
NEW = f"{OLD}, 'no_service'"
CHECK = "ck_table_sessions_session_close_reason"
OLD_COMPLETE = "AND NOT s.partial"
NEW_COMPLETE = "AND NOT s.partial\n        AND s.close_reason IS DISTINCT FROM 'no_service'"


def _previous() -> ModuleType:
    path = Path(__file__).with_name(
        "2026_10_19_1600-7c41d2e9a0b3_consumiendo_state_and_partial_sessions.py"
    )
    spec = importlib.util.spec_from_file_location("previous_consumiendo", path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def upgrade() -> None:
    """Upgrade schema."""
    op.drop_constraint(op.f(CHECK), "table_sessions", type_="check")
    op.create_check_constraint(op.f(CHECK), "table_sessions", f"close_reason IN ({NEW})")
    # Las sesiones "sin servicio" (se fueron enseguida sin atención ni consumo) no son
    # completas: no entran en las métricas.
    view = _previous().VIEW
    assert OLD_COMPLETE in view
    op.execute("DROP VIEW IF EXISTS session_metrics")
    op.execute(view.replace(OLD_COMPLETE, NEW_COMPLETE))


def downgrade() -> None:
    """Downgrade schema."""
    op.execute("DROP VIEW IF EXISTS session_metrics")
    op.execute(_previous().VIEW)
    op.execute(
        "UPDATE table_sessions SET close_reason = 'closing_time' WHERE close_reason = 'no_service'"
    )
    op.drop_constraint(op.f(CHECK), "table_sessions", type_="check")
    op.create_check_constraint(op.f(CHECK), "table_sessions", f"close_reason IN ({OLD})")
