# Calibración

Orden recomendado la primera vez. Todo se hace desde el dashboard, sin tocar código.

## 1. Cámaras

**Cámaras**: todas tienen que estar "En línea". Si una figura "Con problemas", la resolución del
substream no coincide con la esperada (ver `docs/instalacion.md`).

## 2. Mesas y plano

**Plano del salón**: crear todas las mesas (etiqueta tal como la usa el personal, ej. "12", "G3";
sector, ej. salon / galeria) y ubicarlas arrastrándolas. El plano es lo que se ve en `/alertas`.

## 3. Zonas por cámara

**Zonas** → elegir cámara. Para cada mesa visible:

1. *Superficie*: el contorno del tablero de la mesa (se usa para contar vasos y platos).
2. *Asientos*: el área donde quedan los **pies o el asiento** de quien está sentado (el sistema
   mira el punto inferior central de cada persona). Incluir las sillas y un margen.

Además:

- **Zonas de personal**: barra, pase de cocina, caja. Dibujarlas donde quedan **los pies** del
  personal cuando trabaja ahí (no la mesada). Cuanto más recorridos del personal pasen por zonas
  dibujadas, mejor los distingue el sistema de los clientes.
- **Áreas a ignorar**: vidrieras (gente que pasa por la vereda), espejos, la fecha/hora que el
  NVR imprime en la imagen, televisores.

Consejos:
- Si una mesa se ve desde dos cámaras, dibujarla en ambas y darle más **prioridad** a la que la ve
  mejor (más cerca, menos tapada). Si esa cámara falla, se usa la otra.
- El panel "Mesas sin ninguna cámara" muestra qué mesas quedan sin cobertura: sirve para decidir
  dónde ubicar las cámaras nuevas.
- Si desde un ángulo no se ven bien los vasos y platos, en la mesa elegir "Solo visita del personal".
- Al terminar, **Respaldo → Descargar respaldo**.

## 4. Perfil de personal

**Configuración → Perfil de personal**: tomar una imagen con varios mozos y clientes, marcar 3–10
de cada uno y "Calcular y activar". El resultado dice si el color de la ropa alcanza:

- **buena**: el color distingue bien.
- **regular**: ayuda, combinado con postura y recorridos.
- **insuficiente**: con ropa negra pasa seguido (muchos clientes visten oscuro). La solución más
  efectiva es un **distintivo de color** (delantal o remera de un color llamativo) y recalibrar.

## 5. Postura (sentado / parado)

Sin modelo de keypoints, el sistema decide con la proporción alto/ancho de cada persona y su
movimiento. Depende del ángulo de la cámara. Para medirlo con video real:

```sh
uv run python tools/replay.py VIDEO.dav --zones respaldo.yaml --camera ID --seconds 120 --annotate
```

Abrir `data/replay/VIDEO/annotated.mp4`: cada persona tiene la etiqueta `cli`/`MOZO` y `parado`.
Si clientes sentados aparecen "parado", subir **Proporción para 'parado'** (Configuración →
Visión); si mozos parados aparecen sentados, bajarla. En cámaras en altura suele andar entre 2.4 y
3.0 (en un video real de un café: sentados 1.3–2.8, parados 2.9–3.9).

## 6. Detección por mosaicos

Con cámaras gran angular en altura, la detección por mosaicos es imprescindible (en un video real
pasó de 15 a 41 personas detectadas). Cuesta ~4 veces más: correr el benchmark después de dibujar
las zonas (solo se procesa la zona dibujada, así que dibujar de más cuesta GPU).
