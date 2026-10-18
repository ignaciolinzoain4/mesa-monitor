import pytest
from tools.check_licenses import classify


@pytest.mark.parametrize(
    ("license_text", "expected"),
    [
        ("Apache-2.0", "ok"),
        ("MIT License", "ok"),
        ("BSD-3-Clause", "ok"),
        ("Apache Software License; MIT License", "ok"),
        ("Mozilla Public License 2.0 (MPL 2.0)", "weak"),
        ("GPLv2", "deny"),
        ("GPL-3.0-or-later", "deny"),
        ("LGPL-3.0-only", "deny"),
        ("AGPL-3.0", "deny"),
        ("GNU Affero General Public License v3", "deny"),
        ("GNU Lesser General Public License v3 (LGPLv3)", "deny"),
        ("PML 1.0", "deny"),
        ("MIT OR GPL-2.0", "deny"),  # dual: se revisa a mano antes de aceptarla
        ("UNKNOWN", "unknown"),
        ("Proprietary", "unknown"),
    ],
)
def test_classify(license_text: str, expected: str) -> None:
    assert classify(license_text) == expected
