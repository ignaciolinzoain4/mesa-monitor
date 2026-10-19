# Mesa Monitor

Sistema que mira las cámaras del local y registra **el estado de cada mesa** (libre, ocupada,
atendida, sucia), cuánto tiempo pasa en cada estado y **avisa** cuando una mesa espera demasiado.
Incluye un panel de análisis y una API para conectarlo con el sistema de gestión.

- No graba video ni reconoce caras. Todo funciona dentro de la red del local.
- Mide la atención de **mesas**, no el trabajo de cada empleado: no sabe qué mozo atendió qué mesa.
  Antes de usarlo en el local, ver [docs/cumplimiento.md](docs/cumplimiento.md).
- Especificación completa: [SPEC.md](SPEC.md).
- Licencia: [Apache-2.0](LICENSE).

## Estado del proyecto

| Fase | Contenido | Estado |
|---|---|---|
| F0 | Esqueleto: Docker Compose, Postgres + migraciones, API `/health`, dashboard vacío, CI | ✅ |
| F1 | Ingesta de cámaras (go2rtc), snapshots, benchmark de hardware | ✅ |
| F2 | Editor de zonas y plano del salón, respaldo YAML | ✅ |
| F3 | Detección (RF-DETR por mosaicos) + tracking + overlay + replay de video | ✅ |
| F4 | Máquina de estados + persistencia + clasificador y calibración de personal | ✅ |
| F5 | API completa, WebSocket, motor de alertas, webhooks, accesos | ✅ |
| F6 | Pantalla /alertas para PCs de tiqueo (sonido, PIN, reconexión) | ✅ |
| F7 | Análisis y métricas | Pendiente |
| F8 | Cliente Python + guía de integración | Pendiente |
| F9 | Evaluación y ajuste | Pendiente |

## Instalación rápida (Docker)

Guía completa (Windows con GPU, Linux, cámaras, benchmark): [docs/instalacion.md](docs/instalacion.md).

```sh
cp .env.example .env                                   # completar contraseñas y JWT_SECRET
cp config/example/cameras.yaml config/local/cameras.yaml   # completar canales
docker compose up -d --build
```

Abrir <http://localhost:8000>. El estado del servidor está en <http://localhost:8000/api/v1/health>
y la documentación de la API en <http://localhost:8000/api/v1/docs>.

En `.env`, `COMPOSE_PROFILES=cpu` usa el procesador; `COMPOSE_PROFILES=gpu` usa una placa NVIDIA.

## Desarrollo

Ver [CLAUDE.md](CLAUDE.md) para la estructura del repo y todos los comandos. Resumen:

```sh
make sync     # instala dependencias (necesita uv: https://docs.astral.sh/uv/)
make check    # lint + tipos + tests + licencias + dashboard
```
