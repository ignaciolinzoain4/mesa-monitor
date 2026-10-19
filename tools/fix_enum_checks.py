"""Hook de Alembic: limpia los CHECK duplicados que autogenera para los enums (ver CLAUDE.md).

Los enums se guardan como texto + CHECK (`db.base.str_enum`). Autogenerate emite el CHECK dos
veces (uno con nombre plano y otro con la convención `ck_...`): se deja solo el de la convención
y el `sa.Enum` de la migración no crea el suyo.
"""

import re
import sys
from pathlib import Path

PLAIN_CHECK = re.compile(
    r"""\n\s*sa\.CheckConstraint\(\s*(?:"[^"]*"|'[^']*'),\s*name=(?:"[a-z_]+"|'[a-z_]+'),?\s*\),"""
)


def fix(text: str) -> str:
    return PLAIN_CHECK.sub("", text).replace("create_constraint=True", "create_constraint=False")


if __name__ == "__main__":
    for arg in sys.argv[1:]:
        p = Path(arg)
        p.write_text(fix(p.read_text(encoding="utf-8")), encoding="utf-8")
