# Arquitectura de SC Viajes

## Fundación de validación de embarque

`tickets.BoardingRecord` es el registro durable del embarque. Cada fila conserva el pasaje, pasajero, viaje, asignación de butaca, operador y fecha/hora consciente de zona horaria; una restricción única condicional permite un solo registro `ACTIVE` por pasaje y conserva las filas `REVERSED` como historial.

La validación interna vive bajo `/panel/embarques/` y reutiliza el token opaco cuyo hash ya está almacenado en `Ticket.verification_token_hash`, además del código legible del pasaje. El servicio recarga y comprueba en servidor el pasaje, reserva `CONFIRMED`, asignación `CONFIRMED`, relaciones cruzadas y coincidencia exacta con el viaje seleccionado. La primera validación crea el registro y la siguiente devuelve el registro activo sin duplicarlo; la reversión es exclusivamente administrativa, exige motivo, usa POST+CSRF y conserva el historial.

El manifiesto agrega el estado `Embarcado`/`Pendiente` y sus totales mediante una anotación `Exists`, sin escribir al consultar ni exponer información de contacto, pagos o comprobantes. La verificación pública del QR permanece de consulta y no registra embarques. Cámara web, funcionamiento offline y política automática de no-show quedan fuera de alcance.

## Base técnica

- Monolito modular en Django.
- PostgreSQL en producción.
- Renderizado con Django Templates, CSS local y HTMX cuando aporte valor.
- Panel interno 100 % personalizado bajo `/panel/`.
- Django Admin permanece deshabilitado.
- Railway como plataforma de despliegue.
- WhiteNoise para archivos estáticos.
- Sin CDN de Tailwind ni dependencias frontend remotas.

Se prevé procesamiento en segundo plano para vencimientos, correos, PDFs y reintentos. La tecnología queda diferida: no instalar Celery ni Redis ahora, ni introducir infraestructura sin una decisión aprobada.

## Fundación de pagos manuales

- `payments` depende de `sales` para reservas y snapshots de importe; `sales` y `operations` no dependen de `payments`.
- `Payment` representa un cobro manual completo: efectivo aprobado inmediatamente o transferencia bancaria bajo revisión. El importe se calcula desde `SeatAssignment.price` y se conserva como `Decimal`.
- Las transiciones bloquean `Booking` y luego `Payment` dentro de `transaction.atomic`; la confirmación actualiza pago, reserva y butacas atómicamente.
- Los comprobantes usan almacenamiento privado compatible con `default_storage`, nombres UUID no predecibles, extensiones PDF/JPG/JPEG/PNG y límite configurable de 10 MB. La descarga requiere autenticación y permisos.
- El panel ofrece registro de efectivo, presentación y revisión de transferencias, auditoría y métricas de pagos aprobados. No se habilitan pasarelas, pagos parciales ni caja.
- Antes de producción debe definirse almacenamiento persistente para comprobantes en Railway.

## Checkout publico hasta reserva HELD

- `core` coordina la interfaz publica de busqueda, seleccion de viajes, butacas, pasajeros y resumen. La home institucional se conserva y las reglas de negocio no se implementan en JavaScript.
- Las consultas parten de `operations` y usan la fecha y permisos de `TripStop` de la subida concreta. La creacion delega en `sales.services.create_online_booking` y `create_booking`, que mantienen la atomicidad, el cierre online y los snapshots de precio.
- El estado previo a la creacion se limita a identificadores y criterios no sensibles en la sesion Django. Despues de crear `Booking`, el resumen requiere un token aleatorio asociado al `public_id` en esa sesion.
- El alcance publico incluye una superficie limitada de `core` para seleccionar transferencia y cargar comprobantes de un `Booking` `ONLINE` `HELD`, protegida por sesion, token y CSRF. Los endpoints internos de `payments`, comprobantes y datos del panel no se exponen publicamente; pasajes, PDF, QR, correo y cuentas de clientes siguen fuera de alcance.
- La proteccion inicial contra abuso combina CSRF, honeypot y limite de holds por sesion. Una politica distribuida de rate limiting y almacenamiento persistente quedan pendientes de infraestructura aprobada.

## Fundación de pasajes (PDF, QR y entrega agrupada)

- `tickets` es un módulo independiente que depende únicamente de `sales` para consultar agregados confirmados (`Booking`, `BookingLeg`, `BookingPassenger`, `SeatAssignment`). `sales`, `operations`, `payments`, `panel` y `core` no dependen de `tickets`.
- `Booking` actúa como raíz transaccional bloqueada (`select_for_update()`). Los pasajes capturan instantáneas inmutables de pasajero, documento enmascarado, tramo, horarios, butaca, categoría, moneda, código de reserva e importe histórico en `Decimal` (obtenido directamente de `SeatAssignment.price`, nunca recalculado desde `TripFare`).
- Emisión atómica y durabilidad: `issue_tickets_for_booking` opera como servicio top-level que rechaza anidamiento en bloques atómicos previos para evitar archivos huérfanos. En caso de fallo en cualquier etapa (PDF, almacenamiento, base de datos o auditoría), revierte la transacción de base de datos y elimina físicamente del almacenamiento cada archivo PDF generado.
- Generación portátil de PDF y QR: arquitectura desacoplada en datos (`TicketData`), generador (`build_ticket_pdf`) y plantilla provisional reemplazable (`draw_provisional_ticket`), basada en ReportLab y `qrcode` puro Python sin dependencias de motores HTML nativos, navegadores headless ni dependencias nativas/C (como `pyzbar` / `libzbar`).
- Seguridad de tokens y privacidad:
  - Generación de tokens opacos de alta entropía (>= 256 bits).
  - Separación estricta de alcances: el token de verificación QR (`verification_token_hash`) solo permite consultar validez básica en modo lectura sin exponer motivo de anulación ni ningún dato fuera de estado, código, pasajero, documento oculto, origen, destino, fecha, butaca y categoría. No expone precio, email, teléfono, documento completo, pagos ni otros pasajeros. La descarga del archivo PDF requiere un token de descarga diferenciado (`download_token_hash`) o autenticación interna de rol autorizado (`Administrador`, `Vendedor`, superusuario).
  - La base de datos almacena exclusivamente hashes SHA-256; los tokens en texto plano solo existen temporalmente en memoria o impresos en el pasaje.
- Almacenamiento privado: `PrivateTicketFileSystemStorage` independiente de `payments`, con `base_url=None` y rutas impredecibles por UUID para impedir acceso público directo o indexación.
- Entrega de correos agrupada: `send_booking_tickets` envía exactamente un correo al email de la reserva con todos los pasajes PDF adjuntos. Registra intentos en `TicketEmailAttempt` persistiendo el estado `PENDING` antes de la comunicación I/O de red, impidiendo envíos duplicados concurrentes y exigiendo reintento explícito (`retry=True`) únicamente tras fallos registrados.
- Pendientes antes de producción: definición de almacenamiento persistente en Railway y proveedor SMTP definitivo. La emisión automática ligada a la aprobación de pagos se integrará en una rama posterior.

## Ciclo de vida operativo de viajes (2026-10-01)

## Almacenamiento privado y correo productivo (2026-10-02)

Los pasajes PDF y comprobantes de transferencia usan storages privados separados
de los archivos estáticos. Desarrollo y tests conservan filesystem local; producción
debe seleccionar `PRIVATE_STORAGE_BACKEND=s3` mediante variables de entorno. El
backend usa `django-storages` con un bucket S3-compatible, endpoint opcional,
credenciales fuera del repositorio y objetos privados. No se generan URLs desde
los modelos: las descargas siguen pasando por vistas autenticadas o tokens
autorizados. La migración de archivos locales queda para una etapa posterior,
con inventario, copia verificada, checksum, ventana de corte y rollback.

WhiteNoise continúa sirviendo exclusivamente `staticfiles`; no comparte bucket,
raíz ni permisos con el almacenamiento privado. El correo conserva console en
desarrollo y locmem en tests, y permite SMTP genérico mediante entorno con TLS,
SSL, timeout y reply-to validados. La entrega mantiene `TicketEmailAttempt` y sus
reintentos idempotentes: un fallo SMTP no revierte pagos ni emisión de pasajes.

## Mantenimiento operativo efímero (2026-10-02)

`run_operational_maintenance` es un comando de proceso corto para un Cron futuro de
Railway. Reutiliza `sales.expire_booking` y `tickets.process_booking_fulfillment`,
procesa cada unidad en una transacción independiente y limita cantidad y tiempo.
Los locks de filas y leases existentes son compatibles con PostgreSQL; SQLite se
usa para pruebas. `--dry-run` sólo consulta y no crea trabajos, cambia estados,
audita ni envía correo. El comando no usa Celery, Redis, señales ni migraciones.

Las transiciones `SCHEDULED -> STARTED` y `STARTED -> COMPLETED` se ejecutan mediante servicios transaccionales del panel. Cada servicio bloquea el `Trip` con `select_for_update()`, valida la transición y registra actor, estado anterior, estado nuevo y timestamps en la auditoría dentro de la misma transacción; son idempotentes cuando el estado ya es el destino. Los timestamps `started_at` y `completed_at` se presentan en `America/Argentina/Buenos_Aires`. Sólo Administrador y Vendedor pueden invocarlas mediante POST con CSRF, y el inicio exige una pantalla de confirmación. Una vez iniciado el viaje, el dominio rechaza nuevas reservas, asignaciones y confirmaciones de ventas manuales, mantiene el corte online de una hora y no libera automáticamente reservas `HELD` ni transferencias pendientes.

## Módulos conceptuales

| Módulo | Responsabilidad |
| --- | --- |
| `operations` | Paradas, recorridos, colectivos, butacas, viajes y tarifas. |
| `sales` | Reservas, ventas, pasajeros y asignación de butacas. |
| `payments` | Pagos manuales en efectivo y transferencias bancarias; las pasarelas quedan para una etapa posterior. |
| `tickets` | Generación de PDF, QR y embarque. |
| `customers` | Cuentas, compra como invitado, Google y consentimiento comercial. |
| `notifications` | Correos transaccionales y comunicaciones autorizadas. |
| `migration` | Importación auditable desde Google Sheets/AppSheet. |
| `panel` | Interfaz interna personalizada. |
| `core` | Infraestructura compartida y sitio existente. |

Esta separación es conceptual. No crear todos estos módulos todavía: se implementarán progresivamente. La primera implementación funcional será solamente el módulo `operations`.

## Reglas de diseño

- Las integraciones de pago deben utilizar adaptadores para no mezclar APIs externas con el dominio.
- Los webhooks deberán ser idempotentes.
- El precio pagado deberá guardarse como una instantánea histórica en la venta.
- Usar `Decimal`, nunca `float`, para dinero.
- La disponibilidad de una butaca no puede depender solamente de lo mostrado en pantalla: debe validarse transaccionalmente en el servidor.
- Las operaciones sensibles deben registrar auditoría: usuario, fecha, acción y valores relevantes.
- Las reglas de recorridos y horarios deben vivir en el dominio, no en templates o JavaScript.
- El mapa visual debe construirse a partir de datos de posición de las butacas.
- Las interfaces deben estar en español argentino. Las fechas y horas deben ser conscientes de zona horaria, usando `America/Argentina/Buenos_Aires`.
- Toda regla de negocio debe tener pruebas. Los cambios de modelos deben acompañarse de sus migraciones, sin ejecutarlas contra bases reales.

Las reglas del producto se encuentran en [PROJECT_SPEC.md](PROJECT_SPEC.md). Las definiciones abiertas se registran en [DECISIONS.md](DECISIONS.md); no deben resolverse por suposición.

## Integración de fulfillment económico

## Reportes básicos de ventas (2026-10-01)

El panel personalizado expone `/panel/reportes/ventas/` y su descarga CSV en modo estrictamente lectura para Administrador/Vendedor. La fuente es `Payment` APPROVED unido a `Booking` CONFIRMED; cada fila representa un pago y se consulta con `select_related` para evitar N+1. Los filtros de fecha usan `America/Argentina/Buenos_Aires`, el límite de rango es configurable mediante `PANEL_REPORTS_MAX_RANGE_DAYS` (366 por defecto), y el CSV usa UTF-8 BOM, neutraliza fórmulas y no incluye PII, comprobantes ni datos de contabilidad/caja.

La confirmación continúa siendo responsabilidad de `payments`. Dentro de la misma transacción se crea un `tickets.TicketFulfillment` durable y se registra un callback `transaction.on_commit`; el callback nunca revierte el pago y los trabajos pendientes o fallidos se recuperan con `tickets.services.reconcile_confirmed_fulfillments`. La emisión y el correo permanecen en `tickets`, sin señales ocultas, Celery o Redis.
## Cuentas de clientes

La app `customers` reutiliza el usuario estándar de Django sin cambiar `AUTH_USER_MODEL`. `Customer` es un perfil separado y no otorga permisos del panel. El checkout sigue admitiendo invitados; las reservas nuevas de un cliente autenticado se vinculan explícitamente. Las compras invitadas se reclaman con un token aleatorio almacenado como hash, de un solo uso y con vencimiento configurable.

El acceso con Google usa OAuth configurable por variables de entorno y `state` en sesión; la simulación solo se habilita explícitamente para desarrollo y pruebas. No se mezclan cuentas de clientes con Administrador o Vendedor. Los tickets continúan en almacenamiento privado y se autorizan por asociación de cuenta.

Los consentimientos comerciales son eventos auditables separados de la compra y de la creación de cuenta; incluyen versión y origen y pueden revocarse sin borrar datos operativos.
La confirmación continúa siendo responsabilidad de `payments`. Dentro de la misma transacción se crea un `tickets.TicketFulfillment` durable y se registra un callback `transaction.on_commit`; el callback nunca revierte el pago y los trabajos pendientes o fallidos se recuperan con el comando `reconcile_fulfillments` o `tickets.services.reconcile_confirmed_fulfillments`. Por lo tanto existe una dependencia explícita y unidireccional `payments -> tickets`; `tickets` no importa `payments`. La emisión y el correo permanecen en `tickets`, sin señales ocultas, Celery o Redis.

Los trabajos tienen una concesión temporal (`lease_until`) para evitar doble procesamiento. Un proceso que cae deja el trabajo recuperable después de `TICKETS_FULFILLMENT_STALE_SECONDS`. La operación periódica futura podrá invocar `python manage.py reconcile_fulfillments --limit 100`; esta entrega no instala ni configura un programador.
## Notificaciones transaccionales (2026-10-02)

`notifications.TransactionalNotification` es un outbox durable separado de
`tickets.TicketEmailAttempt`. Mantiene `PENDING`, `PROCESSING`, `SENT` y
`FAILED`, con unicidad por evento, tipo y destinatario. Los servicios programan
filas mediante `transaction.on_commit`, por lo que un rollback no deja correos
huérfanos y SMTP no revierte pagos, reservas, butacas ni fulfillment.

El procesamiento reclama cada fila con `select_for_update`, asigna un lease y
envía fuera de la transacción. `SENT` es terminal; leases vencidos se recuperan.
PostgreSQL serializa procesos mediante el lock de fila. SQLite conserva la
semántica funcional para desarrollo, sin presentarse como garantía concurrente.
La tarea `notifications` de `run_operational_maintenance` y el reintento POST
del panel reutilizan este servicio. No se guardan tokens, comprobantes,
contraseñas ni secretos y no se registra el destinatario en logs o auditoría.

## Manifiesto operativo de pasajeros

## Bandeja operativa de solo lectura (2026-10-02)

`panel.inbox_services` compone señales existentes sin modelo propio ni migración. Valida filtros server-side, conserva contadores globales al paginar y enlaza a pantallas autorizadas. La prioridad es crítica para vencimientos/fallos/leases, alta para pendientes próximos y tickets incompletos, media para tareas pendientes o rechazos recientes e informativa para viajes próximos. No reemplaza caja, contabilidad ni mantenimiento programado.

El manifiesto del panel es una consulta de solo lectura sobre un `Trip` concreto. La fuente de filas es `sales.SeatAssignment`, limitada simultáneamente por `trip_id`, `leg__trip_id`, `AssignmentStatus.CONFIRMED` y `BookingStatus.CONFIRMED`. La consulta usa `select_related` para pasajero, reserva, tramo y `TripStop` con su parada, y no consulta pagos, comprobantes, tickets, correos ni auditoría.

Las paradas y el resumen se calculan con la fotografía real de `TripStop`; nunca se reconstruyen desde nombres o desde `Route`. La vista HTML, la vista de impresión y el CSV son endpoints GET protegidos por los roles Administrador/Vendedor y no modifican datos. El CSV se emite en UTF-8 con BOM y antepone una comilla simple a valores que podrían interpretarse como fórmulas de planilla.
### Gestión interna de clientes

El panel personalizado incorpora una consulta GET-only para Administrador y Vendedor. La lista y el detalle parten de `customers.Customer` y solo recorren asociaciones explícitas `CustomerBooking`; nunca enlazan reservas por coincidencia de email. Las reservas muestran estado, tramos, pasajes, pagos, fulfillment, estados de correo y reclamos únicamente como pendiente/consumido, sin tokens, hashes, comprobantes ni PII innecesaria. Los indicadores usan agregaciones y subconsultas de consentimiento actual para evitar N+1.
