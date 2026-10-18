"""Chequeo de licencias del entorno Python (SPEC §3.2).

Falla (exit 1) si algún paquete instalado tiene licencia GPL/AGPL/LGPL/SSPL/PML o una licencia
que no está en la lista de permitidas. Uso:

    uv run python tools/check_licenses.py

Para un paquete con metadata incompleta (licencia "UNKNOWN") que se revisó a mano, agregarlo
a `REVIEWED` con la licencia verificada y la fuente.
"""

import json
import re
import subprocess
import sys

# Copyleft fuerte o licencias no-libres: prohibidas.
DENY = re.compile(r"\b([AL]?GPL|GNU (Affero |Lesser )?General Public|SSPL|PML\b)", re.I)

# Permisivas. MPL-2.0 es copyleft débil a nivel archivo: aceptable mientras no modifiquemos
# esos archivos (caso certifi/pathspec, dependencias transitivas usadas tal cual).
ALLOW = re.compile(
    r"\b(Apache|MIT|BSD|ISC|PSF|Python Software Foundation|Unlicense|0BSD|Zlib|"
    r"MPL[- ]?2\.0|Mozilla Public License 2\.0|CC0)\b",
    re.I,
)
WEAK_COPYLEFT = re.compile(r"MPL|Mozilla", re.I)

# Paquetes revisados a mano: nombre -> licencia verificada (con fuente en el comentario).
REVIEWED: dict[str, str] = {}

IGNORE = {"mesa-monitor", "mesa-monitor-client"}


def classify(license_text: str) -> str:
    """Devuelve "deny", "unknown", "weak" (copyleft débil aceptado) u "ok"."""
    if DENY.search(license_text):
        return "deny"
    if not ALLOW.search(license_text):
        return "unknown"
    if WEAK_COPYLEFT.search(license_text):
        return "weak"
    return "ok"


def main() -> int:
    raw = subprocess.run(
        [sys.executable, "-m", "piplicenses", "--from=mixed", "--format=json"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    denied: list[str] = []
    unknown: list[str] = []
    weak: list[str] = []
    for pkg in json.loads(raw):
        name, lic = pkg["Name"], pkg["License"]
        if name in IGNORE:
            continue
        lic = REVIEWED.get(name, lic)
        verdict = classify(lic)
        if verdict == "deny":
            denied.append(f"  {name} {pkg['Version']}: {lic}")
        elif verdict == "unknown":
            unknown.append(f"  {name} {pkg['Version']}: {lic}")
        elif verdict == "weak":
            weak.append(f"  {name}: {lic}")

    if weak:
        print("Copyleft débil aceptado (no modificar estos paquetes):", *weak, sep="\n")
    if denied:
        print("\nLICENCIAS PROHIBIDAS:", *denied, sep="\n")
    if unknown:
        print("\nLicencias no reconocidas (revisar y agregar a REVIEWED):", *unknown, sep="\n")
    if denied or unknown:
        return 1
    print("\nLicencias OK.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
