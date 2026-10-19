# Estado del proyecto — qué hicimos, dónde estamos, a dónde vamos

> Escrito para alguien que se suma al proyecto (y para su Claude Code). Fecha: 2026-10-20.
> Repo: <https://github.com/ignaciolinzoain4/mesa-monitor> (público, Apache-2.0).
> Si sos un asistente de IA leyendo esto: la especificación completa está en `SPEC.md`, las
> convenciones y trampas conocidas en `CLAUDE.md`. Este documento es el resumen narrativo.

## 1. Qué es Mesa Monitor

Un sistema que mira las cámaras de un bar/café (NVR Dahua, pero sirve cualquier marca) y, para cada
mesa, sabe **en qué estado está y hace cuánto**:

| Estado | Significa | Cómo lo detecta |
|---|---|---|
| **Libre** | vacía y limpia | nadie sentado |
| **Sin atender** | clientes sentados, todavía no pasó un mozo | sentados sostenidos `occupy_confirm_s` (20 s) |
| **Atendidos** | ya pasó un mozo (carta, pedido) | visita de personal ≥ `visit_min_s` (4 s) en la mesa |
| **Consumiendo** | hay comida o bebida en la mesa | objetos de vajilla/comida (COCO) sobre la mesa en una ventana de 30 s |
| **Sucia** | se fueron y falta limpiar | nadie sentado `vacate_confirm_s` (120 s) |
| *Sin visión* / *Deshabilitada* | cámara caída / mesa apagada | salud del stream / configuración |

Con eso genera **alertas** (mesa sin atender hace X minutos, sucia hace X minutos, cámara caída),
**métricas** (espera hasta la primera atención, % atendido a tiempo, rotación, ocupación, por
mesa/sector/franja/día/turno) y una **API** para que el sistema de gestión del local lo consuma.

Restricciones duras (no negociables, ver `SPEC.md` Anexo A y `docs/cumplimiento.md`):
- **No identifica personas**: no hay reconocimiento facial, los `track_id` viven solo en memoria y
  nunca se guardan. No hay métricas por empleado ni rankings: se mide la atención de **mesas**.
- No graba video. Todo corre dentro de la red del local.
- Solo dependencias con licencia permisiva (Apache/MIT/BSD): por eso RF-DETR y no YOLO de
  Ultralytics (AGPL), asyncpg y no psycopg (LGPL). `tools/check_licenses.py` lo valida en CI.

## 2. Qué hicimos

### 2.1 Todo el sistema (fases F0–F9 de la spec)

| Pieza | Tecnología | Dónde |
|---|---|---|
| Ingesta de cámaras | **go2rtc** (restream RTSP/ONVIF/HTTP); config generada desde `config/local/cameras.yaml` con presets por marca (dahua, hikvision, reolink, tapo, onvif, …) | `src/mesa_monitor/config/cameras.py` |
| Detección | **RF-DETR** Medium (pesos COCO, Apache-2.0) con **mosaicos** (`supervision.InferenceSlicer`) sobre la región de interés + una pasada sobre la región entera | `vision/detector.py` |
| Tracking | **OC-SORT** (Roboflow `trackers`) | `vision/detector.py` |
| Escena | punto de apoyo de cada persona → zona de asientos de cada mesa; postura por proporción alto/ancho + movimiento | `vision/scene.py` |
| Mozo vs. cliente | clasificador heurístico con puntaje + reglas duras (ver 3.2) | `vision/staff.py` |
| Máquina de estados | pura, sin IO, con antirrebote (`Sustain`) y tests de líneas de tiempo sintéticas | `core/state_engine.py` |
| Persistencia | PostgreSQL 16 + SQLAlchemy 2 async + Alembic; bandeja de eventos + `LISTEN/NOTIFY` | `vision/persistence.py`, `db/`, `migrations/` |
| API | FastAPI: REST, WebSocket, webhooks firmados HMAC-SHA256, JWT / API keys / tokens de dispositivo | `api/` |
| Pantallas | React + Vite + TS + Tailwind: dashboard en vivo, análisis (Recharts), sesiones, calibración de zonas sobre la imagen, plano del salón, `/alertas` con sonido para las PCs de tiqueo | `dashboard/` |
| Integración | cliente Python liviano `mesa_monitor_client` + `INTEGRATION.md` (guía escrita para el Claude del dueño) + JSON Schemas de los eventos | `client/`, `schemas/` |
| Herramientas | `tools/replay.py` (procesa un video grabado con el mismo pipeline y genera un mp4 con el análisis dibujado), `tools/evaluate.py` (precisión contra anotaciones manuales), `tools/benchmark.py` (elige modelo/fps según el hardware) | `tools/` |
| Despliegue | Docker Compose con perfiles `gpu` (CUDA) / `cpu`; CI en GitHub Actions (lint, mypy, pytest con Postgres, licencias, gitleaks, build de imágenes) | `docker-compose.yml`, `.github/` |

~10.000 líneas de Python, ~250 tests, CI en verde.

### 2.2 Validación con video real (lo más importante de las últimas iteraciones)

Usamos un video de 28 minutos de una cámara cenital gran angular de un café (exportado del NVR
Dahua, `.dav`). El video **no está en el repo** (privacidad). Dibujamos 9 mesas (numeradas 10 a 18),
una zona de personal (barra/cafetera) y la caja, y corrimos `tools/replay.py`.

Se hicieron dos rondas de revisión cuadro por cuadro contra lo que realmente pasaba en el video. Lo
que se aprendió y se corrigió (detalle técnico en `CLAUDE.md` → "Trampas conocidas"):

**Detección y tracking**
- La imagen entera a la resolución del modelo pierde a la mayoría de las personas chicas del fondo
  → detección por mosaicos sobre la región de las zonas (15 → 41 personas detectadas).
- **OC-SORT solo crea tracks con confianza ≥ 0.6** por defecto: la gente del fondo (0.4–0.6) nunca
  se seguía y sus mesas figuraban libres → `TrackerParams.high_conf = 0.4`.
- Las personas sobre el **borde entre dos mosaicos** salían como dos "medias personas" (dos clientes
  o dos mozos) → pasada adicional sobre la región entera + `suppress_contained` (saca cajas
  contenidas en otra más grande).

**Postura y ocupación**
- Con la cámara alta, alguien sentado de costado se ve tan "alargado" como alguien parado
  (proporción 1.3–2.8 sentados vs. 2.9–3.9 parados) → se combina con movimiento: quien está
  **quieto en una silla** cuenta como sentado.
- El tamaño del grupo es la **mediana** de una ventana de 20 s (un duplicado no hace "3 personas"
  en una mesa de 2).
- Para dar una mesa por vacía solo cuentan los sentados o quietos: contar "cualquiera dentro de la
  zona de asientos" hacía que las mesas **nunca** se vaciaran (vecinos, gente de paso).

**Mozos** (lo más difícil: visten de negro, pero muchos clientes también)
- Sentado en una mesa = cliente, siempre (incluye "quieto en la silla aunque se vea parado").
- La zona de personal solo suma si la persona **se queda** ahí unos segundos (un cliente que paga
  o pasa no se convierte en mozo).
- Sin perfil de color calibrado se usa la fracción de píxeles oscuros del torso.
- Un mozo que el sistema no vio salir de la barra se reconoce por estar **de pie un rato y
  desplazándose** por el salón (medido con la cabeza, no los pies, porque las piernas se tapan).
- **Traspaso de identidad**: si el tracker pierde a un mozo y aparece un track nuevo al lado, hereda
  la marca; si el tracker le pasa el id de un cliente sentado a otra persona, no hereda nada.
- La visita se atribuye a **una sola mesa** (la más cercana), medida desde la cadera: con las
  piernas tapadas la caja termina en las rodillas y el "pie" caía en la mesa de adelante.

**Comida / consumo**
- Clases COCO de vajilla y comida (cup, bowl, wine glass, bottle, cubiertos, sandwich, pizza,
  cake, …). Los platos de las mesas lejanas salen con confianza 0.25–0.30: el umbral depende del
  **tamaño de la mesa en la imagen** (0.25 chicas, 0.32 grandes).
- Un objeto que parpadea (un servilletero confundido con un vaso) no alcanza: tiene que aparecer
  en ≥ 40 % de los cuadros de una ventana de 30 s.
- Lo que ya estaba en la mesa al sentarse no cuenta como consumo.

**Arranque, cortes y casos borde**
- Al arrancar (o tras un corte de video/reinicio de más de 5 min) no se sabe qué pasó antes: cada
  mesa se observa ~45 s ("Verificando") y se decide el estado con lo que se ve (con comida →
  Consumiendo; con gente sin comida → Sin atender). Esas sesiones quedan marcadas `partial` y no
  entran en las métricas de espera.
- Una mesa sucia pasa a Libre aunque no se vea al mozo, si se ve vacía de vajilla un rato (solo en
  mesas donde la cámara sí ve la vajilla).
- Clientes que se sientan un par de minutos y se van sin atención ni consumo (se cambiaron de
  mesa): la sesión se cierra como `no_service`, la mesa vuelve a Libre y no cuenta en métricas.

**Presentación**: el video de análisis muestra cada mesa con un relleno suave del color del estado y
una etiqueta con número, tiempo en el estado y personas; los mozos con una marca "MOZO"; la
leyenda arriba a la derecha y avisos tipo notificación arriba a la izquierda. Sin cajas ni líneas
de calibración (eso solo aparece en modo calibración).

## 3. Dónde estamos parados

### 3.1 Lo que funciona bien

- La infraestructura completa (cámaras → detección → estados → base → API → pantallas → alertas
  → métricas) está hecha, testeada y empaquetada en Docker. Falta instalarla en el local.
- En el video de prueba, las mesas **grandes y cercanas a la cámara** (11, 12, 13) siguen bien su
  estado todo el tiempo.
- El arranque con mesas ya ocupadas: 12, 13, 14 y 15 empiezan directamente en "Consumiendo"; la
  10 pasa a "Consumiendo" cuando realmente le llega la comida (no por objetos falsos).
- La comida que llega a las mesas lejanas ahora se detecta (17 y 18), con algo de demora.

### 3.2 Lo que todavía falla (medido en el video)

1. **Mozos que atienden lejos de la barra**: muchas visitas reales no se detectan, entonces mesas
   que ya fueron atendidas pasan directo de "Sin atender" a "Consumiendo" sin pasar por
   "Atendidos". También hay algunos falsos mozos (clientes caminando hacia la caja).
2. **Mesas del fondo y de los costados (16, 17, 18)**: chicas en la imagen, medio tapadas por una
   columna o por otras personas. Se detecta menos gente, menos vajilla y menos visitas.
3. **Vaciado y limpieza**: mejoró mucho (una mesa que antes se veía vacía 14 minutos tarde ahora
   ~3 minutos), pero sigue habiendo mesas cuyo vaciado se detecta tarde o no se detecta.
4. Todo corre en una Mac sin GPU a ~2.3 cuadros por segundo de detección; en vivo, con una NVIDIA
   en el local, se puede usar más fps y mejor modelo.

Honestamente: como **primer acercamiento con una sola cámara de mala calidad** el resultado es
bueno y demuestra que el enfoque funciona; para usarlo en serio hace falta lo de la sección 4.

## 4. A dónde vamos

### 4.1 Próximos pasos técnicos (en orden)

1. **Mozos**: mejorar la detección de visitas en el salón (combinación color + permanencia junto a
   una mesa ocupada + recorrido), y medirlo con `tools/evaluate.py` contra anotaciones manuales.
2. **Calibración del perfil de personal** desde el dashboard con imágenes reales (marcar 3–10 mozos
   y 3–10 clientes). Si el color no alcanza (remera negra), la solución más efectiva es un
   **distintivo**: delantal o remera de color llamativo → recalibrar y el problema 1 se achica mucho.
3. **Más cobertura**: una o dos cámaras con mejor ángulo para las mesas que hoy se ven chicas o
   tapadas. El sistema ya soporta varias cámaras por mesa con prioridad (usa la que mejor la ve).
4. **Evaluación formal (F9)**: anotar a mano 1 hora de video (CSV `table_label, event,
   timestamp_video`, ver `docs/ajuste.md`) y sacar errores medios por evento y precisión de visitas.
5. **Instalación en el local** con GPU, benchmark, dibujar todas las mesas en el editor de zonas.
6. Más adelante (fuera del MVP): KDS de cocina y eventos del POS ("pedido cargado") para refinar
   los estados; mesas que se unen.

### 4.2 Lo que necesitamos del local / del sistema de cámaras

- Acceso al **NVR** (IP, usuario, puerto RTSP; los mismos datos que la app DMSS). Las credenciales
  van en `.env`, nunca al repo.
- El **substream** de cada cámara en **H.264 o H.265** (no MJPEG en Dahua).
- Saber qué **PC** procesa: sistema operativo y placa de video (idealmente NVIDIA ≥ 6 GB).
- Más video exportado de distintos horarios (mucha gente / poca gente / noche).
- Antes de usarlo con empleados: **avisarles** y hacer la consulta legal (`docs/cumplimiento.md`).

## 5. Cómo probarlo (para tu Claude Code)

```sh
git clone https://github.com/ignaciolinzoain4/mesa-monitor.git && cd mesa-monitor
make sync                      # uv sync --extra vision --extra cpu  (necesita uv)
make check                     # lint + mypy + tests + licencias + dashboard

# Analizar un video exportado del NVR con zonas dibujadas (respaldo YAML del dashboard):
PYTHONPATH=src uv run python tools/replay.py VIDEO.dav --zones zonas.yaml --camera ID \
    --annotate --video-fps 15
# → data/replay/VIDEO/annotated.mp4, events.jsonl, sessions.csv

# Sistema completo:
cp .env.example .env && cp config/example/cameras.yaml config/local/cameras.yaml
docker compose up -d --build   # http://localhost:8000
```

Formato del YAML de zonas (coordenadas normalizadas 0–1 sobre la imagen de la cámara):

```yaml
version: 1
cameras:
  cafe:
    table_views:
      - table: "10"
        table_polygon: [[0.325,0.435],[0.39,0.395],[0.435,0.425],[0.37,0.48]]   # la mesa
        seats_polygon: [[0.29,0.47],[0.44,0.44],[0.48,0.50],[0.47,0.59],[0.29,0.60]]  # donde apoyan los pies los sentados
    staff_zones:
      - {kind: bar, name: Barra, polygon: [[0.0,0.30],[0.13,0.30],[0.17,0.40],[0.17,0.57],[0.0,0.60]]}
    ignore_masks:
      - {name: Fecha OSD, polygon: [[0.66,0.03],[0.99,0.03],[0.99,0.10],[0.66,0.10]]}
```

Puntos de entrada para leer el código, en orden: `core/state_engine.py` (reglas de estados y sus
parámetros en `EngineParams`), `vision/scene.py` (de cajas a "sentados/mozo por mesa"),
`vision/staff.py` (mozo vs. cliente), `vision/pipeline.py` (pipeline por cámara y el overlay del
video), `tools/replay.py`. Los tests de `tests/test_state_engine.py` y `tests/test_scene_staff.py`
describen cada caso aprendido del video real, con su nombre en español.
