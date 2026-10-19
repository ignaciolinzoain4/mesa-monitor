# INTEGRATION.md — Integrar Mesa Monitor en un sistema de gestión en Python

> **Para el asistente de IA que lee esto:** vas a ayudar a una persona **sin conocimientos
> técnicos** a conectar su sistema de gestión (Python, framework a determinar) con Mesa Monitor.
> Seguí esta guía al pie de la letra. Explicale cada paso en lenguaje simple, pedile los datos de a
> uno y no le hagas tocar nada fuera de lo que se describe acá. Antes de escribir código, averiguá
> qué framework usa su proyecto (mirá si hay `manage.py` → Django; `from flask` → Flask;
> `from fastapi` → FastAPI; `import streamlit` → Streamlit; si no, es un script).

---

## 1. Qué es Mesa Monitor y qué datos entrega

Mesa Monitor es un sistema aparte (corre en otra computadora del local) que mira las cámaras y
sabe, para cada mesa, en qué estado está y hace cuánto:

| Estado | Qué significa |
|---|---|
| `LIBRE` | Mesa vacía y limpia |
| `OCUPADA_SIN_ATENDER` | Hay clientes sentados y todavía no pasó un mozo |
| `ATENDIDA` | Hay clientes y ya fueron atendidos (pasó un mozo) |
| `CONSUMIENDO` | Ya tienen comida o bebida en la mesa |
| `DESOCUPADA_SUCIA` | Los clientes se fueron y falta limpiar |
| `SIN_VISION` | La cámara no ve la mesa en este momento |
| `DESHABILITADA` | Mesa desactivada |

Además genera **alertas** cuando una mesa espera demasiado:

| Alerta | Cuándo |
|---|---|
| `SIN_ATENCION` | Clientes sentados sin atender hace más de X minutos (aviso y crítica) |
| `SIN_VISITA` | Hace mucho que el mozo no pasa por una mesa atendida |
| `MESA_SUCIA` | Mesa sin limpiar hace más de X minutos |
| `ESTADIA_LARGA` | Clientes hace mucho tiempo (opcional) |
| `CAMARA_CAIDA` | Una cámara no envía imagen |

Cuando el sistema arranca con mesas ya ocupadas no sabe cuándo se sentaron: esas sesiones vienen
con `partial: true`, arrancan en `CONSUMIENDO` si ya hay comida o bebida en la mesa y no cuentan
para las métricas de espera. `served_at` indica cuándo apareció la comida o bebida en la mesa.

Y **métricas**: espera promedio hasta la primera atención, porcentaje de mesas atendidas a tiempo,
rotación, ocupación, etc. Las métricas son siempre por **mesa, sector, horario o día**, nunca por
empleado (es una regla del sistema, ver `docs/cumplimiento.md`).

El sistema de gestión **no instala nada de cámaras ni de inteligencia artificial**: solo usa un
paquete chico (`mesa-monitor-client`) que se conecta por la red local.

## 2. Qué se necesita

1. **La dirección del servidor**, por ejemplo `http://192.168.1.50:8000`. Es la PC donde corre
   Mesa Monitor. La persona la puede ver en el navegador de esa PC (la dirección con la que entra
   al dashboard) o preguntársela a quien lo instaló.
2. **Una API key**. Se crea en el dashboard de Mesa Monitor: **Configuración → Accesos → API keys
   → "Crear API key"**. Empieza con `mm_`. **Se muestra una sola vez**: copiarla en ese momento.

Prueba rápida (desde la PC del sistema de gestión, en el navegador):
`http://DIRECCIÓN:8000/api/v1/health` tiene que mostrar `"status":"ok"`.

## 3. Instalación (un solo comando)

Con el entorno virtual del proyecto activado (si usa uno):

```bash
pip install "git+https://github.com/ignaciolinzoain4/mesa-monitor.git#subdirectory=client"
```

Si el proyecto tiene `requirements.txt`, agregá esta línea y volvé a instalar:

```
mesa-monitor-client @ git+https://github.com/ignaciolinzoain4/mesa-monitor.git#subdirectory=client
```

Requiere Python 3.10 o superior. Instala solo `httpx`, `websockets` y `pydantic`.

## 4. Dónde guardar la API key (nunca en el código)

Crear (o completar) el archivo `.env` en la carpeta del proyecto:

```dotenv
MESA_MONITOR_URL=http://192.168.1.50:8000
MESA_MONITOR_API_KEY=mm_pegar_aca_la_clave
```

- Verificá que `.env` esté en `.gitignore` (si el proyecto usa git). Si no está, agregalo.
- Para leer el `.env`: si el proyecto ya usa `python-dotenv` (`load_dotenv()`), alcanza con eso.
  Si no, `pip install python-dotenv` y al inicio del programa:

```python
from dotenv import load_dotenv

load_dotenv()
```

En el código, la clave se lee siempre así: `os.environ["MESA_MONITOR_API_KEY"]`.

## 5. Ejemplos completos

Todos los ejemplos completos y probados están en `client/examples/` del repositorio. Lo esencial:

### Crear el cliente (una sola vez para todo el programa)

```python
import os
from mesa_monitor_client import MesaMonitorClient

client = MesaMonitorClient(
    base_url=os.environ["MESA_MONITOR_URL"],
    api_key=os.environ["MESA_MONITOR_API_KEY"],
)
```

### Mostrar el estado de las mesas

```python
estado = client.get_state()
for mesa in estado.tables:
    minutos = (mesa.elapsed_seconds or 0) / 60
    print(mesa.table.label, mesa.state.value, f"{minutos:.0f} min", mesa.people, "personas")
```

### Mostrar las alertas activas

```python
for alerta in client.get_active_alerts():
    print(alerta.where, alerta.alert_type.value, alerta.severity.value)  # where = "Mesa 12"
```

Marcar una alerta como vista (deja de sonar en las PCs del local): `client.ack_alert(alerta.alert_id)`.

### Recibir alertas en tiempo real

```python
@client.on("alert.opened")
def nueva_alerta(event):
    print("Nueva alerta:", event.data.where, event.data.alert_type.value)


client.start_in_background()  # apps comunes (script, Flask, Django, Tkinter, Streamlit)
# o, en apps async (FastAPI):  asyncio.create_task(client.run_forever())
```

Eventos disponibles: `alert.opened`, `alert.updated`, `alert.resolved`, `table.state_changed`,
`table.staff_visit`, `camera.status`, `snapshot`, `heartbeat` (o `"*"` para todos).
Si se corta la red, el cliente **reconecta solo**. Con `start_in_background()` además mantiene
actualizados `client.tables`, `client.alerts` y `client.active_alerts_now()` sin pedir nada.

> Las funciones registradas con `@client.on` se ejecutan en otro hilo: tienen que ser rápidas.
> En Tkinter, no tocar la ventana desde ahí: guardar el evento en una `queue.Queue` y leerla con
> `root.after(...)`.

### Métricas del día

```python
from datetime import datetime, timedelta

hoy = client.get_metrics(from_=datetime.now().astimezone() - timedelta(hours=12))
g = hoy.groups[0]
print(g.n_sessions, "mesas atendidas")
print(
    "Espera promedio:", round((g.t_esp.avg or 0) / 60, 1), "min"
)  # los tiempos vienen en SEGUNDOS
print("A tiempo:", g.sla_pct, "%")
```

Agrupar: `client.get_metrics(group_by="hour")` (también `day`, `weekday`, `shift`, `table`, `sector`).

### Según el framework

| Framework | Ejemplo | Dónde arrancar la escucha en vivo |
|---|---|---|
| Script | `client/examples/script_simple.py` | `client.start_in_background()` al inicio |
| Flask | `client/examples/flask_app.py` | a nivel módulo, una vez |
| FastAPI | `client/examples/fastapi_app.py` | `lifespan` → `asyncio.create_task(client.run_forever())` |
| Django | `client/examples/django_example.py` | `AppConfig.ready()` (evitar doble arranque con `RUN_MAIN`) |
| Streamlit | `client/examples/streamlit_app.py` | dentro de `@st.cache_resource` |
| Webhooks | `client/examples/webhook_receiver.py` | alternativa: Mesa Monitor le avisa por POST firmado |

## 6. Qué NO hacer

- **No** instalar dependencias de visión (torch, ultralytics, opencv, rfdetr) en el sistema de
  gestión: no hacen falta y pueden romperlo.
- **No** conectarse directo a la base de datos de Mesa Monitor: usar siempre el cliente (o la API).
- **No** abrir puertos del router hacia internet ni exponer Mesa Monitor afuera del local.
- **No** escribir la API key en el código ni subirla a GitHub: va en `.env`.
- **No** crear reportes o rankings por empleado ni cruzar datos de mozos con estos tiempos:
  el sistema mide la atención de **mesas** (ver `docs/cumplimiento.md`).
- **No** pedir datos en un bucle muy rápido (ej. `get_state()` cada 0,1 s): usar los eventos en
  vivo o, como mucho, consultar cada 5 segundos.

## 7. Problemas comunes

| Mensaje / síntoma | Causa probable | Solución |
|---|---|---|
| `No se pudo conectar con Mesa Monitor en …` | La PC de cámaras está apagada, otra red o dirección mal escrita | Prender la PC, probar `http://DIRECCIÓN:8000/api/v1/health` en el navegador, revisar `MESA_MONITOR_URL` |
| `La API key no es válida o fue revocada` | Clave mal copiada o revocada | Crear otra en Configuración → Accesos → API keys y actualizar `.env` |
| `La API key no tiene permiso…` | La clave es de "lectura" y se pidió algo de administrador | Usar solo las funciones de esta guía, o crear la clave con rol admin |
| `KeyError: 'MESA_MONITOR_API_KEY'` | El `.env` no se cargó | Revisar que exista y que se llame a `load_dotenv()` al inicio |
| Los eventos no llegan pero `get_state()` anda | Falta `start_in_background()` / `run_forever()`, o un firewall bloquea WebSocket | Agregarlo; en el log aparece "Conectado a Mesa Monitor" |
| Los minutos de las mesas no coinciden con el reloj | Reloj de alguna PC desfasado | Activar la hora automática en Windows (Configuración → Hora e idioma) en ambas PCs |
| `ModuleNotFoundError: mesa_monitor_client` | Se instaló en otro entorno de Python | Activar el entorno virtual del proyecto y repetir el `pip install` |

Para ver qué hace el cliente, activar el log:
```python
import logging

logging.basicConfig(level=logging.INFO)
```

## 8. Prompt sugerido para el dueño

El dueño puede pegarle esto a su asistente:

> Integrá el módulo Mesa Monitor en mi sistema siguiendo el archivo INTEGRATION.md del
> repositorio https://github.com/ignaciolinzoain4/mesa-monitor . Quiero una sección que muestre
> las alertas activas y el estado de las mesas, que se actualice sola. Pedime la dirección del
> servidor y la API key cuando las necesites, guardalas en el archivo .env y explicame cada paso
> en simple. No instales nada de cámaras ni de inteligencia artificial.

---

Referencia técnica: la API completa está documentada en `http://DIRECCIÓN:8000/api/v1/docs`
(OpenAPI) y el formato de cada evento en `schemas/*.json`.
