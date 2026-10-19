"""Rate limiting básico en memoria (SPEC §17): por IP, más estricto en login y PIN."""

import time
from collections import defaultdict, deque

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

STRICT_PATHS = {"/api/v1/auth/login": (10, 60.0), "/api/v1/auth/device-pin": (5, 60.0)}
DEFAULT_LIMIT = (300, 60.0)  # pedidos por ventana (s)


class RateLimitMiddleware(BaseHTTPMiddleware):
    def __init__(self, app: object, enabled: bool = True) -> None:
        super().__init__(app)  # type: ignore[arg-type]
        self.enabled = enabled
        self._hits: dict[tuple[str, str], deque[float]] = defaultdict(deque)

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        path = request.url.path
        if not self.enabled or not path.startswith("/api/") or path.endswith("/ws"):
            return await call_next(request)
        limit, window = STRICT_PATHS.get(path, DEFAULT_LIMIT)
        bucket = path if path in STRICT_PATHS else "*"
        ip = request.client.host if request.client else "?"
        now = time.monotonic()
        hits = self._hits[(ip, bucket)]
        while hits and now - hits[0] > window:
            hits.popleft()
        if len(hits) >= limit:
            return JSONResponse(
                {"detail": "Demasiados pedidos. Esperá un minuto y volvé a intentar."},
                status_code=429,
                headers={"Retry-After": str(int(window))},
            )
        hits.append(now)
        return await call_next(request)
