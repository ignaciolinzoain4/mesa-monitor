# Cumplimiento y uso responsable

> Este documento resume cómo está diseñado Mesa Monitor para respetar a empleados y clientes, y
> qué tiene que hacer el dueño del local **antes de usarlo en producción**. No es asesoramiento
> legal: la lista y el borrador de abajo se tienen que revisar con un abogado.

## Para qué sirve el sistema

Mesa Monitor mide **cuánto tiempo pasa una mesa sin ser atendida** y avisa al personal cuando una
mesa está esperando. El objetivo es **mejorar el servicio y ayudar al equipo**, no evaluar ni
controlar a cada empleado.

Esto es una regla de diseño del sistema, no solo una recomendación de uso: el sistema **no tiene
forma** de decir qué empleado atendió qué mesa.

## Qué hace y qué no hace

| Hace | No hace |
|---|---|
| Detecta si hay personas sentadas en cada mesa | No reconoce caras ni identifica personas |
| Distingue "personal" de "cliente" para saber si una mesa fue visitada | No sabe *quién* del personal visitó la mesa |
| Guarda la hora de inicio y fin de cada visita a una mesa | No guarda a qué empleado corresponde una visita |
| Calcula tiempos por mesa, sector, horario, día y turno | No calcula tiempos ni rankings por empleado |
| Muestra alertas en las PCs del local | No graba video |

Detalles técnicos que garantizan lo anterior:

- El seguimiento de cada persona en la imagen usa un número temporal que existe solo en la memoria
  del programa mientras la persona está en cámara. **Nunca se guarda** en la base de datos.
- La tabla de visitas guarda solo: mesa, sesión, hora de inicio, hora de fin y un nivel de confianza.
- Las métricas solo se pueden agrupar por mesa, sector, hora, día, día de semana y turno. Hay un
  test automático que falla si alguien agrega una columna o agrupación que identifique personas.
- La pantalla de diagnóstico ("overlay de debug") muestra el video en vivo con recuadros y viene
  **apagada** por defecto. No graba.
- La herramienta opcional de "recolección de recortes" (para entrenar un clasificador más adelante)
  guarda **imágenes de personas**. Viene **apagada**; si se activa, borra los recortes a los 7 días
  (configurable) y su uso se tiene que incluir en la notificación a los empleados.
- Todo funciona dentro de la red del local. No se expone el video ni la base de datos a internet.

## Integración futura con el sistema de comandas (POS)

Si el POS llega a informar qué mozo tiene asignada cada mesa, **ese dato no se cruza con las
métricas de cámara** sin una revisión legal previa. Esta restricción también figura en el código
(`src/mesa_monitor/core/metrics.py`).

## Checklist para el dueño del local (antes de producción)

- [ ] Notificar por escrito a los empleados la existencia y la finalidad del sistema (art. 71 LCT).
- [ ] Actualizar el cartel de videovigilancia incluyendo la nueva finalidad (Disp. 10/2015 AAIP y modificatorias).
- [ ] Actualizar el manual de tratamiento y la inscripción de la base de videovigilancia ante la AAIP.
- [ ] Consultar con un abogado laboralista antes de pasar a producción (protocolo de cámaras del
      Ministerio de Trabajo de Córdoba), incluyendo si se piensa usar alguna métrica para
      incentivos grupales.

## Borrador de notificación a empleados

> **BORRADOR — A REVISAR POR UN ABOGADO ANTES DE USARLO.**

---

**Notificación sobre el sistema de monitoreo de atención de mesas**

[Ciudad], [fecha]

A todo el personal de [nombre del local]:

Les informamos que a partir del [fecha] el local va a usar un sistema llamado Mesa Monitor, que
funciona con las cámaras de seguridad que ya están instaladas.

**Para qué se usa.** El sistema mide cuánto tiempo pasa una mesa esperando ser atendida y muestra
avisos en las computadoras del local para que podamos atender antes. Su finalidad es mejorar el
servicio a los clientes y ayudar al equipo.

**Para qué NO se usa.** El sistema no reconoce caras, no identifica personas y no registra qué
empleado atendió cada mesa. No se usa para evaluar, sancionar ni comparar el trabajo individual de
ningún empleado. Los datos se analizan solo por mesa, sector, horario y día.

**Qué datos se guardan.** Se guarda el estado de cada mesa (libre, ocupada, atendida, sucia) y la
hora de cada cambio. No se graba video con este sistema. [Si se activa la recolección de recortes:
detallar qué imágenes se guardan, para qué y por cuánto tiempo.]

**Dónde están los datos.** En una computadora dentro del local. No se envían a internet.

**Consultas.** Cualquier duda sobre el sistema la pueden hacer a [nombre / cargo / contacto].

Recibí una copia de esta notificación:

Nombre: ______________________  Firma: ______________________  Fecha: ____________

---
