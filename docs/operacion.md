# Operación diaria

## Encender / apagar

```sh
docker compose up -d      # encender (también arranca solo al prender la PC, si Docker arranca solo)
docker compose down       # apagar (los datos quedan guardados)
docker compose ps         # ver qué está corriendo
```

En Windows: Docker Desktop → Settings → General → "Start Docker Desktop when you sign in".

## Ver qué pasa (logs)

```sh
docker compose logs -f worker-gpu    # o worker-cpu: procesamiento de video
docker compose logs -f api           # servidor, alertas, webhooks
```

El dashboard tiene **Salud del sistema** con lo mismo en forma simple.

## Backups

La configuración (mesas, zonas, umbrales) se descarga desde **Respaldo**. Para la base completa
(incluye el historial de sesiones):

```sh
docker compose exec db pg_dump -U mesa -d mesa_monitor -Fc > backup-$(date +%F).dump
# restaurar en una instalación nueva:
docker compose exec -T db pg_restore -U mesa -d mesa_monitor --clean < backup-AAAA-MM-DD.dump
```

Guardar los backups fuera de la PC (pendrive o nube del local). Contienen tiempos de mesas, no
imágenes ni datos personales.

## Actualizar

```sh
git pull
docker compose up -d --build
```

Las migraciones de la base se aplican solas al arrancar la API.

## Retención de datos

- Sesiones y visitas: se guardan siempre (son la base de las métricas).
- Transiciones de estado: 180 días · log de cámaras: 90 días · eventos: 7 días · entregas de
  webhooks: 30 días. Se limpian solos cada 6 horas.
- Recortes de personas (solo si se activa la recolección): 7 días por defecto.
- No se graba video.

## Problemas frecuentes

| Problema | Qué revisar |
|---|---|
| `/alertas` dice "Sin conexión" | Que la PC de Mesa Monitor esté prendida; la red; `docker compose ps` |
| Una cámara "Sin señal" | El NVR y el cable de la cámara; ver `docs/instalacion.md` |
| Todas las mesas en "Sin visión" | El worker: `docker compose logs worker-gpu` |
| No suena en una PC de tiqueo | Tocar "Activar sonido" (el navegador lo exige después de cada recarga) y subir el volumen |
| La PC de tiqueo pide PIN | El navegador borró los datos: ingresar el PIN de nuevo (o crear otro dispositivo) |
