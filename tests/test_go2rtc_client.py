from datetime import UTC, datetime, timedelta

import httpx
import pytest

from mesa_monitor.api.camera_monitor import CameraMonitor
from mesa_monitor.core.enums import CameraStatus
from mesa_monitor.vision.go2rtc import Go2rtcClient, SnapshotError, StreamKind, jpeg_size
from tests.conftest import fake_jpeg


def test_jpeg_size_reads_sof() -> None:
    assert jpeg_size(fake_jpeg(1920, 1080)) == (1920, 1080)


@pytest.mark.parametrize("data", [b"", b"not a jpeg", b"\xff\xd8\xff"])
def test_jpeg_size_rejects_garbage(data: bytes) -> None:
    assert jpeg_size(data) is None


def _client(handler: httpx.MockTransport) -> Go2rtcClient:
    return Go2rtcClient("http://go2rtc.test", transport=handler)


async def test_frame_requests_main_stream_name() -> None:
    seen: list[str] = []

    def handler(req: httpx.Request) -> httpx.Response:
        seen.append(req.url.params["src"])
        return httpx.Response(200, content=fake_jpeg(), headers={"content-type": "image/jpeg"})

    client = _client(httpx.MockTransport(handler))
    frame = await client.frame("salon-1", StreamKind.MAIN)
    assert seen == ["salon-1_main"]
    assert (frame.width, frame.height) == (704, 480)
    await client.aclose()


async def test_empty_200_means_camera_down() -> None:
    # Comportamiento real de go2rtc 1.9.14 con una cámara caída.
    client = _client(httpx.MockTransport(lambda req: httpx.Response(200, content=b"")))
    with pytest.raises(SnapshotError, match="sin señal"):
        await client.frame("salon-1")
    await client.aclose()


async def test_404_means_unknown_stream() -> None:
    client = _client(httpx.MockTransport(lambda req: httpx.Response(404, text="stream not found")))
    with pytest.raises(SnapshotError, match="no conoce"):
        await client.frame("salon-1")
    await client.aclose()


async def test_connection_error_is_wrapped() -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused")

    client = _client(httpx.MockTransport(handler))
    with pytest.raises(SnapshotError, match="no responde"):
        await client.frame("salon-1")
    await client.aclose()


class FakeClock:
    def __init__(self) -> None:
        self.now = datetime(2026, 10, 18, 20, 0, tzinfo=UTC)

    def __call__(self) -> datetime:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += timedelta(seconds=seconds)


async def test_monitor_tolerates_short_failures_then_goes_offline() -> None:
    responses: list[bytes] = [fake_jpeg(), b"", b"", b"", fake_jpeg(1280, 720)]
    client = _client(httpx.MockTransport(lambda req: httpx.Response(200, content=responses.pop(0))))
    clock = FakeClock()
    monitor = CameraMonitor(client, None, probe_interval_s=10, offline_after_s=30, clock=clock)
    monitor.set_cameras({"cam": (704, 480)})

    assert (await monitor.check("cam")).status is CameraStatus.ONLINE
    clock.advance(10)
    assert (await monitor.check("cam")).status is CameraStatus.ONLINE  # 0 s caída
    clock.advance(20)
    assert (await monitor.check("cam")).status is CameraStatus.ONLINE  # 20 s < 30 s
    clock.advance(15)
    h = await monitor.check("cam")  # 35 s
    assert h.status is CameraStatus.OFFLINE
    assert h.detail is not None
    assert "sin señal" in h.detail
    clock.advance(10)
    h = await monitor.check("cam")
    assert h.status is CameraStatus.DEGRADED  # volvió, pero con otra resolución
    assert h.detail == "Resolución 1280x720, se esperaba 704x480"
    await client.aclose()


async def test_monitor_never_seen_camera_is_offline_immediately() -> None:
    client = _client(httpx.MockTransport(lambda req: httpx.Response(200, content=b"")))
    monitor = CameraMonitor(client, None, probe_interval_s=10, offline_after_s=30)
    monitor.set_cameras({"cam": None})
    assert (await monitor.check("cam")).status is CameraStatus.OFFLINE
    await client.aclose()
