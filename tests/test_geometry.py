import pytest

from mesa_monitor.core.geometry import (
    InvalidPolygonError,
    area,
    box_overlap_fraction,
    contains,
    expand,
    validate_polygon,
)

SQUARE = [[0.2, 0.2], [0.6, 0.2], [0.6, 0.6], [0.2, 0.6]]
L_SHAPE = [[0.0, 0.0], [0.5, 0.0], [0.5, 0.2], [0.2, 0.2], [0.2, 0.5], [0.0, 0.5]]


def test_area() -> None:
    assert area(SQUARE) == pytest.approx(0.16)


@pytest.mark.parametrize(
    ("x", "y", "inside"),
    [(0.4, 0.4, True), (0.1, 0.1, False), (0.2, 0.4, True), (0.6, 0.6, True), (0.61, 0.4, False)],
)
def test_contains_square(x: float, y: float, inside: bool) -> None:
    assert contains(SQUARE, x, y) is inside


def test_contains_concave() -> None:
    assert contains(L_SHAPE, 0.1, 0.4)
    assert not contains(L_SHAPE, 0.4, 0.4)  # la "muesca" de la L


def test_expand_grows_from_centroid_and_clamps() -> None:
    big = expand(SQUARE, 1.5)
    assert big[0] == pytest.approx([0.1, 0.1])
    assert big[2] == pytest.approx([0.7, 0.7])
    clamped = expand([[0.0, 0.0], [1.0, 0.0], [1.0, 1.0], [0.0, 1.0]], 2)
    assert all(0 <= c <= 1 for p in clamped for c in p)


@pytest.mark.parametrize(
    "poly",
    [
        [[0, 0], [1, 1]],
        [[0, 0], [1.2, 0], [1, 1]],
        [[0, 0], [0.5, 0.5], [1, 1]],
        [[0, 0, 0], [1, 0], [1, 1]],
    ],
)
def test_validate_rejects_bad_polygons(poly: list[list[float]]) -> None:
    with pytest.raises(InvalidPolygonError):
        validate_polygon(poly)


def test_validate_rounds() -> None:
    assert validate_polygon([[0.1234567, 0], [1, 0], [1, 1]])[0] == [0.12346, 0.0]


def test_box_overlap_fraction() -> None:
    assert box_overlap_fraction((0, 0, 1, 1), (0, 0, 0.5, 1)) == pytest.approx(0.5)
    assert box_overlap_fraction((0, 0, 1, 1), (2, 2, 3, 3)) == 0
