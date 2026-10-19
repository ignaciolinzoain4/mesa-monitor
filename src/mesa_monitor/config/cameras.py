"""Carga de `config/local/cameras.yaml`: conexiones (NVR u otras) y cámaras.

Los valores `${VAR}` / `${VAR:-default}` se reemplazan por variables de entorno, así las
credenciales quedan en `.env` y nunca en el YAML.

Cualquier sistema de cámaras sirve si entrega video por RTSP, ONVIF, HTTP/MJPEG o archivo:
- `preset` de marca (dahua, hikvision, reolink, …) arma la URL RTSP sola;
- `url_template` propio para cualquier otra marca;
- `source` / `source_main` literales por cámara (cualquier fuente que acepte go2rtc).

Variables de `url_template`: {user} {password} {host} {port} {channel} {channel2} (01, 02…)
{subtype} (0 = principal, 1 = secundario) {stream_n} (1 = principal, 2 = secundario)
{quality} ("main" / "sub").
"""

import os
import re
from pathlib import Path
from typing import Annotated, Any, Self
from urllib.parse import quote

import yaml
from pydantic import BaseModel, ConfigDict, Field, SecretStr, StringConstraints, model_validator

DAHUA_URL_TEMPLATE = (
    "rtsp://{user}:{password}@{host}:{port}/cam/realmonitor?channel={channel}&subtype={subtype}"
)
# Plantillas RTSP por marca (las documentadas por cada fabricante; verificar con el manual del
# equipo si algo no conecta). Grabadores: {channel} = número de cámara en el NVR/DVR.
PRESETS: dict[str, str] = {
    "dahua": DAHUA_URL_TEMPLATE,
    "amcrest": DAHUA_URL_TEMPLATE,  # misma familia que Dahua
    "intelbras": DAHUA_URL_TEMPLATE,  # misma familia que Dahua
    "hikvision": "rtsp://{user}:{password}@{host}:{port}/Streaming/Channels/{channel}0{stream_n}",
    "ezviz": "rtsp://{user}:{password}@{host}:{port}/Streaming/Channels/{channel}0{stream_n}",
    "uniview": "rtsp://{user}:{password}@{host}:{port}/unicast/c{channel}/s{subtype}/live",
    "reolink": "rtsp://{user}:{password}@{host}:{port}/h264Preview_{channel2}_{quality}",
    "tapo": "rtsp://{user}:{password}@{host}:{port}/stream{stream_n}",
    # ONVIF: go2rtc descubre las URLs solo (no distingue principal/secundario).
    "onvif": "onvif://{user}:{password}@{host}:{port}",
}
SUBSTREAM = 1
MAIN_STREAM = 0

_ENV_REF = re.compile(r"\$\{([A-Z0-9_]+)(?::-([^}]*))?\}")

CameraId = Annotated[str, StringConstraints(pattern=r"^[a-z0-9][a-z0-9_-]{0,47}$")]


class CamerasConfigError(ValueError):
    """Error de configuración con mensaje entendible para el usuario."""


class Connection(BaseModel):
    model_config = ConfigDict(extra="forbid")

    host: str
    port: int = 554
    user: str = ""
    password: SecretStr = SecretStr("")
    preset: str | None = None  # marca: ver PRESETS. Por defecto, Dahua.
    url_template: str | None = None  # plantilla propia (gana sobre el preset)

    @model_validator(mode="after")
    def _known_preset(self) -> Self:
        if self.preset is not None and self.preset not in PRESETS:
            raise ValueError(
                f"preset desconocido {self.preset!r}; válidos: {', '.join(sorted(PRESETS))} "
                "(o definí url_template)"
            )
        return self

    @property
    def template(self) -> str:
        return self.url_template or PRESETS[self.preset or "dahua"]

    def url(self, channel: int | None, subtype: int) -> str:
        # Usuario y clave se escapan: una clave con "@" o ":" rompería la URL RTSP.
        ch = 1 if channel is None else channel
        return self.template.format(
            user=quote(self.user, safe=""),
            password=quote(self.password.get_secret_value(), safe=""),
            host=self.host,
            port=self.port,
            channel=ch,
            channel2=f"{ch:02d}",
            subtype=subtype,
            stream_n=subtype + 1,
            quality="sub" if subtype == SUBSTREAM else "main",
        )


class Camera(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: CameraId
    name: str
    enabled: bool = True
    connection: str | None = "nvr"
    channel: int | None = Field(default=None, ge=1, le=64)
    # Fuente go2rtc literal (cualquier URL rtsp://, http://, onvif://, ffmpeg:archivo…).
    # Si está, se ignoran connection/channel. `source_main` = versión en alta para snapshots.
    source: str | None = None
    source_main: str | None = None
    fps_detection: float = Field(default=3.0, gt=0, le=15)
    expected_resolution: tuple[int, int] | None = None

    @model_validator(mode="after")
    def _source_or_channel(self) -> Self:
        if self.source is None and self.connection is None:
            raise ValueError("cada cámara necesita `connection` o `source`")
        return self


class CamerasFile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    connections: dict[str, Connection] = Field(default_factory=dict)
    cameras: list[Camera] = Field(default_factory=list)

    @model_validator(mode="after")
    def _check_refs(self) -> Self:
        seen: set[str] = set()
        for cam in self.cameras:
            if cam.id in seen:
                raise ValueError(f"id de cámara repetido: {cam.id!r}")
            seen.add(cam.id)
            if cam.source is None and cam.connection not in self.connections:
                raise ValueError(
                    f"la cámara {cam.id!r} usa la conexión {cam.connection!r}, "
                    "que no está definida en `connections`"
                )
        return self

    def stream_url(self, cam: Camera, subtype: int) -> str:
        """URL de origen para go2rtc. Contiene credenciales: no loguear (usar `redact_url`)."""
        if cam.source is not None:
            return cam.source_main if subtype == MAIN_STREAM and cam.source_main else cam.source
        if cam.connection is None:  # imposible: lo garantiza Camera._source_or_channel
            raise CamerasConfigError(f"la cámara {cam.id!r} no tiene conexión")
        return self.connections[cam.connection].url(cam.channel, subtype)


def _expand_env(value: Any, env: dict[str, str]) -> Any:
    if isinstance(value, str):

        def repl(m: re.Match[str]) -> str:
            name, default = m.group(1), m.group(2)
            if name in env:
                return env[name]
            if default is not None:
                return default
            raise CamerasConfigError(f"Falta la variable {name} en el archivo .env")

        return _ENV_REF.sub(repl, value)
    if isinstance(value, dict):
        return {k: _expand_env(v, env) for k, v in value.items()}
    if isinstance(value, list):
        return [_expand_env(v, env) for v in value]
    return value


def parse_cameras(text: str, env: dict[str, str] | None = None) -> CamerasFile:
    raw = yaml.safe_load(text) or {}
    expanded = _expand_env(raw, dict(os.environ) if env is None else env)
    try:
        return CamerasFile.model_validate(expanded)
    except ValueError as exc:
        raise CamerasConfigError(f"cameras.yaml inválido: {exc}") from exc


def load_cameras(path: Path, env: dict[str, str] | None = None) -> CamerasFile:
    if not path.is_file():
        raise CamerasConfigError(
            f"No existe {path}. Copiá config/example/cameras.yaml a config/local/ y completalo."
        )
    return parse_cameras(path.read_text(encoding="utf-8"), env)


def redact_url(url: str) -> str:
    """Oculta usuario y clave de una URL para poder mostrarla o loguearla."""
    return re.sub(r"(://)[^/@\s]+@", r"\1***@", url)
