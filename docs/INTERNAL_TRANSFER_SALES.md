# Flujo de Ventas Internas por Transferencia Bancaria - SC Viajes

Este documento describe la especificación técnica, reglas de negocio implementadas, alcance de permisos, comandos de mantenimiento y estado de validación del flujo de ventas internas por transferencia bancaria en el panel operativo de SC Viajes.

---

## 1. Reglas de Negocio Implementadas

### Regla (1): Restricción de reservas manuales sin pago / sin vencimiento
- Los vendedores **no pueden** crear reservas manuales sin pago ni vencimiento (`expires_at = None`).
- Dicha facultad queda restringida con exclusividad a usuarios con rol `Administrador` o superusuarios mediante la validación `require_can_create_manual_unexpired_reservation(actor)` en `panel/reservation_services.py` y `panel/internal_sales_permissions.py`.
- Si un vendedor intenta crear una reserva con `expires_at = None`, se rechaza con `PermissionDenied`.

### Regla (2): Compra pendiente por transferencia con retención temporal
- Los vendedores pueden registrar compras pendientes por transferencia bancaria exigiendo datos completos de contacto (email válido) y de todos los pasajeros (nombre, apellido, tipo y número de documento, fecha de nacimiento, nacionalidad).
- La reserva se crea en estado `HELD` con una retención temporal (`expires_at`) calculada conforme a la Regla 3, bloqueando las butacas seleccionadas de forma transaccional.

### Regla (3): Cálculo de vencimiento (`expires_at`)
- `expires_at` se calcula como el valor mínimo entre:
  1. La **medianoche al término del día de confirmación de compra** en la zona horaria `America/Argentina/Buenos_Aires` (`(local_now.date() + timedelta(days=1)) a las 00:00:00 AR_TZ`).
  2. **Una hora antes de la salida inicial** del viaje (`trip.departure_at - timedelta(hours=1)`).
- **Salida inicial vs parada intermedia**: La salida inicial se determina siempre a partir de `trip.departure_at` (partida de la cabecera del viaje), y **nunca** a partir del horario programado de subida en una parada intermedia (`TripStop.scheduled_at`).
- **Ida y vuelta**: En operaciones con viaje de ida y vuelta, se toma el límite más temprano aplicable entre ambos viajes (`min(ida.departure_at, vuelta.departure_at) - 1h`).

### Regla (4): Inmutabilidad del vencimiento
- Una vez calculado y asignado `expires_at` a una reserva, éste **no se reinicia ni se extiende** al editar notas internas, actualizar datos de pasajeros o reenviar notificaciones.

### Regla (5): Validación de comprobante y alcance de aprobación
- El registro del pago por transferencia bancaria exige un archivo de comprobante válido (formatos admitidos: `.pdf`, `.jpg`, `.jpeg`, `.png`, tamaño máximo configurado en `PAYMENTS_MAX_VOUCHER_SIZE_BYTES`).
- El vendedor realiza la verificación externa en la cuenta bancaria de la empresa y **solo puede revisar y aprobar transferencias de sus propias ventas** (`payment.booking.seller_id == user.pk` o `payment.registered_by_id == user.pk`).
- Administradores y superusuarios poseen autorización global para revisar y aprobar transferencias de cualquier vendedor. Intentos de revisión por un vendedor ajeno se rechazan con `ValidationError` o `PermissionDenied`.
- **Seguridad y alcance en salidas idempotentes**: En `_review_transfer_payment_atomic`, la validación de autorización `validate_payment_reviewer(reviewer, payment=payment)` se ejecuta **antes de cualquier retorno por idempotencia**. De este modo, un vendedor ajeno no puede eludir el alcance por vendedor ni inspeccionar/revisar un pago que ya se encuentre aprobado o confirmado, garantizando al mismo tiempo la idempotencia propia para el vendedor dueño y administradores.

### Regla (6): Carga de comprobante no confirma ni extiende
- La presentación del comprobante crea o transiciona el pago a estado `UNDER_REVIEW` (En revisión).
- La reserva permanece en estado `HELD` y el vencimiento `expires_at` **no se modifica ni se pospone**.

### Regla (7): Liberación por vencimiento con preservación de historial
- Cuando una reserva alcanza su vencimiento (`expires_at <= now`), la butaca es liberada para su venta general (estado `RELEASED` en `SeatAssignment`), mientras que los registros de `Booking`, `BookingPassenger`, `BookingLeg` y `Payment` permanecen íntegros en base de datos para fines contables y de auditoría.

### Regla (8): Venta en la última hora antes de la salida inicial
- Dentro de la última hora previa a la salida inicial (`trip.departure_at - timedelta(hours=1) <= now < trip.departure_at`), queda **estrictamente prohibido** generar retenciones pendientes persistentes.
- Toda operación dentro de esta ventana exige completarse como **venta inmediata confirmada** (`create_immediate_transfer_sale`), requiriendo:
  - Datos completos de contacto y de todos los pasajeros.
  - Archivo de comprobante adjunto válido.
  - Declaración expresa de verificación de pago (`payment_verified = True`).
- Se transiciona atómicamente la reserva a `CONFIRMED`, el pago a `APPROVED`, se asigna la butaca y se encola la emisión de pasajes.

### Regla (9): Comprobación transaccional de butaca y rechazo por conflicto
- Antes de confirmar una reserva o aprobar una transferencia, se bloquea la reserva y sus asignaciones con `select_for_update()`, comprobando que ninguna de las butacas haya sido confirmada previamente por otra reserva (`SeatAssignment` con `status=CONFIRMED`).
- En caso de colisión, se aborta la transacción y se rechaza la operación con `SeatUnavailableError`.

### Regla (10): Bloqueo tras la salida inicial
- Desde el instante exacto de la salida inicial del viaje (`now >= trip.departure_at`), queda totalmente bloqueada la creación o confirmación de nuevas ventas en el flujo interno/manual (`BookingChannel.MANUAL`, `ValidationError: El viaje ya inició su salida inicial`).

### Regla (11): Idempotencia en confirmación y emisión
- Las operaciones `confirm_booking` y `review_transfer_payment` son estrictamente idempotentes. Si se invocan sobre una reserva o pago ya confirmados/aprobados, retornan exitosamente sin duplicar pagos, sin reasignar butacas y sin emitir duplicados de pasajes ni alterar la secuencia persistente (`TicketSequence`).

### Regla (12): Aislamiento del flujo público
- El flujo público de venta online (`/compra/`, `BookingChannel.ONLINE`) y sus plazos específicos (corte a 60 min, retención online estándar) no sufrieron alteraciones y permanecen completamente aislados e intactos. La regla de salida inicial no afecta al canal online, permitiendo compras web para paradas intermedias antes de su horario de corte.

### Regla (13): Menores gratuitos y snapshots
- Se preserva la regla de gratuidad para menores de 5 años (0 a 4 años cumplidos) que viajan en brazos/falda (`is_lap_infant = True`, importe `$0.00`, acompañante adulto obligatorio).
- Los snapshots de pasajeros (`BookingPassenger` y `AuditEvent`) se almacenan de manera inmutable al momento de crear la reserva.

---

## 2. Alcance por Vendedor (Scoping)

Se implementó el aislamiento estricto de visibilidad y operación por vendedor:
- **QuerySets acotados**:
  - `_visible_bookings(user)`: Si el usuario no es Administrador, filtra `Booking.objects.all().filter(seller=user)`.
  - `_visible_payments(user)`: Si el usuario no es Administrador, filtra `Payment.objects.all().filter(booking__seller=user)`.
- **Listados y contadores**: Los paneles de reservas y pagos pendientes muestran únicamente las operaciones propias del vendedor autenticado.
- **Detalle y comprobantes**: El endpoint de descarga de comprobantes `/panel/pagos/<id>/comprobante/` rechaza el acceso si el usuario no es Administrador y no es el vendedor de la operación (`PermissionDenied`).
- **Aprobación de pagos**: En `/panel/pagos/<id>/revisar/`, solo el vendedor asignado o un Administrador pueden ejecutar la aprobación o rechazo.
- **Filtros y opciones en listados**: En `panel/payment_views.py::payment_list`, el contexto `all_sellers` se restringe exclusivamente al usuario actual para no administradores (`User.objects.filter(pk=request.user.pk)`), manteniéndose global solo para Administradores y superusuarios. Asimismo, si un vendedor no administrador intenta inyectar el filtro `?seller=` con un usuario ajeno, el parámetro se descarta y limpia, evitando la exposición o filtrado indebido de datos ajenos.

---

## 3. Comandos y Procesos Operativos de Mantenimiento

Para la expiración de reservas y gestión del ciclo de vida se cuenta con los siguientes procesos:

1. **Expiración de reservas sin pago o vencidas**:
   ```bash
   python manage.py run_operational_maintenance --task expire
   ```
   - Identifica reservas en estado `HELD` con `expires_at <= now` que no tienen comprobante en revisión.
   - Invoca `sales.services.expire_booking(booking_pk)`, marcando la reserva como `EXPIRED` y liberando las asignaciones a `RELEASED`.

2. **Expiración de pagos por transferencia vencidos**:
   ```bash
   python manage.py run_operational_maintenance --task payments
   ```
   - Identifica reservas vencidas que poseen pagos en revisión (`UNDER_REVIEW` o `AWAITING_VOUCHER`).
   - Invoca `payments.services.expire_public_transfer_if_expired`, actualizando el pago y liberando las butacas.

3. **Reconciliación y entrega de pasajes**:
   ```bash
   python manage.py run_operational_maintenance --task fulfillment
   ```
   - Procesa la emisión de pasajes y envío de notificaciones para reservas confirmadas pendientes.

---

## 4. Módulos Nuevos y Estado de Integraciones

Para respetar la restricción estricta de no editar `panel/permissions.py`, `panel/urls.py`, navegación compartida ni documentos canónicos, se crearon los siguientes módulos específicos:

- `sales/internal_transfer.py`: Lógica central de venta interna por transferencia (`calculate_internal_transfer_expiration`, `validate_internal_sale_timing`, `validate_complete_internal_data`, `create_internal_transfer_booking`, `create_immediate_transfer_sale`).
- `panel/internal_sales_permissions.py`: Reglas de autorización específicas (`can_create_manual_unexpired_reservation`, `can_review_transfer_payment`, `can_view_transfer_voucher`).
- `panel/internal_sales_urls.py`: Rutas auxiliares preparadas para navegación dedicada si se requiere desacoplar de `panel/urls.py`.
- `sales/test_internal_transfer.py`: Suite exhaustiva con 23 pruebas unitarias e integrales que validan las 13 reglas.

### Integraciones pendientes informadas
- Si la coordinación decide exponer las rutas de `panel/internal_sales_urls.py` de forma desacoplada en lugar de reutilizar las vistas existentes en `panel/urls.py`, se requerirá incluir `path("internal-sales/", include("panel.internal_sales_urls"))` en la configuración global de URLs. Actualmente, las vistas existentes (`panel/reservation_views.py` y `panel/payment_views.py`) fueron adaptadas para dar soporte inmediato a estas reglas sin romper contratos existentes.

---

## 5. Pruebas de Concurrencia en PostgreSQL (Estado: Pendiente)

- **Situación**: El entorno de trabajo actual (Windows) no cuenta con una instancia de PostgreSQL en ejecución (`localhost:5432` no responde a servicios PostgreSQL).
- **Declaración formal**: Se declara **pendiente** la ejecución de pruebas de concurrencia nativas bajo aislamiento de PostgreSQL (tales como verificación de bloqueos `pg_try_advisory_lock` y provisión de colisiones de restricción única con `select_for_update()`).
- **Mitigación transaccional**: Todo el código de negocio fue desarrollado e instrumentado utilizando transacciones atómicas de Django (`@transaction.atomic`), bloqueos determinísticos ordenados por clave primaria (`select_for_update()`) y verificación de colisiones en base de datos.
