"""Settings del proceso, leídos de variables de entorno / `.env`."""

from functools import lru_cache
from pathlib import Path
from typing import Annotated, Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql+asyncpg://mesa:mesa@localhost:5432/mesa_monitor"

    jwt_secret: SecretStr = SecretStr("")
    admin_username: str = "admin"
    admin_password: SecretStr = SecretStr("")

    nvr_host: str = ""
    nvr_port: int = 554
    nvr_user: str = ""
    nvr_password: SecretStr = SecretStr("")

    timezone: str = "America/Argentina/Cordoba"
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    cors_origins: Annotated[list[str], NoDecode] = Field(default_factory=list)

    config_dir: Path = Path("config/local")
    dashboard_dist: Path = Path("dashboard/dist")
    go2rtc_url: str = "http://go2rtc:1984"
    go2rtc_rtsp_url: str = "rtsp://go2rtc:8554"  # restream interno que lee el worker
    worker_debug_url: str = "http://worker:8001"  # overlay de debug (MJPEG) del worker
    crops_dir: Path = Path("data/crops")  # recolección opcional de recortes (fuera del repo)

    jwt_ttl_minutes: int = 12 * 60
    # Monitor de cámaras: cada cuánto se pide un frame y a partir de cuándo se considera caída.
    camera_probe_interval_s: float = 10.0
    camera_offline_after_s: float = 30.0

    @field_validator("cors_origins", mode="before")
    @classmethod
    def _split_origins(cls, value: object) -> object:
        if isinstance(value, str):
            return [o.strip() for o in value.split(",") if o.strip()]
        return value

    @field_validator("timezone")
    @classmethod
    def _check_timezone(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise ValueError(f"Zona horaria desconocida: {value!r}") from exc
        return value

    @property
    def tz(self) -> ZoneInfo:
        return ZoneInfo(self.timezone)


@lru_cache
def get_settings() -> Settings:
    return Settings()
