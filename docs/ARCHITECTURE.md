# Arquitectura de SC Viajes

Este documento consolida la arquitectura del sistema SC Viajes tras la integración del PR #48, contrastando el código implementado, los modelos y las restricciones vigentes.

## Base técnica

- **Monolito modular en Django**: separación en aplicaciones cohesivas (`operations`, `sales`, `payments`, `tickets`, `customers`, `notifications`, `cash_register`, `panel`, `core`).
- **Base de datos de producción**: PostgreSQL (con bloqueos transaccionales `select_for_update`, advisory locks y restricciones de integridad condicionales). SQLite se reserva exclusivamente para desarrollo local y tests efímeros.
- **Frontend y estilos**: Django Templates, CSS local nativo sin frameworks pesados, sin dependencias remotas y sin CDN de Tailwind.
- **Panel interno**: 100 % personalizado bajo la ruta `/panel/`. Django Admin permanece estrictamente deshabilitado.
- **Zona horaria y moneda**: `America/Argentina/Buenos_Aires` para todas las fechas y horas visibles y lógicas de corte; `Decimal` (dos decimales) para todos los importes monetarios.
- **Plataforma de despliegue**: Railway y WhiteNoise para archivos estáticos locales (`staticfiles`).
- **Procesamiento asíncrono y mantenimiento**: no se utilizan Celery ni Redis. Los procesos en segundo plano se gestionan mediante comandos Django efímeros (`run_operational_maintenance`) y workers dedicados con advisory locks de sesión PostgreSQL.

## Módulo de operaciones (`operations`)

### Recorridos, paradas y control interprovincial
- Modelos: `Stop`, `Route`, `RouteStop`, `Bus`, `Seat`, `Trip`, `TripStop`, `TripFare`.
- La parada Güemes (`GUE`, provincia de Salta) forma parte integral de ambos recorridos (Córdoba → Jujuy y Jujuy → Córdoba).
- **Control interprovincial**: `Stop.province` define explícitamente la jurisdicción de cada parada. `Route.allows_journey` y los servicios de venta rechazan tramos cuyo origen y destino pertenezcan a la misma provincia.
- `setup_initial_operations`: comando idempotente con soporte `--dry-run` para configurar paradas y recorridos sin alterar históricos de viajes existentes.

### Tarifas por salida y categoría
- `TripFare` define la tarifa a nivel de salida (`trip`) y categoría de butaca (`seat_category`), con restricción de unicidad `ops_fare_trip_category_unique` sobre `(trip, seat_category)`.
- Reemplazó el modelo anterior de tarifas por par de paradas. El precio es uniforme en todos los tramos permitidos de la salida.
- Edición exclusiva por Administradores antes de la partida (`now < trip.departure_at`), serializada con bloqueo `select_for_update()` sobre `Trip` y `TripFare`, y auditada en `panel.AuditEvent`.
- Migración `operations.0003_tripfare_per_departure.py` consolida duplicados idénticos y aborta ante inconsistencias históricas sin inventar precios.

### Butacas prioritarias 51 y 54
- Las butacas 51 y 54 son prioritarias y se evalúan dinámicamente por número y salida, sin alterar el estado global de la butaca en el colectivo.
- Quedan bloqueadas para venta general hasta las 00:00 de Argentina de dos días calendario antes de la fecha local de salida.
- La validación se ejecuta en servidor en la consulta de disponibilidad y dentro de la transacción atómica de `create_booking`.

### Horarios estimados por parada
- Cada parada del cronograma (`TripStop`) almacena su horario estimado de arribo/partida en `scheduled_at`.
- `update_trip_schedules`: servicio transaccional protegido con bloqueo exclusivo sobre `Trip`, `Bus` y `TripStop`, accesible únicamente para usuarios del grupo `Administrador` antes del inicio del viaje.
- Valida orden estrictamente creciente de horarios, cruces de medianoche y no solapamiento del mismo colectivo con otros viajes activos.

## Módulo de ventas y reservas (`sales`)

### Agregado transaccional y canales
- Modelos: `Booking`, `BookingLeg`, `BookingPassenger`, `SeatAssignment`.
- Canales soportados: `ONLINE` y `MANUAL`.
- `Booking` actúa como raíz transaccional bloqueada (`select_for_update()`).

### Vencimientos y retención de butacas
- **Canal ONLINE**: hold temporal de 15 minutos (`SALES_ONLINE_HOLD_MINUTES`) con vencimiento explícito `expires_at`.
- **Canal MANUAL**: reserva rápida con `expires_at = None`. No expira automáticamente por tiempo; conserva la butaca hasta su pago o su liberación manual.
  > [!NOTE]
  > **Antecedente histórico**: la regla inicial contemplaba 24 horas (`SALES_MANUAL_HOLD_HOURS`), sustituida por la regla de retención indefinida sin vencimiento automático.
- **Liberación de reservas**: restringida exclusivamente al creador de la reserva manual en el panel. Las reservas `CONFIRMED` no admiten liberación administrativa sin política comercial aprobada.
- **Restricción de unicidad condicional**: `sales_active_trip_seat_unique` sobre `(trip, seat)` para estados `HELD` y `CONFIRMED`. Al expirar o liberar, pasa a `RELEASED` preservando la trazabilidad histórica.

### Menores de cinco años sin butaca
- Centralizado en `sales.minors` (`MINOR_FREE_AGE_YEARS = 5`).
- Todo pasajero estrictamente menor de 5 años (0 a 4 años cumplidos) califica como menor gratuito en falda/brazos (`BookingPassenger.is_lap_infant = True`). Al cumplir 5 años exactos o más, abona pasaje regular y ocupa asiento propio.
- Requiere madre o acompañante con butaca en la misma compra.
- No genera `SeatAssignment`, no ocupa disponibilidad del colectivo, mantiene importe `Decimal("0.00")` y no genera comisión.
- La condición se congela como snapshot inmutable al crear la reserva; compras ida/vuelta conservan la gratuidad en ambos tramos.
  > [!NOTE]
  > **Antecedente histórico**: el umbral inicial de menores de 2 años fue reemplazado por el de menores de 5 años tras la decisión de Sandro.

### Cortes de venta
- Venta online: cierra 60 minutos antes del horario de subida del pasajero.
- Venta manual: corte estricto a los 30 minutos previos a la subida; a los 29 minutos se rechaza.
- Viaje iniciado (`STARTED`): bloquea toda venta manual u online.

## Módulo de pagos y caja (`payments`, `cash_register`)

### Pagos manuales y transferencias
- `payments.Payment` representa el cobro de una reserva: efectivo (`APPROVED` inmediato) o transferencia bancaria (`UNDER_REVIEW`).
- **Revisión de transferencias**: potestad exclusiva de Administradores activos. Los rechazos conservan el intento fallido y permiten cargar un nuevo comprobante sin liberar la butaca retenida. La aprobación confirma atómicamente pago, reserva y butacas, disparando el fulfillment de pasajes.
- Almacenamiento privado de comprobantes (`PrivateFileSystemStorage` / backend S3 configurable con URLs no públicas).

### Caja diaria (`cash_register`)
- Separada de `payments`. Cada pago manual en efectivo aprobado se vincula con un `CashMovement` único dentro de una sesión abierta del vendedor (`opened_by`).
- Restricción de base de datos PostgreSQL: una única sesión de caja abierta por vendedor.
- Movimientos inmutables y cierres auditados.

## Módulo de pasajes y embarque (`tickets`)

### Plantilla PDF oficial y estructura de dos talones
- Generación de pasajes desacoplada en ReportLab (`tickets.rendering`):
  - 1 sola página (596 x 842 pt).
  - Dos talones: **Talón superior** ("Talón empresa/agencia") y **Talón inferior** ("Talón para el pasajero"), divididos por línea punteada.
  - Logo oficial incrustado en ambos talones (`static/assets/scviajes-logo.png`).
- **Supresión de códigos visuales**: la plantilla oficial aprobada no contiene códigos QR ni códigos de barras visuales en ninguno de los talones.
- **Tokens seguros y validación de embarque**: los tokens criptográficos de alta entropía (`verification_token_hash`, `download_token_hash`) se conservan en la base de datos (hashes SHA-256). El embarque se valida internamente en `/panel/embarques/` mediante lectura de teclado o ingreso manual del código legible, persistiendo `tickets.BoardingRecord`.

### Numeración secuencial persistente (`TicketSequence`)
- Modelo `TicketSequence` con bloqueo transaccional `select_for_update()` para evitar colisiones bajo concurrencia PostgreSQL.
- Formato `AAA-000000001` en adelante para pasajes reales (`AAA-000000000` para plantilla vacía).
- El mismo número se replica exactamente en ambos talones. Tramos y pasajeros múltiples reciben números correlativos consecutivos. Idempotente ante reenvíos y descargas; no se reutilizan números ante anulaciones.

### Emisión, almacenamiento y entrega
- `issue_tickets_for_booking`: servicio transaccional top-level que congela instantáneas inmutables de pasajero, documento enmascarado, tramos, vendedor, paradas, horarios e importe. Limpieza física dual ante errores (rollback de base de datos y borrado físico de PDFs).
- `send_booking_tickets`: envía un único correo agrupando todos los pasajes PDF de la reserva, con registro en `TicketEmailAttempt` y reintentos idempotentes.

## Módulo del panel interno (`panel`)

- **Control de acceso y roles**: usuarios del grupo `Administrador` y `Vendedor`. Superusuarios no asignables mediante panel.
- **Alcance por vendedor**: aplicado en el QuerySet base antes de cualquier filtro, búsqueda, métrica o exportación CSV. Los vendedores únicamente acceden a sus propias operaciones.
- **Gestión de staff y propietarios**: `panel.StaffProfile` registra datos del vendedor. La capacidad `is_owner` (no expuesta en la interfaz) restringe la invitación o promoción de administradores.
- **Reportes y balance de vendedores**:
  - Separa reservas confirmadas de pasajes y tramos comisionables.
  - Comisión de `Decimal("5000.00")` por cada asignación confirmada con precio positivo.
  - Muestra bruto, unidades comisionables, comisión y neto operativo. No ejecuta transferencias bancarias automáticas.
- **Auditoría centralizada**: `panel.AuditEvent` registra eventos transaccionales con snapshots `before`/`after` sanitizados (sin contraseñas, secretos ni PII innecesaria).

## Notificaciones y mantenimiento (`notifications`, `core`)

- **Outbox transaccional**: `notifications.TransactionalNotification` garantiza que no se envíen correos de transacciones revertidas (vía `transaction.on_commit`).
- **Worker de mantenimiento**: `run_maintenance_worker` y `run_operational_maintenance` gestionan expiraciones y fulfillment periódico con advisory lock de sesión PostgreSQL (`pg_try_advisory_lock`), impidiendo ejecuciones simultáneas.
- **Protección contra abuso**: `core.AbuseCounter` registra contadores basados en HMAC-SHA256 por ventana de tiempo, sin persistir direcciones IP ni datos sensibles en texto plano.
