# mesa-monitor-client

Cliente Python liviano para conectarse a un servidor **Mesa Monitor**: estado de las mesas,
alertas en tiempo real y métricas. No instala nada de visión (ni torch, ni numpy).

```bash
pip install "git+https://github.com/ignaciolinzoain4/mesa-monitor.git#subdirectory=client"
```

```python
from mesa_monitor_client import MesaMonitorClient

client = MesaMonitorClient(base_url="http://192.168.1.50:8000", api_key="mm_...")
print(client.get_state())


@client.on("alert.opened")
def nueva_alerta(event):
    print("Alerta:", event.data.alert_type, "mesa", event.data.table.label)


client.start_in_background()
```

Guía completa (escrita para que la siga un asistente de IA): [INTEGRATION.md](../INTEGRATION.md).
