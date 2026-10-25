# Estado: butacas prioritarias 51 y 54

Fecha: 2026-10-25  
Rama: `feature/sandro-butacas-prioritarias-20261025`  
Base: `16a68086f83b1aa7be279a25d3cc885fbdd9a54f`

## Implementado

- La prioridad se evalúa por viaje y número de butaca. Las butacas 51 y 54
  no se bloquean mediante un estado global del asiento.
- La venta general queda bloqueada hasta las 00:00 de Argentina de dos días
  calendario antes de la fecha local de salida. Por ejemplo, una salida el
  jueves 15/10/2026 a las 20:00 libera el martes 13/10/2026 a las 00:00;
  no se calcula restando 48 horas.
- La elegibilidad se calcula al consultar y al crear la reserva. No requiere
  worker, no escribe desde las consultas GET y vuelve a validarse dentro de la
  transacción que toma las butacas.
- Una reserva u ocupación existente conserva su ocupante y no se transforma en
  una butaca prioritaria bloqueada. La protección de doble reserva permanece
  en el servicio transaccional.
- La web, el panel y sus mapas muestran el bloqueo, pero la seguridad no
  depende del HTML: un POST manipulado se rechaza en el servicio.
- No se agregó acreditación, documentación médica, precio especial ni cuenta
  nueva. Antes de la liberación no existe un circuito general para asignar
  esas butacas por fuera de una autorización todavía no definida.

## Contratos pendientes

- Sandro debe definir quién autoriza una asignación prioritaria antes de la
  liberación, cómo se acredita y qué tarifa corresponde. No se inventó ese
  procedimiento ni se recopilaron datos médicos.
- La prueba de concurrencia efectiva con bloqueos y restricciones de
  PostgreSQL debe ejecutarse contra PostgreSQL. La suite realizada utiliza la
  base temporal SQLite disponible en este entorno; SQLite no reproduce todos
  los bloqueos de fila ni el comportamiento de concurrencia de producción.
- No se agregaron migraciones: el diseño reutiliza `Seat` y la disponibilidad
  existente, por lo que no requiere migración persistente.

## Validación

Se cubren límites inmediatamente antes, exactamente en y después del plazo,
zona horaria y cambio de año, ambos números prioritarios, otras butacas,
aislamiento entre salidas, ocupaciones existentes, GET sin escrituras, POST
manipulado y doble reserva. Las validaciones finales se registran en la
entrega de la rama.

