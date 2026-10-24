# Estado: menores gratuitos sin butaca — 24/10/2026

## Implementado en esta rama

- `BookingPassenger` conserva todos los datos del menor y separa la persona de `SeatAssignment`.
- Un menor de dos años a la fecha provisional de compra puede asociarse a una madre de la misma compra; no recibe butaca, precio ni unidad comisionable.
- La asociación y el indicador de gratuidad se guardan como snapshot al crear la reserva. La confirmación y las consultas no recalculan la edad.
- La emisión conserva un ticket por pasajero y tramo, pero permite ticket sin butaca para el menor. El manifiesto lo muestra como “Menor gratuito en falda” sin consumir ocupación.
- La compra ida/vuelta usa el mismo snapshot en ambos tramos. La compra posterior es independiente y vuelve a evaluar la fecha de compra.

## Ambigüedad de fecha de compra

El modelo actual no tiene un campo separado llamado `purchase_at`: tiene `Booking.created_at` y `confirmed_at`. Esta rama usa el instante efectivo recibido por el servicio al crear la reserva (por defecto, el instante de creación) como proxy provisional y persiste el resultado. No se eligió silenciosamente la fecha de confirmación ni se recalculan compras existentes al consultar.

## Decisión provisional pendiente de Sandro

“¿La gratuidad se determina por la edad al comprar y cubre ambos tramos de una compra ida/vuelta aunque cumpla dos antes del regreso?”

La política está centralizada en `sales/minors.py` con el identificador `provisional-created-at-until-sandro`, para cambiarla sin distribuir la regla por checkout, panel, emisión o manifiesto.

## Validación y contratos

- Se mantienen el límite vigente de personas por compra y la concurrencia existente de butacas; un menor gratuito cuenta como persona, pero no como asiento.
- No se modificaron `sales/**` fuera de los archivos propios de esta rama, operaciones de rutas, pagos, proveedores ni autorizaciones de viaje.
- La exportación y los reportes existentes basados en asignaciones no deben contar al menor como pasaje comisionable. Si una integración externa necesita distinguir personas de butacas, debe consumir `BookingPassenger.is_lap_infant` y `accompanied_by`.
