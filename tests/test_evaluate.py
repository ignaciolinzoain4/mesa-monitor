import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from tools.evaluate import Mark, evaluate, load_annotations, load_detections, main, parse_video_time

START = datetime(2026, 10, 18, 21, 0, tzinfo=UTC)


@pytest.mark.parametrize(
    ("raw", "secs"), [("90", 90), ("01:30", 90), ("1:02:03", 3723), (" 0:05 ", 5)]
)
def test_parse_video_time(raw: str, secs: float) -> None:
    assert parse_video_time(raw) == secs


def test_matching_errors_and_visit_precision_recall() -> None:
    ann = [
        Mark("12", "seated", 100),
        Mark("12", "first_attention", 300),
        Mark("12", "staff_visit", 300),
        Mark("12", "staff_visit", 900),
        Mark("12", "staff_visit", 1500),  # no detectada → falso negativo
        Mark("12", "vacated", 2000),
    ]
    det = [
        Mark("12", "seated", 120),  # +20 s
        Mark("12", "first_attention", 304),
        Mark("12", "staff_visit", 304),
        Mark("12", "staff_visit", 890),
        Mark("12", "staff_visit", 1200),  # nadie la anotó → falso positivo
        Mark("12", "vacated", 2150),
        Mark("7", "seated", 100),  # otra mesa, no anotada
    ]
    r = evaluate(ann, det)
    assert r["seated"].errors == [20]
    assert r["seated"].false_positives == 1  # la mesa 7
    assert r["staff_visit"].matched == 2
    assert r["staff_visit"].false_positives == 1
    assert r["staff_visit"].false_negatives == 1
    assert r["staff_visit"].precision == pytest.approx(2 / 3)
    assert r["staff_visit"].recall == pytest.approx(2 / 3)
    assert r["vacated"].errors == [150]


def test_one_to_one_matching_prefers_closest() -> None:
    ann = [Mark("1", "staff_visit", 100), Mark("1", "staff_visit", 110)]
    det = [Mark("1", "staff_visit", 108)]
    r = evaluate(ann, det)["staff_visit"]
    assert (r.matched, r.false_negatives, r.errors) == (1, 1, [-2])


def test_end_to_end_with_replay_output(tmp_path: Path) -> None:
    def iso(s: float) -> str:
        return (START + timedelta(seconds=s)).isoformat()

    events = [
        {"event": "SessionOpened", "table_id": "larga", "session_id": "s1", "seated_at": iso(6)},
        {
            "event": "VisitStarted",
            "table_id": "larga",
            "session_id": "s1",
            "visit_id": "v1",
            "started_at": iso(176),
        },
        {
            "event": "SessionUpdated",
            "table_id": "larga",
            "session_id": "s1",
            "first_attention_at": iso(176),
            "vacated_at": None,
        },
        {
            "event": "SessionUpdated",
            "table_id": "larga",
            "session_id": "s1",
            "first_attention_at": iso(176),
            "vacated_at": iso(900),
        },
        {
            "event": "SessionClosed",
            "table_id": "larga",
            "session_id": "s1",
            "cleaned_at": iso(1000),
            "reason": "cleaned",
        },
    ]
    ev_path = tmp_path / "events.jsonl"
    ev_path.write_text("\n".join(json.dumps(e) for e in events))
    (tmp_path / "observations.jsonl").write_text(json.dumps({"ts": START.isoformat()}) + "\n")
    csv_path = tmp_path / "anotaciones.csv"
    csv_path.write_text(
        "table_label,event,timestamp_video\n"
        "larga,seated,00:00\nlarga,first_attention,02:50\nlarga,staff_visit,02:50\n"
        "larga,vacated,14:00\nlarga,cleaned,16:30\n"
    )
    dets = load_detections(ev_path, START)
    assert {d.event for d in dets} == {
        "seated",
        "staff_visit",
        "first_attention",
        "vacated",
        "cleaned",
    }
    assert len(load_annotations(csv_path)) == 5
    out = tmp_path / "r.json"
    assert main([str(ev_path), str(csv_path), "--json", str(out)]) == 0
    data = json.loads(out.read_text())
    assert data["seated"]["mean_abs_error_s"] == 6
    assert data["staff_visit"]["f1"] == 1.0


def test_bad_event_name_is_reported(tmp_path: Path) -> None:
    p = tmp_path / "a.csv"
    p.write_text("table_label,event,timestamp_video\n1,sentados,00:10\n")
    with pytest.raises(SystemExit, match="evento desconocido"):
        load_annotations(p)
