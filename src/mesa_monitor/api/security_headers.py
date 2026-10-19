"""Cabeceras de seguridad para la API y el dashboard."""

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response

HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    # Los tokens de dispositivo (/alertas?token=) y de <img> (?access_token=) viajan en la URL:
    # nunca se mandan a otro sitio en el Referer.
    "Referrer-Policy": "no-referrer",
    "Permissions-Policy": "camera=(), microphone=(), geolocation=()",
}


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        response = await call_next(request)
        for k, v in HEADERS.items():
            response.headers.setdefault(k, v)
        return response
