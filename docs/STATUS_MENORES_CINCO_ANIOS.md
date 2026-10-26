# Estado: umbral de gratuidad para menores de cinco años — 25/10/2026

## Alcance implementado

- Se actualizó el umbral de gratuidad sin butaca de menores de 2 años a **menores de 5 años** (`MINOR_FREE_AGE_YEARS = 5` en `sales/minors.py` y validación cliente auxiliar en `static/js/checkout.js`).
- Todo pasajero con edad estrictamente menor a 5 años (`0` a `4` años cumplidos, inclusive el día previo a su quinto cumpleaños) en la fecha de referencia de la compra califica como menor gratuito sin butaca (`BookingPassenger.is_lap_infant = True`).
- Al cumplir **exactamente 5 años** (o más) en la fecha de referencia de una compra nueva, el pasajero deja de ser gratuito: debe ocupar butaca propia, abona la tarifa vigente de la salida y no puede declarar madre acompañante.

## Fecha de referencia de compra, ida/vuelta y regreso separado

- Se mantiene expresamente la fecha de referencia de compra actualmente implementada en `sales/minors.py` (`MINOR_POLICY_ID = "provisional-created-at-until-sandro"`): la edad se evalúa una única vez al crear la reserva sobre la fecha local en `America/Argentina/Buenos_Aires` del instante efectivo recibido por el servicio (`purchase_at`, correspondiente a `Booking.created_at` / `effective_now`).
- **Compra ida/vuelta antes de cumplir 5 años**: el resultado se persiste como snapshot inmutable en `BookingPassenger` (`is_lap_infant` y `accompanied_by`), por lo que una compra de ida y vuelta realizada antes de cumplir 5 años conserva la gratuidad sin butaca y el precio cero en ambos tramos aunque el menor cumpla 5 años antes del viaje de ida o del regreso.
- **Regreso comprado por separado después de cumplir 5 años**: constituye una compra nueva e independiente que evalúa la edad en su propia fecha de creación; si para entonces el menor ya cumplió 5 años, exige butaca y tarifa propia sin modificar la reserva histórica de la ida.

## Reglas conservadas e integridad histórica

- **Acompañante obligatorio**: el menor gratuito debe asociarse a una madre acompañante con butaca propia dentro de la misma compra; se rechazan autorreferencias, referencias fuera de rango, ciclos y acompañantes que también sean menores gratuitos.
- **Ausencia de asiento, precio cero y comisión cero**: el menor gratuito cuenta como pasajero de la reserva, pero no genera `SeatAssignment`, no descuenta disponibilidad del colectivo, mantiene importe `Decimal("0.00")`, no suma unidad comisionable en reportes ni dashboard (`commissionable_units`), recibe su pasaje emitido sin butaca (`seat_number=None`, `seat_category="LAP_INFANT"`, `price=Decimal("0.00")`) y aparece en el manifiesto operativo como `"Sin butaca"` (`"Menor gratuito en falda"`).
- **Inmutabilidad histórica**: la confirmación de la reserva (`confirm_booking`), el registro o aprobación de pagos, la emisión de pasajes (`issue_tickets_for_booking`), la validación de embarque (`validate_boarding`), el manifiesto, los reportes y los cambios posteriores de tarifa (`TripFare`) consumen el snapshot persistido y nunca recalculan la edad ni alteran reservas históricas previas.

## Decisiones pendientes conservadas con Sandro

No se resolvieron por suposición y permanecen abiertas para definición comercial:

1. **Referencia temporal de compra**: mantener creación de la reserva (`Booking.created_at`) o adoptar la confirmación del pago (`Booking.confirmed_at`) como instante definitivo para evaluar la edad.
2. **Seguro sobre menores gratuitos sin butaca**: no se agregó cargo, cálculo ni leyenda de seguro adicional.
3. **Validación con RENAPER**: no se agregaron integraciones ni validaciones externas de identidad/vínculo.

## Validación ejecutada

- `python -m ruff check .`: sin errores.
- `python manage.py check`: sin errores (`0 issues`).
- `python manage.py makemigrations --check --dry-run`: sin migraciones pendientes (`No changes detected`).
- `git diff --check`: limpio.
- Pruebas específicas (`python manage.py test sales.test_minors tickets.test_minors`) y suite completa ejecutadas sobre base SQLite temporal en memoria, sin tocar bases persistentes.
