# Mesa Monitor

Sistema que mira las cámaras del local y registra **el estado de cada mesa** (libre, ocupada,
atendida, sucia), cuánto tiempo pasa en cada estado y **avisa** cuando una mesa espera demasiado.
Incluye una pantalla de alertas para las PCs del local, un panel de análisis y una API para
conectarlo con el sistema de gestión.

- No graba video ni reconoce caras. Todo funciona dentro de la red del local.
- Mide la atención de **mesas**, no el trabajo de cada empleado: no sabe qué mozo atendió qué mesa.
  Antes de usarlo en el local, ver [docs/cumplimiento.md](docs/cumplimiento.md).
- Funciona con **cualquier sistema de cámaras** que entregue video por red (ver abajo).
- Especificación: [SPEC.md](SPEC.md) · Licencia: [Apache-2.0](LICENSE).

## Qué incluye

| Parte | Qué hace |
|---|---|
| **Pantalla `/alertas`** | Para las PCs de tiqueo: plano del salón con el estado y el cronómetro de cada mesa, alertas con sonido, "Visto" y "Posponer". Se abre con un link o un PIN. |
| **Dashboard** | En vivo, análisis (espera, % atendido a tiempo, rotación, ocupación, mapa de calor, CSV), sesiones, salud del sistema y toda la configuración. |
| **Calibración** | Editor de zonas sobre la imagen de cada cámara, plano del salón arrastrando mesas, perfil de la ropa del personal, respaldo en YAML. |
| **Visión** | RF-DETR (detección por mosaicos) + OC-SORT, distinción personal/cliente y máquina de estados por mesa. |
| **Integración** | API REST + WebSocket + webhooks firmados y un cliente Python liviano ([INTEGRATION.md](INTEGRATION.md)). |
| **Herramientas** | Benchmark del hardware, replay sobre video grabado (genera un video con el análisis dibujado) y evaluación de precisión. |

## Instalación rápida (Docker)

Guía completa (Windows con GPU, Linux, benchmark): [docs/instalacion.md](docs/instalacion.md).

```sh
cp .env.example .env                                        # contraseñas, JWT_SECRET y datos del NVR
cp config/example/cameras.yaml config/local/cameras.yaml    # cámaras (ver la sección siguiente)
docker compose up -d --build
```

Abrir <http://localhost:8000> (o `http://IP-DE-LA-PC:8000` desde otra PC del local). La API está
documentada en <http://localhost:8000/api/v1/docs>. En `.env`, `COMPOSE_PROFILES=gpu` usa una placa
NVIDIA y `COMPOSE_PROFILES=cpu` el procesador.

## Conectar cualquier sistema de cámaras

Las cámaras se declaran en `config/local/cameras.yaml` (no se sube al repo). Las contraseñas van en
`.env` y se referencian como `${VARIABLE}`. Sirve cualquier equipo que entregue video por **RTSP,
ONVIF, HTTP/MJPEG** o un **archivo**: la conexión la hace [go2rtc](https://github.com/AlexxIT/go2rtc).

Conviene usar el **stream secundario** (de menor resolución) para la detección: el sistema lo pide
solo y usa el principal únicamente para las fotos de calibración.

### 1. Grabador (NVR/DVR) o cámaras de una marca conocida: `preset`

```yaml
connections:
  nvr:
    preset: hikvision          # dahua (por defecto) · amcrest · intelbras · hikvision · ezviz
                               # · uniview · reolink · tapo · onvif
    host: ${NVR_HOST}
    port: ${NVR_PORT:-554}
    user: ${NVR_USER}
    password: ${NVR_PASSWORD}

cameras:
  - { id: salon-1, name: Salón fondo, channel: 1 }     # channel = número de cámara en el NVR
  - { id: salon-2, name: Salón entrada, channel: 2 }
```

| `preset` | URL que se arma (stream secundario) |
|---|---|
| `dahua`, `amcrest`, `intelbras` | `rtsp://…/cam/realmonitor?channel=1&subtype=1` |
| `hikvision`, `ezviz` | `rtsp://…/Streaming/Channels/102` |
| `uniview` | `rtsp://…/unicast/c1/s1/live` |
| `reolink` | `rtsp://…/h264Preview_01_sub` |
| `tapo` | `rtsp://…/stream2` |
| `onvif` | `onvif://usuario:clave@ip:puerto` (go2rtc descubre las URLs solo) |

### 2. Cualquier otra marca: `url_template`

Con la URL RTSP que figura en el manual del equipo, reemplazando las partes variables:

```yaml
connections:
  otra-marca:
    host: ${CAM_HOST}
    user: ${CAM_USER}
    password: ${CAM_PASSWORD}
    url_template: "rtsp://{user}:{password}@{host}:{port}/live/ch{channel}/{quality}"
```

Variables: `{user}` `{password}` (se escapan solas) `{host}` `{port}` `{channel}` `{channel2}`
(01, 02…) `{subtype}` (0 principal / 1 secundario) `{stream_n}` (1 principal / 2 secundario)
`{quality}` (`main` / `sub`).

### 3. Una cámara suelta con su propia URL: `source`

```yaml
cameras:
  - id: galeria
    name: Galería
    source: "rtsp://${CAM10_USER}:${CAM10_PASSWORD}@${CAM10_HOST}:554/stream2"   # baja (detección)
    source_main: "rtsp://${CAM10_USER}:${CAM10_PASSWORD}@${CAM10_HOST}:554/stream1"  # alta (fotos)
```

`source` acepta cualquier fuente de go2rtc: `rtsp://`, `onvif://`, `http://…/video.mjpg` (cámaras
que solo dan MJPEG o JPEG), `ffmpeg:/ruta/video.mp4` (un video, útil para probar sin cámaras) o
`ffmpeg:virtual?video=testsrc&size=704x480#video=h264` (imagen de prueba).

### Probar la conexión

1. `docker compose up -d --force-recreate go2rtc-config go2rtc` (después de editar el archivo).
2. En la misma PC, <http://localhost:1984> (consola de go2rtc): cada cámara aparece dos veces
   (`id` = secundario, `id_main` = principal). Si se ve ahí, el sistema la puede usar.
3. En el dashboard, **Cámaras**: todas tienen que figurar "En línea".

Si no conecta: verificar usuario/clave con la app del fabricante, que el stream secundario esté
habilitado en **H.264 o H.265** (no MJPEG en los Dahua) y que la PC esté en la misma red. Detalles
en [docs/instalacion.md](docs/instalacion.md).

## Ver el análisis sobre un video grabado (demo)

Con un video exportado del NVR y un respaldo de zonas (dashboard → Respaldo):

```sh
uv run python tools/replay.py VIDEO.dav --zones respaldo.yaml --camera salon-1 --annotate --video-fps 15
```

Genera `data/replay/VIDEO/annotated.mp4`: cada mesa pintada con su estado, el cronómetro y la
cantidad de personas, y el personal resaltado. Los videos nunca se suben al repo.

## Documentación

| Para | Documento |
|---|---|
| Instalar (Windows/Linux, GPU, benchmark) | [docs/instalacion.md](docs/instalacion.md) |
| Calibrar zonas, perfil de personal y postura | [docs/calibracion.md](docs/calibracion.md) |
| Medir la precisión y ajustar | [docs/ajuste.md](docs/ajuste.md) |
| Operación diaria, backups, actualización | [docs/operacion.md](docs/operacion.md) |
| Acceso desde fuera del local | [docs/acceso-remoto.md](docs/acceso-remoto.md) |
| Cumplimiento (empleados, AAIP) | [docs/cumplimiento.md](docs/cumplimiento.md) |
| Integrar con el sistema de gestión | [INTEGRATION.md](INTEGRATION.md) |
| Desarrollo (estructura, comandos, decisiones) | [CLAUDE.md](CLAUDE.md) |

## Estado

Todas las fases de la especificación (F0–F9) están implementadas y probadas (tests, CI y pruebas
de punta a punta con un video real de un café). Falta lo que solo se puede hacer en el local:

1. Cargar las cámaras reales y correr el benchmark en la PC del local (con GPU).
2. Dibujar mesas y zonas, y calibrar el perfil de la ropa del personal.
3. Medir la precisión con una hora de video anotado ([docs/ajuste.md](docs/ajuste.md)).
4. Notificar a los empleados y hacer la consulta legal ([docs/cumplimiento.md](docs/cumplimiento.md)).

## Desarrollo

```sh
make sync     # instala dependencias (necesita uv: https://docs.astral.sh/uv/)
make check    # lint + tipos + tests + licencias + dashboard
```

Estructura, convenciones y trampas conocidas: [CLAUDE.md](CLAUDE.md).
