"""Exporta el JSON Schema de todos los eventos (SPEC §12) a `schemas/`.

uv run python tools/export_schemas.py          # escribe
uv run python tools/export_schemas.py --check  # falla si están desactualizados (CI)
"""

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from pydantic import TypeAdapter

from mesa_monitor.core.events import EVENT_MODELS, AnyEvent

OUT = Path("schemas")


def render() -> dict[str, str]:
    files: dict[str, Any] = {
        f"{name}.json": model.model_json_schema() for name, model in EVENT_MODELS.items()
    }
    files["events.json"] = TypeAdapter(AnyEvent).json_schema()
    return {
        k: json.dumps(v, indent=2, ensure_ascii=False, sort_keys=True) + "\n"
        for k, v in files.items()
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true")
    args = ap.parse_args()
    files = render()
    if args.check:
        stale = [
            n for n, txt in files.items() if not (OUT / n).is_file() or (OUT / n).read_text() != txt
        ]
        if stale:
            print(
                "JSON Schemas desactualizados:",
                ", ".join(stale),
                "→ correr tools/export_schemas.py",
            )
            return 1
        print("JSON Schemas al día.")
        return 0
    OUT.mkdir(exist_ok=True)
    for name, txt in files.items():
        (OUT / name).write_text(txt, encoding="utf-8")
    print(f"{len(files)} esquemas escritos en {OUT}/")
    return 0


if __name__ == "__main__":
    sys.exit(main())
