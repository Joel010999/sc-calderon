# Arquitectura de SC Viajes

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

## FundaciÃ³n de pagos manuales

- `payments` depende de `sales` para reservas y snapshots de importe; `sales` y `operations` no dependen de `payments`.
- `Payment` representa un cobro manual completo: efectivo aprobado inmediatamente o transferencia bancaria bajo revisiÃ³n. El importe se calcula desde `SeatAssignment.price` y se conserva como `Decimal`.
- Las transiciones bloquean `Booking` y luego `Payment` dentro de `transaction.atomic`; la confirmaciÃ³n actualiza pago, reserva y butacas atÃ³micamente.
- Los comprobantes usan almacenamiento privado compatible con `default_storage`, nombres UUID no predecibles, extensiones PDF/JPG/JPEG/PNG y lÃ­mite configurable de 10 MB. La descarga requiere autenticaciÃ³n y permisos.
- El panel ofrece registro de efectivo, presentaciÃ³n y revisiÃ³n de transferencias, auditorÃ­a y mÃ©tricas de pagos aprobados. No se habilitan pasarelas, pagos parciales ni caja.
- Antes de producciÃ³n debe definirse almacenamiento persistente para comprobantes en Railway.

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

## Integraci?n de fulfillment econ?mico

La confirmaci?n contin?a siendo responsabilidad de `payments`. Dentro de la misma transacci?n se crea un `tickets.TicketFulfillment` durable y se registra un callback `transaction.on_commit`; el callback nunca revierte el pago y los trabajos pendientes o fallidos se recuperan con `tickets.services.reconcile_confirmed_fulfillments`. La emisi?n y el correo permanecen en `tickets`, sin se?ales ocultas, Celery o Redis.
