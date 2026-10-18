# Mesa Monitor

Sistema que mira las cámaras del local y registra **el estado de cada mesa** (libre, ocupada,
atendida, sucia), cuánto tiempo pasa en cada estado y **avisa** cuando una mesa espera demasiado.
Incluye un panel de análisis y una API para conectarlo con el sistema de gestión.

- No graba video ni reconoce caras. Todo funciona dentro de la red del local.
- Especificación completa: [SPEC.md](SPEC.md).
- Licencia: [Apache-2.0](LICENSE).

## Estado del proyecto

| Fase | Contenido | Estado |
|---|---|---|
| F0 | Esqueleto: Docker Compose, Postgres + migraciones, API `/health`, dashboard vacío, CI | ✅ |
| F1 | Ingesta de cámaras (go2rtc), snapshots, benchmark de hardware | Pendiente |
| F2 | Editor de zonas y plano del salón | Pendiente |
| F3 | Detección + tracking + overlay + replay de video | Pendiente |
| F4 | Máquina de estados de mesa + clasificador de mozos | Pendiente |
| F5 | API completa, WebSocket, alertas, webhooks | Pendiente |
| F6 | Pantalla de alertas para PCs de tiqueo | Pendiente |
| F7 | Análisis y métricas | Pendiente |
| F8 | Cliente Python + guía de integración | Pendiente |
| F9 | Evaluación y ajuste | Pendiente |

## Instalación rápida (Docker)

Requisitos: Docker (en Windows: Docker Desktop con WSL2). Para usar placa de video NVIDIA hace
falta además el soporte de GPU de Docker (se documenta en F1).

```sh
cp .env.example .env      # completar contraseñas
docker compose up -d
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
