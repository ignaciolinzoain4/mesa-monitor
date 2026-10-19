from pathlib import Path

import pytest
import yaml

from mesa_monitor.config.cameras import CamerasConfigError, parse_cameras, redact_url
from mesa_monitor.vision.go2rtc import build_go2rtc_config, write_go2rtc_config

ENV = {"NVR_HOST": "nvr.local", "NVR_USER": "admin", "NVR_PASSWORD": "p@ss:word/1"}

YAML = """
connections:
  nvr:
    host: ${NVR_HOST}
    port: ${NVR_PORT:-554}
    user: ${NVR_USER}
    password: ${NVR_PASSWORD}
cameras:
  - id: salon-1
    name: Salón
    channel: 3
    expected_resolution: [704, 480]
  - id: galeria
    name: Galería
    channel: 5
    enabled: false
  - id: prueba
    name: Prueba
    source: "ffmpeg:virtual?video=testsrc&size=704x480#video=h264"
"""


def test_parse_expands_env_and_defaults() -> None:
    cams = parse_cameras(YAML, ENV)
    nvr = cams.connections["nvr"]
    assert nvr.host == "nvr.local"
    assert nvr.port == 554
    assert [c.id for c in cams.cameras] == ["salon-1", "galeria", "prueba"]
    assert cams.cameras[0].expected_resolution == (704, 480)


def test_missing_env_var_has_clear_message() -> None:
    with pytest.raises(CamerasConfigError, match="NVR_PASSWORD"):
        parse_cameras(YAML, {k: v for k, v in ENV.items() if k != "NVR_PASSWORD"})


def test_dahua_url_escapes_credentials() -> None:
    cams = parse_cameras(YAML, ENV)
    url = cams.stream_url(cams.cameras[0], subtype=1)
    assert url == (
        "rtsp://admin:p%40ss%3Aword%2F1@nvr.local:554/cam/realmonitor?channel=3&subtype=1"
    )


def test_redact_url_hides_credentials() -> None:
    assert redact_url("rtsp://admin:secreto@nvr.local:554/x") == "rtsp://***@nvr.local:554/x"


@pytest.mark.parametrize(
    ("snippet", "message"),
    [
        ("cameras: [{id: a, name: A, channel: 1}, {id: a, name: B, channel: 2}]", "repetido"),
        ("cameras: [{id: a, name: A, channel: 1, connection: otra}]", "otra"),
        ("cameras: [{id: A mayúscula, name: A, channel: 1}]", "id"),
        ("cameras: [{id: a, name: A, channel: 1, fps_detection: 0}]", "fps_detection"),
    ],
)
def test_invalid_configs(snippet: str, message: str) -> None:
    text = "connections: {nvr: {host: h}}\n" + snippet
    with pytest.raises(CamerasConfigError, match=message):
        parse_cameras(text, {})


def test_go2rtc_config_has_sub_and_main_streams_for_enabled_cameras() -> None:
    cfg = build_go2rtc_config(parse_cameras(YAML, ENV))
    streams = cfg["streams"]
    assert isinstance(streams, dict)
    assert set(streams) == {"salon-1", "salon-1_main", "prueba", "prueba_main"}
    assert streams["salon-1"].endswith("channel=3&subtype=1")
    assert streams["salon-1_main"].endswith("channel=3&subtype=0")
    assert streams["prueba"] == streams["prueba_main"]


def test_written_config_is_private_and_valid_yaml(tmp_path: Path) -> None:
    out = tmp_path / "go2rtc" / "go2rtc.yaml"
    write_go2rtc_config(parse_cameras(YAML, ENV), out)
    assert out.stat().st_mode & 0o777 == 0o600
    text = out.read_text()
    assert text.startswith("# GENERADO AUTOMÁTICAMENTE")
    assert "salon-1_main" in yaml.safe_load(text)["streams"]


def test_example_config_is_valid() -> None:
    example = Path("config/example/cameras.yaml").read_text()
    cams = parse_cameras(example, {"NVR_HOST": "h", "NVR_USER": "u", "NVR_PASSWORD": "p"})
    assert cams.cameras


@pytest.mark.parametrize(
    ("preset", "sub", "main"),
    [
        ("dahua", "/cam/realmonitor?channel=3&subtype=1", "/cam/realmonitor?channel=3&subtype=0"),
        ("hikvision", "/Streaming/Channels/302", "/Streaming/Channels/301"),
        ("uniview", "/unicast/c3/s1/live", "/unicast/c3/s0/live"),
        ("reolink", "/h264Preview_03_sub", "/h264Preview_03_main"),
        ("tapo", "/stream2", "/stream1"),
    ],
)
def test_brand_presets(preset: str, sub: str, main: str) -> None:
    text = f"""
connections:
  nvr: {{host: 10.0.0.5, user: u, password: p, preset: {preset}}}
cameras:
  - {{id: c3, name: C3, channel: 3}}
"""
    cams = parse_cameras(text, {})
    cam = cams.cameras[0]
    assert cams.stream_url(cam, 1) == f"rtsp://u:p@10.0.0.5:554{sub}"
    assert cams.stream_url(cam, 0) == f"rtsp://u:p@10.0.0.5:554{main}"


def test_unknown_preset_and_custom_template() -> None:
    with pytest.raises(CamerasConfigError, match="preset desconocido"):
        parse_cameras("connections: {x: {host: h, preset: marca-x}}\ncameras: []", {})
    cams = parse_cameras(
        "connections: {x: {host: h, url_template: 'rtsp://{host}/live/{quality}'}}\n"
        "cameras: [{id: a, name: A, connection: x}]",
        {},
    )
    assert cams.stream_url(cams.cameras[0], 1) == "rtsp://h/live/sub"


def test_literal_sources_with_separate_main() -> None:
    cams = parse_cameras(
        "cameras: [{id: ip, name: IP, source: 'rtsp://cam/low', source_main: 'rtsp://cam/high'}]",
        {},
    )
    cam = cams.cameras[0]
    assert cams.stream_url(cam, 1) == "rtsp://cam/low"
    assert cams.stream_url(cam, 0) == "rtsp://cam/high"
