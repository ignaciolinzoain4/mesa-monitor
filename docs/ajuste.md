# Ajuste y evaluación de precisión

Objetivo: medir qué tan bien coincide lo que detecta el sistema con la realidad, y ajustar.

## 1. Conseguir video

Exportar del NVR (DMSS o la web del NVR) **1 a 2 horas de servicio en hora pico** de cada cámara.
Guardarlo **fuera del repositorio** (ej. `D:\videos-mesa-monitor\`). Nunca subirlo a GitHub.

## 2. Correr el replay

```sh
uv run python tools/replay.py D:\videos\salon_1900.dav --zones respaldo.yaml --camera salon-1 --annotate
```

Genera en `data/replay/salon_1900/`: `events.jsonl`, `sessions.csv` y `annotated.mp4`.

**En la PC del local (Docker)**, con los videos en `D:\videos` y el respaldo de zonas copiado a
`config/local/respaldo.yaml`:

```powershell
docker compose run --rm -v D:\videos:/videos worker-gpu python tools/replay.py /videos/salon_1900.dav `
    --zones /app/config/local/respaldo.yaml --camera salon-1 --annotate --out /videos/replay_salon_1900
docker compose run --rm -v D:\videos:/videos worker-gpu python tools/evaluate.py `
    /videos/replay_salon_1900/events.jsonl /videos/anotaciones.csv --video-start "2026-10-24T19:00:00-03:00"
```

(`worker-cpu` si no hay placa NVIDIA.)

## 3. Anotar a mano (1 hora como mínimo)

Mirar el video (VLC muestra el tiempo transcurrido) y anotar en una planilla con estas columnas
(ver `docs/anotaciones-ejemplo.csv`):

| table_label | event | timestamp_video |
|---|---|---|
| 12 | seated | 00:03:10 |
| 12 | first_attention | 00:06:45 |
| 12 | staff_visit | 00:06:45 |
| 12 | staff_visit | 00:21:30 |
| 12 | vacated | 00:58:02 |
| 12 | cleaned | 01:02:40 |

- `seated`: se sientan (el primero del grupo).
- `first_attention`: el mozo llega por primera vez (también anotarlo como `staff_visit`).
- `staff_visit`: cada vez que el personal se para en la mesa (más de 4 segundos).
- `vacated`: se va el último cliente.
- `cleaned`: la mesa queda limpia.

`timestamp_video` es la posición en el video (`HH:MM:SS`), no la hora del reloj.

## 4. Evaluar

```sh
uv run python tools/evaluate.py data/replay/salon_1900/events.jsonl anotaciones.csv \
    --video-start "2026-10-24T19:00:00-03:00"
```

`--video-start` es la hora que muestra el sello del NVR en el primer cuadro. Resultado:

```
evento           anot. detec.  error medio (s)  mediana    máx   sesgo  precisión  recall
seated              24     25             21.3     19.0   58.0    18.2       96 %    100 %
first_attention     23     22             14.8     ...
staff_visit         61     55              ...                              89 %     80 %
...
```

- **error medio**: cuánto se equivoca el sistema, en segundos.
- **sesgo**: positivo = detecta más tarde (normal por el antirrebote: sentarse se confirma a los 20 s).
- **precisión** de visitas: de las que detectó, cuántas ocurrieron de verdad.
- **recall**: de las que ocurrieron, cuántas detectó.

## 5. Qué ajustar según el síntoma

| Síntoma | Ajuste (Configuración) |
|---|---|
| Visitas que no ocurrieron (precisión baja) | Calibrar el perfil de personal; subir "duración mínima de una visita"; achicar "ampliación del área de asientos" |
| Visitas perdidas (recall bajo) | Bajar "duración mínima de una visita" (ej. 3 s); subir la ampliación de asientos; revisar que el mozo no quede fuera del área dibujada |
| Clientes contados como mozos | Perfil de personal (o distintivo de color); dibujar mejor las zonas de personal; subir "proporción para parado" |
| Mozos contados como clientes | Dibujar las zonas de personal donde quedan sus pies; bajar "proporción para parado" |
| `seated` con mucho retraso | Bajar "tiempo sentados para considerar la mesa ocupada" (cuidado: más falsas ocupaciones) |
| Mesas que se "vacían" cuando alguien va al baño | Subir "tiempo vacía para considerar que se fueron" |
| `vacated` muy tarde | Bajar ese mismo tiempo |
| La mesa nunca queda "limpia" | En esa mesa usar "Solo visita del personal" (no se ven bien los platos desde ese ángulo) |
| Muchas personas sin detectar al fondo | Activar mosaicos; achicar el tamaño de mosaico; modelo más grande |
| Mesas en "Sin visión" a cada rato | Revisar la cámara/NVR; ver "Salud del sistema" |

Después de cada ajuste, volver a correr el replay y la evaluación sobre **el mismo video** para
comparar. Anotar los resultados (el JSON de `--json`) para ver la mejora.
