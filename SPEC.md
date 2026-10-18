# SPEC — Mesa Monitor
### Sistema de monitoreo de tiempos y estados de mesas por cámaras (MVP)

> **Para Claude Code:** este documento es la especificación completa del proyecto. Leelo entero antes de escribir código. Arrancá en **modo plan**: proponé un plan de implementación por fases (sección 18), señalá cualquier ambigüedad o contradicción que encuentres y esperá aprobación antes de empezar. Las decisiones marcadas como **[DECIDIDO]** no se discuten salvo que encuentres un impedimento técnico real; en ese caso explicalo y proponé alternativa.

---

## 0. Convenciones de trabajo

- **Idioma:** UI, documentación y mensajes al usuario en **español rioplatense**. Identificadores de código, nombres de tablas, variables y commits en **inglés**.
- **Verificá las APIs actuales** de `rfdetr`, `supervision`, `trackers` y `go2rtc` leyendo su documentación o el código fuente instalado antes de usarlas. Estas librerías cambian rápido; no asumas firmas de funciones de memoria.
- **Fijá versiones** de todas las dependencias (`pyproject.toml` con versiones exactas + lockfile; `package-lock.json` en el frontend).
- **Commits chicos y descriptivos**, uno o varios por fase. Al cerrar cada fase: tests en verde, README actualizado y un resumen de lo hecho y lo pendiente.
- Creá un `CLAUDE.md` en la raíz del repo con las convenciones del proyecto (estructura, comandos para correr tests, lint, levantar el entorno), para futuras sesiones.
- **Nunca** subas al repo credenciales, URLs de cámaras con usuario/clave, IPs reales, capturas o videos del local. Todo eso va en `.env` y `config/local/` (ignorados por git).
- Si algo de esta spec no se puede implementar como está escrito, no lo resuelvas en silencio: avisá y proponé.

---

## 1. Contexto del negocio

- Bar/café con salón principal, una galería con sillones, barra, caja y cocina. Muchas mesas.
- **Cámaras:** sistema **Dahua** (se gestiona con la app DMSS), grabador (NVR) de **9 canales**. Algunas cámaras tienen mal ángulo para ver mesas; se van a agregar 1 o 2 cámaras nuevas y reubicar otras. El sistema tiene que tolerar cambios de cámaras sin tocar código (todo por configuración).
- **Mozos:** visten de **negro** (remera/pantalón). No es un distintivo fuerte: hay clientes con ropa oscura y la resolución es baja. Puede que más adelante usen delantal de un color llamativo; el sistema debe permitir recalibrar eso.
- **PCs de tiqueo:** PCs comunes con navegador. Las alertas se muestran en una pestaña del navegador con sonido.
- **PC de procesamiento:** ya hay una PC disponible en el local. **No se conoce todavía su GPU ni su sistema operativo** (probablemente Windows). El sistema debe detectar el hardware, recomendar la configuración y funcionar con GPU NVIDIA (CUDA) o, en modo degradado, con CPU.
- **Sistema de gestión del dueño:** escrito en **Python**, privado (no está en GitHub), desarrollado por una persona **sin conocimientos técnicos** con ayuda de Claude. La integración tiene que ser a prueba de errores y estar documentada para que *su* Claude la implemente siguiendo instrucciones.

---

## 2. Alcance

### Dentro del MVP
1. Detección del **estado de cada mesa** y registro del tiempo de cada transición.
2. **Alertas** configurables cuando una mesa supera un tiempo en un estado.
3. **Dashboard de análisis** con métricas históricas.
4. **Tiempo real**: estados y alertas visibles en segundos.
5. **API + cliente Python liviano + guía de integración** para que el sistema de gestión del dueño consuma todo.
6. **Herramientas de calibración**: editor de zonas, calibración del distintivo de mozos, modo replay sobre video grabado, benchmark de hardware.

### Fuera del MVP (no implementar, pero no bloquear)
- Tiempos de cocina por plato (se hará con un KDS, pantalla en cocina con botón "listo"; a futuro llegará como otro emisor de eventos a la misma API).
- Integración con el sistema de comandas/POS (a futuro: el evento "pedido cargado" refinaría los estados).
- Reconocimiento facial o identificación de personas: **prohibido**, no se implementa nunca.
- Mesas que se unen: dejar el modelo de datos preparado (grupos de mesas), sin lógica en el MVP.

---

## 3. Decisiones de arquitectura [DECIDIDO]

### 3.1 Servicio separado, API-first
El sistema corre como **servicio independiente** (Docker Compose) con su propia base, su API y su dashboard. El sistema de gestión del dueño **no importa nada de visión**: se conecta a la API mediante un **cliente Python liviano** (sin PyTorch, sin GPU). Motivos: (a) no romper el proyecto del dueño con dependencias pesadas; (b) aislar licencias; (c) poder usar el dashboard propio de forma independiente.

### 3.2 Licencias: solo permisivas
- Permitido: **Apache-2.0, MIT, BSD**.
- **Prohibido:** `ultralytics` (AGPL-3.0), cualquier dependencia GPL/AGPL, y los componentes **`rfdetr_plus` / RF-DETR XL / 2XL** (licencia PML 1.0). Usar solo tamaños RF-DETR Nano / Small / Medium / Large con pesos Apache.
- Agregar un chequeo en CI (`pip-licenses` o similar) que **falle** si aparece una licencia GPL/AGPL/PML en el árbol de dependencias de Python.
- Licencia del repo: **Apache-2.0**.

### 3.3 Stack
| Capa | Tecnología |
|---|---|
| Ingesta de video | **go2rtc** (restream RTSP: una conexión por cámara al NVR, la comparten worker y dashboard; además WebRTC/MJPEG para ver en vivo) |
| Detección | **RF-DETR** (paquete `rfdetr`, Apache-2.0), pesos COCO; tamaño elegido por benchmark |
| Postura (opcional/fase 2 del clasificador) | Keypoints de RF-DETR si están disponibles con licencia Apache en la versión instalada; si no, heurística por geometría del bounding box |
| Tracking | **Roboflow `trackers`** (Apache-2.0), **OC-SORT por defecto**; tracker configurable (ByteTrack/SORT como alternativa). Cámaras fijas: no usar BoT-SORT con compensación de movimiento |
| Zonas / utilidades | **`supervision`** (PolygonZone, anotadores, utilidades de detección) |
| Backend | Python 3.11+, **FastAPI**, Pydantic v2, SQLAlchemy 2.x, Alembic |
| Base de datos | **PostgreSQL 16** (timestamps `timestamptz` en UTC; mostrar en `America/Argentina/Cordoba`) |
| Comunicación interna | Inserción en Postgres + **`LISTEN/NOTIFY`** para propagar eventos del worker a la API (evitamos sumar Redis) |
| Frontend | **React + Vite + TypeScript**, Tailwind, librería de gráficos (Recharts o similar), editor de polígonos sobre canvas |
| Despliegue | Docker Compose. Perfil `gpu` (runtime NVIDIA) y perfil `cpu`. Documentar instalación en **Windows con Docker Desktop + WSL2 + GPU NVIDIA** y en Linux |

---

## 4. Arquitectura

```
 ┌────────────┐   RTSP    ┌─────────┐  RTSP local  ┌──────────────────────────────┐
 │ NVR Dahua  │──────────▶│ go2rtc  │─────────────▶│ vision-worker                │
 │ 9 canales  │           │         │              │  frame grabber (N fps/cám)   │
 └────────────┘           └────┬────┘              │  → RF-DETR (personas+objetos)│
                               │ WebRTC/MJPEG      │  → OC-SORT (tracks por cám)  │
                               │ snapshots         │  → asignación a zonas        │
                               ▼                   │  → clasificador mozo/cliente │
                        ┌─────────────┐            │  → observaciones por mesa    │
                        │ dashboard   │            │  → STATE ENGINE (FSM/mesa)   │
                        │ (React)     │            └──────────────┬───────────────┘
                        └──────▲──────┘                           │ INSERT + NOTIFY
                               │ REST + WS                        ▼
 ┌──────────────────┐   REST + WS + webhooks   ┌─────────┐   ┌──────────────┐
 │ Sistema gestión  │◀────────────────────────▶│  api    │◀─▶│ PostgreSQL   │
 │ (cliente Python) │                          │ FastAPI │   └──────────────┘
 └──────────────────┘                          │ + alert │
 ┌──────────────────┐   WS                     │  engine │
 │ PCs de tiqueo    │◀────────────────────────▶│         │
 │ (/alertas)       │                          └─────────┘
 └──────────────────┘
```

### 4.1 Componentes
- **go2rtc:** su config se **genera automáticamente** desde `config/local/cameras.yaml`. Usar el **substream** (`subtype=1`) para detección y el main stream solo para snapshots de calibración. La plantilla de URL Dahua debe ser **configurable**, por defecto: `rtsp://{user}:{password}@{host}:{port}/cam/realmonitor?channel={channel}&subtype={subtype}`.
- **vision-worker:** proceso Python con un loop por cámara (hilos o asyncio + cola). Procesa a `fps_detection` configurable (default **3 fps**). Agrupa frames de varias cámaras en batch cuando el modelo lo permite. Reconexión con backoff exponencial si cae una cámara.
- **state engine:** módulo **puro** (sin IO, sin dependencias de visión): recibe observaciones con timestamp y devuelve transiciones y eventos. Determinista y 100% testeable con secuencias sintéticas. Vive en el worker, pero no depende de él.
- **api:** FastAPI. REST + WebSocket + webhooks salientes. Contiene el **motor de alertas** (evalúa umbrales sobre estados vigentes; corre un tick cada segundo).
- **dashboard:** SPA servida por la API (estáticos) o por un contenedor nginx.
- **client:** paquete Python independiente dentro del repo (`client/`), publicado como instalable vía git.

### 4.2 Recuperación ante fallos
- Si el worker se reinicia, **reconstruye el estado vigente de cada mesa desde la base** (sesiones abiertas, última transición) y continúa sin perder sesiones.
- Si una cámara cae, las mesas que dependen solo de ella pasan a `SIN_VISION`. **No se disparan alertas de atención** sobre mesas sin visión; se dispara una alerta `CAMARA_CAIDA`.
- **Horario operativo** configurable: fuera de horario el pipeline queda en reposo y al cierre se cierran las sesiones abiertas con motivo `closing_time`.

---

## 5. Configuración

### 5.1 Archivos
- `.env` (secretos): credenciales NVR, `DATABASE_URL`, `JWT_SECRET`, clave admin inicial, etc. Proveer `.env.example` con placeholders.
- `config/example/*.yaml` versionados (ejemplos sin datos reales). `config/local/*.yaml` ignorado por git.
- `cameras.yaml`: id, nombre, canal, `enabled`, fps de detección, resolución esperada del substream.
- Las **zonas, mesas, umbrales y plano del salón** se editan desde el dashboard y se guardan en la base. Debe existir **export/import a YAML** (backup y versionado manual).
- Los cambios de umbrales y zonas se aplican **en caliente**, sin reiniciar servicios (el worker escucha `NOTIFY config_changed`).

### 5.2 Modelo de mesas y zonas
- **Mesa** (`table`): id, etiqueta visible ("12", "G3"), sector (`salon`, `galeria`, ...), posición en el plano 2D del salón (x, y, ancho, alto, rotación), capacidad opcional, `enabled`, `group_id` opcional (para unir mesas a futuro).
- **Vista de mesa** (`table_view`): relación mesa ↔ cámara con un **polígono de mesa** (superficie) y un **polígono de asientos** (área donde se sienta la gente alrededor), más **prioridad**. Una mesa puede tener vistas en más de una cámara.
- **Zonas de staff** (`staff_zone`) por cámara: barra, pase de cocina, caja. Se usan para el clasificador de mozos.
- **Máscaras de ignorar** por cámara: áreas donde no se procesan detecciones (pasillo exterior, vidrieras, reflejos).
- **Fusión multi-cámara:** para cada mesa, usar la vista de mayor prioridad que esté sana; si la vista principal reporta oclusión fuerte o cámara caída, usar la siguiente. Documentar la regla en el código.

---

## 6. Pipeline de visión

1. **Captura:** frame del substream vía go2rtc a `fps_detection`.
2. **Detección (RF-DETR, pesos COCO):**
   - `person` → personas.
   - Objetos de mesa para el estado "sucia": `cup`, `bowl`, `wine glass`, `bottle`, `fork`, `knife`, `spoon` (configurable). Umbral de confianza configurable por clase.
3. **Filtro:** descartar detecciones dentro de máscaras de ignorar o con confianza baja.
4. **Tracking (OC-SORT)** por cámara → `track_id` estable por persona. Parámetros configurables (buffer de tracks perdidos, etc.).
5. **Asignación a zonas:** para cada persona trackeada, determinar si su "punto de anclaje" (ej. centro inferior del bbox, configurable) cae en el polígono de asientos de alguna mesa, en una zona de staff o en ninguna.
6. **Postura:** sentado/parado (ver sección 7).
7. **Clasificación staff/cliente** (sección 7).
8. **Observación por mesa por tick:** `{table_id, ts, seated_customers, standing_staff_present, staff_track_ids, object_count, view_health}` → state engine.
9. **Modo debug:** stream MJPEG anotado por cámara (bboxes, track ids, zonas, etiqueta staff/cliente) accesible desde el dashboard para calibrar. Apagado por defecto en producción.

---

## 7. Distinción mozo vs. cliente

El modelo detecta "persona", no "mozo". Implementar una **interfaz `StaffClassifier`** con implementaciones intercambiables:

### 7.1 `HeuristicStaffClassifier` (MVP)
Score ponderado por track, con **pesos y umbral configurables**:
- **Postura:** parado suma, sentado resta fuerte. Si hay keypoints, usarlos; si no, heurística por relación alto/ancho del bbox y posición relativa al polígono de asientos.
- **Trayectoria:** el track pasó por una zona de staff en los últimos N minutos (suma); visitó ≥ 2 mesas distintas (suma); permanencia larga sentado en una sola mesa (resta).
- **Apariencia:** similitud del histograma de color del torso (HSV) con un **perfil de staff calibrable** (hoy "negro"; mañana puede ser un delantal de color).
- Suavizado temporal: la etiqueta de un track se estabiliza con histéresis (no cambia de cliente a mozo frame a frame).

### 7.2 Calibración del perfil de staff
Herramienta en el dashboard: el admin selecciona en un snapshot a 3–10 mozos y a 3–10 clientes; el sistema calcula el perfil de color y sugiere pesos y umbral. Guardar el perfil en la base.

### 7.3 Preparado para un clasificador entrenado (post-MVP)
- Implementación futura `ModelStaffClassifier` (clasificador de recortes `staff` vs `customer`).
- Herramienta opcional de **recolección de datos**: con un flag explícito (apagado por defecto), guarda recortes de personas con metadata para etiquetar después. Retención limitada (configurable, default 7 días) y carpeta fuera del repo.

---

## 8. Máquina de estados de mesa

### 8.1 Estados
| Estado | Significado |
|---|---|
| `LIBRE` | Sin clientes, limpia (o sin objetos detectados) |
| `OCUPADA_SIN_ATENDER` | Clientes sentados, todavía sin visita de un mozo |
| `ATENDIDA` | Clientes sentados y ya hubo al menos una visita de mozo |
| `DESOCUPADA_SUCIA` | Los clientes se fueron y quedan objetos en la mesa (o todavía no pasó un mozo) |
| `SIN_VISION` | Ninguna vista sana de la mesa (cámara caída/oclusión total) |
| `DESHABILITADA` | Mesa desactivada por configuración |

### 8.2 Transiciones (todas con antirrebote configurable)
- `LIBRE → OCUPADA_SIN_ATENDER`: ≥ 1 cliente sentado durante `occupy_confirm_s` (default **20 s**). `seated_at` = **inicio** de esa ventana, no el final.
- `OCUPADA_SIN_ATENDER → ATENDIDA`: se registra una **visita de staff** (mozo parado dentro del polígono de asientos ampliado, permanencia entre `visit_min_s` = **4 s** y `visit_max_s` = **600 s**). `first_attention_at` = inicio de la visita. Pasadas de menos de `visit_min_s` no cuentan.
- En `ATENDIDA`: cada visita posterior se registra en `staff_visits` y reinicia el contador "desde la última visita".
- `OCUPADA_* / ATENDIDA → DESOCUPADA_SUCIA`: 0 clientes sentados durante `vacate_confirm_s` (default **120 s**, tolera ir al baño o salir a fumar). `vacated_at` = inicio del vacío. Si queda al menos una persona sentada, la mesa sigue ocupada.
- `DESOCUPADA_SUCIA → LIBRE`: visita de staff **y** `object_count ≤ clean_objects_max` (default 0) sostenido `clean_confirm_s` (default **15 s**). Si la detección de objetos no es confiable en esa vista, existe el modo configurable `clean_mode = staff_visit_only`. `cleaned_at` = fin de la confirmación.
- `DESOCUPADA_SUCIA → OCUPADA_SIN_ATENDER`: llegan clientes nuevos antes de limpiar. Se cierra la sesión anterior con `cleaned_at = null` y se abre una nueva.
- Cualquier estado `→ SIN_VISION` y retorno: pausar contadores de alerta, **no** cerrar la sesión. Si la falta de visión supera `vision_loss_close_s` (default 15 min), cerrar la sesión con motivo `vision_lost`.

### 8.3 Sesión de mesa
Cada ocupación genera una **sesión** con: `seated_at`, `first_attention_at`, lista de visitas, `vacated_at`, `cleaned_at`, `max_people`, motivo de cierre. Cada transición se guarda en `state_transitions` con `reason` y `confidence`.

### 8.4 Tests obligatorios del state engine
Tests de tabla con líneas de tiempo sintéticas: llegada normal; cliente que va al baño; mozo que pasa de largo (< 4 s); mozo que se queda conversando; mesa que se ocupa sucia; cámara que cae a mitad de la sesión; reinicio del worker en medio de una sesión (reconstrucción desde la base); cierre por horario.

---

## 9. Métricas

Definiciones por sesión *i* de una mesa:

| Símbolo | Fórmula | Significado | Unidad |
|---|---|---|---|
| `t_s` | — | Momento en que se sentaron (`seated_at`) | timestamp |
| `t_a` | — | Momento de la primera atención (`first_attention_at`) | timestamp |
| `t_v` | — | Momento en que quedó desocupada (`vacated_at`) | timestamp |
| `t_l` | — | Momento en que quedó limpia (`cleaned_at`) | timestamp |
| `T_esp` | `t_a − t_s` | Espera hasta la primera atención | min |
| `T_est` | `t_v − t_s` | Estadía de los clientes | min |
| `T_lim` | `t_l − t_v` | Tiempo de mesa sucia hasta limpiarse | min |
| `N_vis` | conteo | Cantidad de visitas de mozo en la sesión | visitas |
| `G_max` | `max(t_visita(k+1) − t_visita(k))` | Mayor intervalo sin visita durante la sesión | min |

Agregados por mesa / sector / franja horaria / día de semana / rango de fechas:

| Símbolo | Fórmula | Significado | Unidad |
|---|---|---|---|
| promedio, mediana, p90 | estadísticos sobre `T_esp`, `T_est`, `T_lim`, `G_max` | Distribución de tiempos | min |
| `R` | `N_ses / H_op` | Rotación: sesiones por mesa por hora operativa. `N_ses` = cantidad de sesiones (sesiones); `H_op` = horas operativas del período (h) | sesiones/h |
| `O` | `Σ T_est / (N_mesas · H_op · 60) · 100` | Ocupación. `Σ T_est` = suma de estadías (min); `N_mesas` = mesas habilitadas (mesas); `H_op` en horas | % |
| `SLA` | `N(T_esp ≤ U_1) / N_ses · 100` | % de sesiones atendidas dentro del umbral `U_1` (min) | % |

- Las métricas se calculan con SQL sobre `table_sessions` / `staff_visits` (vistas o materialized views refrescadas periódicamente).
- Excluir del cálculo las sesiones con datos incompletos (ej. `vision_lost`), pero informar cuántas se excluyeron.

---

## 10. Alertas

### 10.1 Tipos y umbrales por defecto (todos configurables)
| Tipo | Condición | Severidad | Default |
|---|---|---|---|
| `SIN_ATENCION` | `OCUPADA_SIN_ATENDER` ≥ `U_1` | warning | 3 min |
| `SIN_ATENCION` | `OCUPADA_SIN_ATENDER` ≥ `U_1_crit` | critical | 6 min |
| `SIN_VISITA` | `ATENDIDA` y tiempo desde la última visita ≥ `U_2` | warning | 15 min |
| `MESA_SUCIA` | `DESOCUPADA_SUCIA` ≥ `U_3` | warning | 5 min |
| `ESTADIA_LARGA` | estadía ≥ `U_4` | info (**desactivada por defecto**) | 120 min |
| `CAMARA_CAIDA` | sin frames ≥ `U_cam` | critical | 30 s |

### 10.2 Configuración
- Umbrales **globales** con **override por sector y por mesa**. Preparado (modelo de datos) para override por franja horaria; UI de franjas opcional.
- Editables desde el dashboard, guardados en la base, **aplicados en caliente**, con **auditoría** (quién cambió qué y cuándo).

### 10.3 Ciclo de vida
`abierta → (reconocida) → resuelta`.
- **Reconocer** ("Visto") desde la pantalla de alertas: silencia el sonido, la alerta sigue visible.
- **Posponer** N minutos.
- **Resolución automática** cuando la mesa cambia de estado.
- Escalamiento: si una warning pasa a critical, se actualiza la misma alerta (no se duplica).
- Deduplicación: una sola alerta activa por (mesa, tipo).

---

## 11. Interfaces de usuario

### 11.1 Pantalla de alertas para PCs de tiqueo — ruta `/alertas`
- Pensada para dejarla abierta todo el día en una PC modesta: liviana, tipografía grande, alto contraste, modo pantalla completa.
- **Plano del salón** con cada mesa coloreada por estado y su cronómetro, más una **lista de alertas activas** ordenada por severidad y antigüedad.
- **Sonido:** Web Audio API. Botón inicial "Activar sonido" (los navegadores exigen una interacción antes de reproducir audio). Sonido distinto para warning y critical; repetición configurable mientras la alerta no se reconozca.
- Notificaciones del navegador (Notification API) opcionales.
- **Reconexión automática** del WebSocket con indicador visible de "sin conexión".
- Acceso con **token de dispositivo** (link con token o PIN corto), sin login de usuario completo.

### 11.2 Dashboard (usuarios autenticados)
- **En vivo:** plano del salón con estados, cronómetros y alertas; acceso a la vista de cámara de cada mesa (stream de go2rtc) y al overlay de debug.
- **Análisis:** filtros por rango de fechas, sector, mesa y turno. Gráficos de espera promedio y p90 por hora, heatmap día × hora de `T_esp`, histograma de distribución, SLA %, rotación, ocupación, ranking de mesas con peores tiempos. **Export CSV.**
- **Sesiones:** listado con línea de tiempo por sesión (sentados → visitas → desocupada → limpia).
- **Configuración:** cámaras, **editor de zonas** (sobre snapshot de cada cámara: polígonos de mesa, asientos, staff e ignorar), **editor del plano del salón** (arrastrar y ubicar mesas), umbrales, perfil de staff, horarios operativos, usuarios, API keys, webhooks, tokens de dispositivo.
- **Salud del sistema:** fps real por cámara, latencia del pipeline, uso de GPU/CPU, estado de conexión de cada cámara.
- Roles: `admin` (todo) y `viewer` (ver y reconocer alertas).

---

## 12. API (contrato para el sistema de gestión)

Prefijo `/api/v1`. Autenticación: **JWT** para el dashboard y **API key** (header `X-API-Key`, guardada hasheada) para integraciones. OpenAPI generado automáticamente; además exportar los **JSON Schema de todos los eventos** a `schemas/`.

### 12.1 REST (mínimo)
```
GET    /health
GET    /state                         # snapshot de todas las mesas
GET    /tables                        # listado + config básica
GET    /tables/{id}
GET    /tables/{id}/sessions?from&to
GET    /sessions?from&to&sector&table
GET    /alerts?status=active|all&from&to
POST   /alerts/{id}/ack
POST   /alerts/{id}/snooze            # body: {minutes}
GET    /metrics/summary?from&to&sector&table&group_by=hour|day|weekday|table|sector
GET    /config/thresholds
PUT    /config/thresholds             # admin
CRUD   /cameras, /zones, /tables, /webhooks, /api-keys   # admin
GET    /cameras/{id}/snapshot         # admin
```

### 12.2 WebSocket `/api/v1/ws`
Al conectar envía `snapshot`; después eventos incrementales. Todos los mensajes llevan `schema_version`, `type`, `ts` (ISO-8601 UTC) e `id` de evento.
Tipos: `snapshot`, `table.state_changed`, `table.staff_visit`, `alert.opened`, `alert.updated`, `alert.resolved`, `camera.status`, `heartbeat` (cada 15 s).

Ejemplo:
```json
{
  "schema_version": 1,
  "id": "evt_01J...",
  "type": "alert.opened",
  "ts": "2026-10-18T20:31:05Z",
  "data": {
    "alert_id": "alr_123",
    "alert_type": "SIN_ATENCION",
    "severity": "warning",
    "table": {"id": "t12", "label": "12", "sector": "salon"},
    "state": "OCUPADA_SIN_ATENDER",
    "state_since": "2026-10-18T20:28:01Z",
    "elapsed_seconds": 184,
    "threshold_seconds": 180
  }
}
```

### 12.3 Webhooks salientes
URLs configurables por tipo de evento. Firma **HMAC-SHA256** en un header, reintentos con backoff exponencial y registro de entregas fallidas.

---

## 13. Cliente Python — `client/` (paquete `mesa_monitor_client`)

- Dependencias mínimas: `httpx`, `websockets`, `pydantic`. **Sin** torch, numpy, ni nada de visión.
- Instalable con `pip install "git+<repo>#subdirectory=client"`.
- API sync y async:
```python
from mesa_monitor_client import MesaMonitorClient

client = MesaMonitorClient(base_url=..., api_key=...)
client.get_state()
client.get_active_alerts()
client.ack_alert(alert_id)
client.get_metrics(from_=..., to=..., group_by="hour")


@client.on("alert.opened")
def handle(event): ...


client.start_in_background()  # hilo con reconexión automática (para apps sync: Flask, Django, Tkinter, Streamlit, scripts)
# o: await client.run_forever()  (apps async)
```
- Reconexión automática con backoff, reenvío del `snapshot` al reconectar, logging claro, errores con mensajes en español entendibles por alguien no técnico.
- Modelos Pydantic tipados para todos los eventos (generados o sincronizados con `schemas/`).
- Tests del cliente contra un servidor fake.

---

## 14. `INTEGRATION.md` — guía escrita para la IA del dueño

Documento en la raíz, **escrito para que lo lea un asistente de IA (Claude)** que ayuda a una persona **sin conocimientos técnicos** a integrar el módulo en su sistema de gestión en Python (framework desconocido). Debe incluir:
1. Qué es el sistema y qué datos entrega, en lenguaje simple.
2. Requisitos: URL del servidor y API key (dónde las consigue el dueño: dashboard → Configuración → API keys).
3. Instalación con un solo comando.
4. **Dónde guardar la API key** (`.env`, nunca en el código) con ejemplo.
5. Ejemplos completos y copiables: mostrar alertas activas, mostrar el estado de las mesas, recibir alertas en tiempo real, consultar métricas del día. Variantes para script simple, Flask, FastAPI, Django y Streamlit.
6. Qué **no** hacer: no instalar dependencias de visión, no conectarse directo a la base de datos, no abrir puertos al exterior.
7. Tabla de problemas comunes y soluciones (servidor apagado, API key inválida, sin conexión, reloj desfasado).
8. Un **prompt sugerido** que el dueño pueda pegarle a su Claude, del estilo: "Integrá el módulo Mesa Monitor en mi sistema siguiendo INTEGRATION.md; quiero una sección que muestre las alertas activas y el estado de las mesas".

---

## 15. Base de datos (mínimo)

`cameras`, `tables`, `table_groups`, `table_views` (polígonos JSON + prioridad), `staff_zones`, `ignore_masks`, `table_sessions`, `staff_visits`, `state_transitions`, `alerts`, `thresholds` (scope global/sector/table, clave, valor), `staff_profiles`, `operating_hours`, `camera_status_log`, `users`, `api_keys` (hash), `device_tokens`, `webhooks`, `webhook_deliveries`, `audit_log`.
- Migraciones con Alembic desde la fase 0.
- Índices por `(table_id, seated_at)`, `(status, opened_at)`, etc.
- Retención configurable para `state_transitions` y `camera_status_log` (las sesiones se guardan indefinidamente).

---

## 16. Rendimiento y hardware

- **`tools/benchmark.py`**: detecta GPU (nombre, VRAM, CUDA), CPU y RAM; mide fps reales de cada tamaño de RF-DETR con frames del substream; **recomienda** el tamaño de modelo y los fps por cámara para la cantidad de cámaras configuradas; escribe la recomendación en un archivo de config sugerido.
- Selección por defecto: Large/Medium con GPU; Small/Nano en CPU con fps reducidos.
- Objetivo: cambio de estado visible en el dashboard en ≤ **5 s** después de confirmado (sin contar el antirrebote).
- Optimización opcional posterior: export a ONNX/TensorRT si el benchmark muestra que hace falta.
- El worker debe funcionar con **una sola GPU** de gama media para 9–11 cámaras a 2–3 fps.

---

## 17. Privacidad y seguridad

- **Sin reconocimiento facial ni identificación** de personas, nunca. Los `track_id` son efímeros.
- **No se graba video.** Los snapshots de debug y la recolección de recortes están apagados por defecto; si se activan, tienen retención limitada y se guardan fuera del repo.
- Todo funciona en la **LAN del local** por defecto. Para acceso remoto, documentar Tailscale o Cloudflare Tunnel. **Nunca** exponer RTSP ni la base a internet.
- API keys hasheadas, CORS restringido, rate limiting básico en la API.
- `pre-commit` con escaneo de secretos (gitleaks o detect-secrets). `.gitignore` estricto (`.env`, `config/local/`, `media/`, `data/`, `*.mp4`, `*.jpg` fuera de `docs/`).

---

## 18. Fases de implementación y criterios de aceptación

| Fase | Entregable | Criterio de aceptación |
|---|---|---|
| **F0** Esqueleto | Estructura del repo, Docker Compose (perfiles gpu/cpu), Postgres + Alembic, FastAPI con `/health`, frontend vacío, CI (ruff, mypy, pytest, chequeo de licencias, escaneo de secretos), `CLAUDE.md`, `.env.example` | `docker compose up` levanta todo; CI en verde |
| **F1** Ingesta | Generador de config de go2rtc desde `cameras.yaml`, endpoint de snapshot, estado de cámaras, `tools/benchmark.py` | Se ven los snapshots de todas las cámaras en el dashboard y el benchmark da una recomendación |
| **F2** Calibración | Editor de zonas por cámara y editor del plano del salón; export/import YAML | Se pueden dibujar y guardar todas las mesas, zonas de staff y máscaras |
| **F3** Visión | Detección + tracking + asignación a zonas + overlay de debug + **modo replay sobre archivos de video** (`tools/replay.py`) | El overlay muestra personas trackeadas en sus mesas; el replay procesa un video grabado y genera observaciones |
| **F4** Estados | State engine puro + persistencia + clasificador heurístico de staff + calibración del perfil | Tests de la sección 8.4 en verde; el replay genera sesiones coherentes |
| **F5** API y alertas | REST, WebSocket, webhooks, motor de alertas, umbrales en caliente, auditoría | Una alerta aparece por WS ≤ 5 s después de cruzado el umbral |
| **F6** Pantalla de alertas | `/alertas` con plano, lista, sonido, reconocer/posponer, reconexión | Funciona horas seguidas en un navegador sin intervención |
| **F7** Análisis | Métricas SQL + vistas del dashboard + export CSV | Las métricas coinciden con un cálculo manual sobre datos de prueba |
| **F8** Integración | Cliente Python + `INTEGRATION.md` + ejemplos | Un script de ejemplo recibe alertas en vivo usando solo el cliente |
| **F9** Evaluación y ajuste | `tools/evaluate.py` (compara eventos detectados contra anotaciones manuales en CSV: diferencia en `seated_at`, `first_attention_at`, `vacated_at`; precisión de visitas detectadas), guía de ajuste, endurecimiento | Reporte de precisión sobre al menos 1 hora de video anotado |

### Formato de anotación manual (para F9)
CSV con columnas: `table_label, event, timestamp_video` donde `event ∈ {seated, first_attention, staff_visit, vacated, cleaned}`. El evaluador reporta errores medios (en segundos) por tipo de evento y visitas falsas positivas/negativas.

---

## 19. Estructura de repo sugerida

```
mesa-monitor/
├── SPEC.md                 # este documento
├── CLAUDE.md
├── README.md
├── INTEGRATION.md
├── LICENSE                 # Apache-2.0
├── docker-compose.yml
├── .env.example
├── config/
│   ├── example/            # versionado, sin datos reales
│   └── local/              # ignorado por git
├── src/mesa_monitor/
│   ├── core/               # state engine puro, modelos de dominio, métricas
│   ├── vision/             # captura, detección, tracking, zonas, staff classifier
│   ├── api/                # FastAPI, WS, webhooks, alert engine
│   ├── db/                 # modelos SQLAlchemy, repositorios
│   └── config/             # carga y validación de config
├── migrations/             # Alembic
├── dashboard/              # React + Vite + TS
├── client/                 # paquete mesa_monitor_client (liviano)
├── schemas/                # JSON Schema de eventos
├── tools/                  # benchmark.py, replay.py, evaluate.py, snapshot.py
├── tests/
└── docs/                   # instalación Windows/Linux, calibración, ajuste, acceso remoto
```

---

## 20. Datos que completa el usuario (no inventar)

Dejar placeholders y pedirlos cuando hagan falta:
- IP, puerto, usuario y contraseña del NVR Dahua; canales que se usan.
- Listado de mesas (etiqueta y sector).
- Horario operativo del local.
- GPU, CPU, RAM y sistema operativo de la PC de procesamiento (o correr `tools/benchmark.py`).
- Video de 1 a 2 horas de servicio en hora pico, exportado del NVR, para replay y evaluación (se guarda fuera del repo).

## 21. Preguntas abiertas para resolver durante el desarrollo
- Ubicación final de las cámaras nuevas: el editor de zonas debe facilitar evaluar qué mesas ve bien cada cámara (marcar mesas sin vista sana).
- Si el perfil "ropa negra" resulta insuficiente en el replay, recomendar al dueño un distintivo de color (delantal) y recalibrar.
- Validar si la detección de objetos (vasos, platos) es confiable desde los ángulos reales; si no, usar `clean_mode = staff_visit_only` en esas mesas.
---

## ANEXO A — Restricciones de cumplimiento (agregado 2026-10-18)

> **Para Claude Code:** este anexo complementa la sección 17 y tiene prioridad sobre cualquier cosa de la spec que lo contradiga. Si ya implementaste algo afectado, revisalo y ajustalo antes de seguir con la fase en curso; informá qué cambiaste.

**Finalidad del sistema:** medir cuánto tiempo pasa una mesa sin ser atendida, para mejorar el servicio y **ayudar** al personal con alertas. **No** es una herramienta de evaluación ni de control individual de empleados. Es una restricción de diseño, no solo de uso.

### A.1 Reglas
- **Ninguna identificación de empleados.** El sistema distingue "staff" de "cliente" solo para detectar visitas a mesas. Los `track_id` de staff existen solo en memoria y **no se persisten** asociados a visitas, sesiones ni métricas. `staff_visits` guarda inicio y fin de cada visita, **sin identidad** de quién la hizo.
- **Métricas solo por mesa, sector, franja horaria, día y turno.** **Prohibido:** métricas por empleado, rankings de personal, asignación mozo ↔ mesa o cualquier vista del tipo "desempeño del mozo X".
- **Lenguaje de la UI:** "atención de mesas" y "servicio"; nunca "desempeño" ni "productividad del personal".
- **Integración futura con el POS:** si el POS trae el mozo asignado a cada mesa, **no** cruzar ese dato con métricas de cámara sin revisión legal previa. Documentarlo como restricción en el código (comentario en el módulo de métricas) y en `docs/cumplimiento.md`.

### A.2 Documentación a generar: `docs/cumplimiento.md`
- Finalidad del sistema en lenguaje simple.
- Checklist para el dueño del local:
  - [ ] Notificar por escrito a los empleados la existencia y finalidad del sistema (art. 71 LCT).
  - [ ] Actualizar el cartel de videovigilancia incluyendo la nueva finalidad (Disp. 10/2015 AAIP y modificatorias).
  - [ ] Actualizar el manual de tratamiento y la inscripción de la base de videovigilancia ante la AAIP.
  - [ ] Consulta con un abogado laboralista antes de pasar a producción (protocolo de cámaras del Ministerio de Trabajo de Córdoba), incluyendo si se piensa usar alguna métrica para incentivos grupales.
- **Borrador** de notificación a empleados, marcado claramente como "a revisar por un abogado".

### A.3 Verificación
- Agregar un test que recorra el esquema de la base y falle si alguna tabla de métricas, visitas o sesiones tiene columnas que identifiquen a un empleado (`staff_id`, `waiter_id`, `employee_*`, `track_id` persistido).
- Revisar endpoints de `/metrics`: ningún `group_by` por persona.
