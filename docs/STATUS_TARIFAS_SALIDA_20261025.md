# Estado de implementación — Tarifas por salida (2026-10-25)

Rama / worktree: `feature/sandro-tarifas-salida-20261025` (`D:\renderbyte\scviajes-wt-tarifas-20261025`).

## 1. Resumen ejecutivo

Se implementó el modelo comercial y operativo de **tarifas por salida** en SC Viajes, reemplazando el esquema anterior de tarifas por par de paradas (`origin_stop`, `destination_stop`) dentro de `operations.TripFare`.

### Reglas confirmadas implementadas
- **Tarifa por salida y categoría de butaca**: cada salida (`operations.Trip`) define un precio para `CAMA` y un precio para `SEMI_CAMA`, con restricción única de base de datos `ops_fare_trip_category_unique` sobre `(trip, seat_category)`.
- **Precio uniforme en todos los tramos permitidos**: dentro de una misma salida, cada categoría mantiene el mismo importe en todos los tramos habilitados del cronograma (`TripStop`), preservando íntegramente las validaciones de secuencia, permisos de subida/bajada (`allows_boarding`, `allows_alighting`) y prohibición de tramos intraprovinciales (`origin_stop.stop.province == destination_stop.stop.province`).
- **Edición exclusiva por Administradores antes de la salida**:
  - Sólo usuarios del grupo `Administrador` o superusuarios activos (`require_operations_manager`) pueden crear, editar, activar o desactivar tarifas de una salida.
  - Todas las mutaciones exigen método `POST` con protección `CSRF`, validación server-side (`can_modify_trip_fares`) que verifica que el viaje esté en estado `SCHEDULED` o `BOARDING`, sin `started_at` ni `completed_at`, y con reloj actual estrictamente anterior a `trip.departure_at` (`now < trip.departure_at`).
  - Se registra auditoría transaccional en `panel.AuditEvent` con el actor, fecha/hora, acción (`CREATE`, `UPDATE`, `ACTIVATE`, `DEACTIVATE`) y snapshots `before` y `after` (`trip_id`, `seat_category`, `amount`, `currency`, `is_active`).
- **Afectación exclusiva a nuevas reservas sobre disponibilidad libre**:
  - Al cambiar el precio de una salida antes de su partida, las nuevas reservas (`ONLINE` y `MANUAL`) que tomen disponibilidad libre leen y congelan el nuevo importe vigente.
  - Las reservas existentes (`HELD` pendientes de pago o `CONFIRMED`) conservan su snapshot original en `sales.SeatAssignment.price` y `sales.SeatAssignment.currency`.
- **Inmutabilidad histórica**:
  - Ventas, pagos (`payments.Payment` y `calculate_booking_total`), pasajes emitidos (`tickets.Ticket.price`), reportes de ventas, caja y comisiones de vendedores (`panel/report_services.py`) leen exclusivamente los snapshots congelados y nunca se recalculan frente a cambios posteriores en `TripFare`.
- **Menores gratuitos sin butaca**:
  - Los menores de dos años sin asiento (`is_lap_infant=True`) no generan `SeatAssignment`, permanecen en importe `Decimal("0.00")` en pasajes y manifiestos, y no alteran totales ni comisiones aunque cambie la tarifa de la salida.
- **Precisión monetaria y concurrencia PostgreSQL**:
  - Todos los importes utilizan `Decimal` (con validación de dos decimales y `amount > Decimal("0.00")`).
  - Tanto la actualización administrativa de tarifa (`panel.trip_services.save_fare` / `set_fare_active`) como la creación de reservas (`sales.services.create_booking`) adquieren bloqueo de fila en orden determinístico sobre `Trip.objects.select_for_update()` y `TripFare.objects.select_for_update()`, serializando lecturas y escrituras en PostgreSQL.

---

## 2. Detección de conflictos históricos y procedimiento de migración

### Diseño de `operations/migrations/0003_tripfare_per_departure.py`
No se inventan precios productivos ni se convierten silenciosamente tarifas históricas ambiguas:
1. En `operations/services.py` se incorporaron las funciones puras:
   - `detect_trip_fare_conflicts(fare_records)`: agrupa registros de tarifas por `(trip_id, seat_category)` e identifica cualquier salida que posea más de un conjunto de `(amount, currency, is_active)` entre distintos tramos.
   - `validate_no_trip_fare_conflicts(fare_records)`: lanza `ValidationError` detallando `trip_id`, `seat_category` y las variantes en conflicto si existen discrepancias.
2. En la migración `operations/migrations/0003_tripfare_per_departure.py`:
   - Primero se eliminan las restricciones antiguas por tramo (`ops_fare_segment_unique`, `ops_fare_distinct_stops`).
   - Luego se ejecuta `RunPython(consolidate_or_reject_ambiguous_trip_fares)`:
     - Si una salida tiene tarifas por tramo con el **mismo** importe, moneda y estado para la misma categoría, conserva un único registro consolidado por `(trip_id, seat_category)` y elimina las filas redundantes de otros tramos.
     - Si una salida tiene tarifas con **distinto** importe, moneda o estado para la misma categoría entre distintos tramos, **aborta la migración con `ValidationError` explícito**, sin sobrescribir ni inventar precios.
   - A continuación se eliminan las columnas `origin_stop` y `destination_stop` y se crea la restricción única `ops_fare_trip_category_unique` sobre `(trip, seat_category)`.

### Procedimiento operativo ante conflictos en entornos con datos previos
Si al ejecutar la migración en un entorno con datos históricos apareciera el `ValidationError` de `validate_no_trip_fare_conflicts`:
1. No forzar ni editar la migración para elegir un precio arbitrario.
2. Inspeccionar las salidas reportadas en el mensaje de error (`Viaje #<trip_id> (<seat_category>)`). Note que las reservas y pasajes históricos ya tienen su precio congelado en `sales_seatassignment.price` y `tickets_ticket.price`, por lo que unificar `operations_tripfare` no altera ningún histórico comercial.
3. Confirmar con la dirección comercial (Sandro) cuál es la tarifa vigente por categoría para las salidas futuras afectadas y normalizar las filas de `operations_tripfare` antes de volver a correr `migrate`.

---

## 3. Archivos modificados y generados

### Dominio y migraciones
- `operations/models.py`: `TripFare` por salida y categoría (`trip`, `seat_category`, `amount`, `currency`, `is_active`), `UniqueConstraint` `ops_fare_trip_category_unique`, eliminación de `origin_stop` y `destination_stop`.
- `operations/services.py`: `detect_trip_fare_conflicts` y `validate_no_trip_fare_conflicts`.
- `operations/migrations/0003_tripfare_per_departure.py`: migración de esquema y validación/consolidación segura sin invención de precios.

### Ventas, panel, plantillas y escenario demo
- `sales/services.py`: `get_trip_availability` y `create_booking` consultan `TripFare` por `(trip, seat_category)` manteniendo todas las validaciones de tramo (`origin_stop`, `destination_stop`, secuencia, permisos y distinta provincia) y bloqueando con `select_for_update()`.
- `panel/services.py`: `snapshot(instance)` de `TripFare` actualizado a `trip_id`, `seat_category`, `amount`, `currency`, `is_active`.
- `panel/trip_forms.py`: `TripFareForm` por `(seat_category, amount, currency)` validando unicidad por salida y categoría.
- `panel/trip_services.py`: `can_modify_trip_fares`, bloqueo `Trip.objects.select_for_update()` y `TripFare.objects.select_for_update()` en `save_fare` y `set_fare_active`, y bloqueo de cambios una vez iniciada o alcanzada la hora de salida.
- `panel/trip_views.py`: ordenamiento de tarifas por categoría, bandera `can_modify_fares` en detalle de viaje y manejo de `ValidationError` en `fare_state`.
- `templates/panel/operations/trip_detail.html` y `templates/panel/operations/fare_form.html`: interfaz en español argentino indicando cobertura en "Todos los tramos permitidos" y ocultando controles de edición cuando la salida ya inició/venció.
- `core/demo_scenario.py`: creación de una tarifa `SEMI_CAMA` y una tarifa `CAMA` por salida en el escenario reproducible de demostración.

### Pruebas actualizadas y ampliadas
- `operations/tests.py`: unicidad por `(trip, seat_category)` y pruebas de `detect_trip_fare_conflicts` / `validate_no_trip_fare_conflicts`.
- `sales/tests.py`: igualdad de precios en todos los tramos permitidos, preservación de snapshots en reservas impagas (`HELD`) y nuevas reservas con tarifa actualizada, ida/vuelta y prueba de concurrencia PostgreSQL entre `save_fare` y `create_online_booking`.
- `sales/test_minors.py`: verificación de que menores gratuitos en brazos (`is_lap_infant=True`) permanecen en `Decimal("0.00")` aunque cambie la tarifa de la salida.
- `panel/test_trips.py`: permisos de Administrador vs Vendedor, bloqueo posterior al horario/inicio de salida, auditoría `before`/`after` y preservación de reservas previas impagas frente a cambios de tarifa.
- `panel/test_reservations.py`, `panel/test_manifest.py`, `panel/test_reports.py`, `panel/test_payments.py`, `payments/tests.py`, `tickets/tests.py`, `core/test_checkout.py`: adaptados al modelo `TripFare` por salida y con pruebas de inmutabilidad de comisiones y checkout público ante cambios de precio.

---

## 4. Archivos compartidos inevitables con Terminal 2 (Butacas prioritarias)

Dado que la Terminal 2 trabaja en paralelo sobre **butacas prioritarias**, se documentan los archivos compartidos que requerirán atención al integrar ambas ramas:

1. `operations/models.py` y `operations/migrations/`:
   - En esta rama se modificó únicamente `TripFare` y se generó `operations/migrations/0003_tripfare_per_departure.py`.
   - Si Terminal 2 modifica `Seat` (por ejemplo, agregando un flag de prioridad) y genera otra migración `0003_...` en `operations`, al integrar ambas ramas se deberá encadenar la dependencia o ejecutar `makemigrations --merge` sin tocar bases reales.
2. `sales/services.py`:
   - En esta rama sólo se modificaron las consultas a `TripFare` en `get_trip_availability` y `create_booking`. No se alteró la lógica de selección ni validación de `Seat`.
3. Archivos de pruebas (`operations/tests.py`, `sales/tests.py`, `panel/test_reservations.py`, `core/test_checkout.py`) y `docs/STATUS.md`:
   - Los cambios de esta rama en fixtures de tests se limitan a la instanciación de `TripFare(trip=..., seat_category=..., amount=...)` (sin `origin_stop`/`destination_stop`) y a nuevos métodos de prueba al final de las clases correspondientes.

---

## 5. Validaciones no ejecutadas por restricción de herramientas

Por instrucción explícita del prompt (*"En esta ejecución no uses shell, terminal, Git, migraciones ni comandos externos. Usá sólo herramientas de lectura y edición de archivos"*), **no se ejecutaron comandos de terminal** en esta sesión. Antes de integrar la rama deben correrse en consola local/CI (con base temporal de pruebas y sin conectar bases reales):

- `git diff --check`
- `python manage.py check`
- `python manage.py makemigrations --check --dry-run`
- `python manage.py test`

## 6. Revisión posterior de Codex

Las validaciones indicadas arriba ya se ejecutaron parcialmente: Ruff, Django check, makemigrations check, migración completa desde SQLite temporal vacía y 35 pruebas afectadas posteriores pasaron. La suite combinada inicial encontró 15 errores en fixtures/expectativas incompatibles, corregidos en esta revisión. PostgreSQL, concurrencia real y suite completa posterior quedan pendientes.
