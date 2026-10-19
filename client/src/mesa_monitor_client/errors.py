"""Errores con mensajes en español, pensados para mostrarle a alguien no técnico."""


class MesaMonitorError(Exception):
    """Error base del cliente."""


class ServidorNoDisponible(MesaMonitorError):
    def __init__(self, base_url: str, detalle: str = "") -> None:
        super().__init__(
            f"No se pudo conectar con Mesa Monitor en {base_url}. "
            "Revisá que la PC del sistema de cámaras esté prendida y en la misma red."
            + (f" (detalle: {detalle})" if detalle else "")
        )


class ApiKeyInvalida(MesaMonitorError):
    def __init__(self) -> None:
        super().__init__(
            "La API key no es válida o fue revocada. Generá una nueva en el dashboard de "
            "Mesa Monitor (Configuración → Accesos → API keys) y guardala en el archivo .env."
        )


class PermisoDenegado(MesaMonitorError):
    def __init__(self, detalle: str = "") -> None:
        super().__init__(
            "La API key no tiene permiso para esta acción" + (f": {detalle}" if detalle else ".")
        )


class ErrorDelServidor(MesaMonitorError):
    def __init__(self, status: int, detalle: str = "") -> None:
        self.status = status
        super().__init__(f"Mesa Monitor respondió con un error ({status}). {detalle}".strip())


class FirmaInvalida(MesaMonitorError):
    def __init__(self) -> None:
        super().__init__(
            "La firma del webhook no es válida (secreto incorrecto o mensaje alterado)."
        )
