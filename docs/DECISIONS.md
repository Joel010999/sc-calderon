# Decisiones de SC Viajes

## Escenario reproducible de aceptacion (2026-10-05)

Se adopta `seed_demo_scenario` solo para DEBUG, tests o entorno demo explicito.
`--dry-run` no escribe y `--reset` solo elimina registros marcados por prefijo o
dominio reservado. No se agregan bypasses de permisos, migraciones ni integraciones
reales; passwords y tokens se generan fuera del repositorio y nunca se imprimen.
## Pureza de GET y auditoría de descargas (2026-10-05)

Se confirma que una solicitud GET no debe persistir expiraciones oportunistas ni
crear auditoría. Resumen, pago pendiente y transferencia muestran el vencimiento
virtualmente; un POST de operación o mantenimiento persiste la transición y
mantiene el rechazo atómico de acciones vencidas. QR público y descargas mantienen
sus controles de autorización, pero no registran eventos por lectura; la auditoría
de acciones mutables continúa en POST autenticado.

## Validación segura de embarque (2026-10-01)

- El dominio de embarque pertenece a `tickets` porque el pasaje ya vincula el token QR seguro con su reserva, tramo, pasajero y asignación de butaca.
- `BoardingRecord` conserva todos los embarques y sus reversiones. Solo puede existir un registro activo por pasaje mediante una restricción única condicional compatible con PostgreSQL y SQLite.
- El panel acepta el token del QR existente o el código exacto del pasaje, pero nunca confía en UUID públicos ni en relaciones enviadas por el navegador. Se exige seleccionar el viaje y se revalidan server-side estado del pasaje, reserva, asignación, relaciones y viaje.
- Administrador y Vendedor pueden validar; solo Administrador puede revertir con motivo obligatorio. Ambas operaciones generan auditoría y las reversiones no eliminan datos.
- La cámara del navegador, el funcionamiento offline y la política automática de no-show quedan explícitamente fuera de esta entrega.

## Confirmado

La especificación es la referencia completa. Este resumen no convierte referencias iniciales ni puntos pendientes en requisitos definitivos.

| Tema | Decisión y referencia |
| --- | --- |
| Producto | Venta online de pasajes y gestión interna mediante panel personalizado. [Producto](PROJECT_SPEC.md#producto). |
| Recorridos | Dos sentidos con orden y permisos de subida y bajada definidos; validación de origen y destino; sin reventa de una butaca por tramos en el mismo viaje para el MVP. [Recorridos](PROJECT_SPEC.md#recorridos). |
| Compra | Ida o ida y vuelta, varios pasajeros, compra como invitado, correo y contraseña o Google; sin códigos de acceso por correo ni Apple/iCloud en la primera versión. Correo de compra obligatorio y consentimiento comercial explícito, separado y auditable. Los datos de pasajero son una referencia por validar y las reglas de menores siguen abiertas. [Compra](PROJECT_SPEC.md#compra). |
| Colectivos | Cama y semicama con precios diferentes, distribución flexible y sin cambio de colectivo una vez programado el viaje en la operación informada. Las 60 butacas y su distribución por planta son referencias que requieren validación productiva. [Butacas y colectivos](PROJECT_SPEC.md#butacas-y-colectivos). |
| Disponibilidad | Retención online de 15 minutos y reserva manual de 24 horas, configurables; comprobante enviado a revisión manual. Cierre online una hora antes de la subida y ventas manuales de administradores y vendedores hasta el inicio del viaje, con igual efecto en disponibilidad, caja, ventas y métricas. El vencimiento durante revisión no está definido. [Disponibilidad y vencimientos](PROJECT_SPEC.md#disponibilidad-y-vencimientos). |
| Pagos | Efectivo manual, transferencia a Mercado Pago con comprobante y aprobación manual, QR de Mercado Pago y tarjetas por Payway. Integraciones reales posteriores a la migración. [Pagos](PROJECT_SPEC.md#pagos). |
| Pasajes | PDF por correo y descargable en la web, sin envío automático por WhatsApp; diseño basado en material del cliente y QR único. Validación de QR y embarque posteriores. [Pasajes](PROJECT_SPEC.md#pasajes). |
| Cambios | Sin cancelación por el pasajero; solicitud de cambio hasta cinco horas antes de la subida, con penalización aún sin importe o fórmula. Políticas de empresa y cambio de nombre pendientes. [Cambios y cancelaciones](PROJECT_SPEC.md#cambios-y-cancelaciones). |
| Migración | Origen AppSheet/Google Sheets; etapa penúltima y anterior a activar pasarelas; prueba inicial, auditoría, validaciones, errores, conteos e importación repetible sin duplicados. Implementación diferida hasta conocer los datos reales. [Migración](PROJECT_SPEC.md#migración). |

La arquitectura acordada y los límites de los módulos están en [ARCHITECTURE.md](ARCHITECTURE.md): monolito modular Django, PostgreSQL productivo, panel propio, recursos frontend locales, Railway y WhiteNoise. La primera implementación funcional será `operations`. La tecnología de procesamiento en segundo plano queda diferida y requiere una decisión aprobada.

### Retiro del dominio del prototipo

- Los modelos `core.Ruta`, `core.Parada`, `core.Bus`, `core.Salida`, `core.Reserva` y `core.ReservaItem` pertenecían exclusivamente al prototipo.
- El propietario confirmó explícitamente que no existe información real que deba conservarse en esas tablas. No se requiere una migración de datos.
- Se retiran mediante una nueva migración de esquema antes de construir funcionalidad nueva, conservando la aplicación `core` y su infraestructura.
- `operations` queda como única fuente de verdad para paradas, recorridos, colectivos, butacas, viajes y tarifas.
- La futura migración desde AppSheet/Google Sheets es una etapa diferente y permanece pendiente según [PROJECT_SPEC.md](PROJECT_SPEC.md#migración).

### Permisos iniciales del panel de configuración operativa

- Un superusuario o un usuario del grupo `Administrador` puede consultar, crear, editar, activar y desactivar colectivos y butacas.
- El grupo `Vendedor` puede consultar recorridos, colectivos y butacas, sin modificar estructura operativa.
- Un usuario `is_staff` sin grupo `Administrador` puede ingresar y consultar, pero no modificar estructura operativa, salvo que sea superusuario.
- Un usuario común no tiene acceso al panel. Un usuario anónimo es redirigido al login.
- Los permisos se verifican en el servidor, en las vistas y en los servicios de escritura. Ocultar acciones en la interfaz no reemplaza esas validaciones.
- Los recorridos y paradas son de solo lectura en esta etapa, incluso para administradores.
- Activar y desactivar son acciones POST con CSRF e idempotentes. No se ofrece eliminación física de colectivos ni butacas.
- Cada cambio efectivo de colectivo o butaca registra actor, acción, entidad, descripción, valores anteriores y posteriores y fecha en la misma transacción. No se registran eventos para operaciones fallidas ni cambios sin efecto. La auditoría no tiene interfaz de edición o eliminación.

### Viajes programados, horarios y tarifas en el panel

- Superusuarios y miembros de `Administrador` pueden crear viajes y gestionar tarifas. Vendedores y usuarios `is_staff` sin ese grupo solo consultan, salvo que sean superusuarios. Se mantienen las restricciones para usuarios comunes y anónimos.
- La creación utiliza dos pasos: selección de recorrido y colectivo, y carga de horarios con revisión visual antes de confirmar. El recorrido y el colectivo deben estar activos; el colectivo debe tener al menos una butaca activa.
- El panel trabaja en horario argentino y convierte las entradas locales a fechas conscientes de zona horaria. La primera salida debe estar en el futuro; esta restricción pertenece al panel, no al servicio de dominio, para no impedir una futura importación histórica.
- El mismo colectivo no puede tener viajes superpuestos. El intervalo se obtiene de la primera y última parada de cada viaje. Solo los viajes `CANCELLED` dejan de bloquear el colectivo.
- Los intervalos que solamente se tocan están permitidos provisionalmente. No se agrega un tiempo de preparación por suposición; queda pendiente de consultar con Sandro.
- La comprobación de superposición y la creación se realizan dentro de una transacción con bloqueo del colectivo. Los horarios y permisos se copian a las paradas concretas del viaje.
- No se permite editar recorrido, colectivo, horarios ni estado del viaje en esta etapa. Tampoco se ofrecen eliminaciones de viajes o tarifas.
- Las tarifas se limitan al viaje de la URL y a tramos válidos de su cronograma. Se usan importes `Decimal`; el estado se cambia únicamente por acciones POST con CSRF e idempotentes.
- La creación del viaje y cada cambio efectivo de tarifa registran auditoría en la misma transacción. El viaje se audita con un único evento y su cronograma completo; los importes se serializan como texto decimal y las fechas como ISO 8601.

### Fundación de reservas y disponibilidad de butacas

- Se adopta `Booking` como agregado principal único, acompañado por `BookingLeg`, `BookingPassenger` y `SeatAssignment`. No se crean entidades separadas para `Compra`, `Reserva` y `Venta`.
- Canales soportados: `ONLINE` y `MANUAL`. Estados de reserva: `HELD`, `CONFIRMED`, `EXPIRED` y `RELEASED`.
- Plazos de vencimiento y cierre configurables:
  - Retención online: 15 minutos por defecto (`SALES_ONLINE_HOLD_MINUTES`). Cierre de venta online a la hora exacta previa a la subida (`SALES_ONLINE_CUTOFF_MINUTES`, validando `now >= cutoff_time`).
  - Reserva manual: 24 horas por defecto (`SALES_MANUAL_HOLD_HOURS`).
  - Ambos canales excluyen únicamente estados iniciados (`STARTED`) y finales (`COMPLETED`, `CANCELLED`); el canal `ONLINE` admite viajes en estado `BOARDING` siempre que no se haya alcanzado el horario límite de corte.
- Autorización de ventas manuales:
  - Exige un usuario activo perteneciente al grupo `Vendedor`, `Administrador` o superusuario. Las reservas online no admiten vendedor. No se amplían permisos sobre el módulo `operations`.
- Límites de pasajeros y tramos:
  - Tramos admitidos: 1 (solo ida) o 2 (ida y vuelta).
  - En reservas de dos tramos, se exige obligatoriamente que ambos tramos correspondan a viajes distintos (`trip1 != trip2`).
  - El viaje de vuelta debe invertir estrictamente las paradas de origen y destino de la ida, comparando los identificadores de parada subyacentes (`TripStop.stop_id`).
  - La salida del viaje de vuelta (`ts_orig2.scheduled_at`) debe ser estrictamente posterior (`>`) a la llegada del viaje de ida (`ts_dest1.scheduled_at`), rechazando horarios contiguos/iguales o anteriores.
  - Límite de pasajeros: 4 por defecto, configurable en settings (`SALES_MAX_PASSENGERS_PER_BOOKING`) y validado en el servidor.
  - `BookingPassenger` modela únicamente la posición relativa (1 a N) sin almacenar datos personales definitivos, a la espera de la validación con Sandro.
- Disponibilidad de butacas (`get_trip_availability`):
  - Exige especificar ambas paradas (`origin_stop` y `destination_stop`) o ninguna de las dos; rechaza con `ValidationError` consultas con paradas parciales (solo origen o solo destino).
- Atomicidad, orden de bloqueos y recarga de base de datos:
  - Toda creación y confirmación es atómica bajo `transaction.atomic`.
  - Orden coherente de bloqueos: primero viajes involucrados con `select_for_update().order_by("pk")`, luego expiración oportunista inicial acotada a esos viajes, y luego bloqueo ordenado de butacas solicitadas con `Seat.objects.select_for_update().order_by("pk")`. Este orden coordina y serializa con los bloqueos que el panel de configuración operativa realiza sobre `Seat` al editar.
  - Relectura del reloj en `create_booking`: tras adquirir los bloqueos sobre viajes y butacas, se relee `timezone.now()` (salvo que `now` haya sido provisto explícitamente en tests) para asegurar que el corte online (`cutoff_time`) y la expiración (`expires_at`) se evalúen con el tiempo efectivo posterior al bloqueo, impidiendo ventas tardías si hubo demoras al esperar los bloqueos.
  - `create_booking` recarga obligatoriamente de la base de datos las instancias de `Trip`, `Seat`, `TripStop` y `TripFare` activas, sin confiar en objetos provistos por el llamador.
  - El snapshot `TripStop` del viaje programado es la autoridad definitiva para orden y permisos de subida/bajada; cambios o eliminaciones posteriores en `RouteStop` no invalidan viajes programados ni reservas.
  - Si falla cualquier tramo o butaca, la transacción se revierte por completo sin restos huérfanos.
- Butacas e integridad condicional:
  - Una butaca se reserva para el viaje completo sin reventa por tramos intermedios en el MVP.
  - Se implementa `UniqueConstraint(trip, seat)` condicional para estados `HELD` y `CONFIRMED`. Al liberar o expirar una reserva, las asignaciones pasan a `RELEASED`, permitiendo reutilizar la butaca sin eliminar el registro histórico para auditoría.
  - Exclusivamente los errores de integridad (`IntegrityError`) correspondientes a colisiones en la asignación de butaca (`sales_active_trip_seat_unique`) se traducen a `SeatUnavailableError`. Esta detección se restringe estrictamente a `constraint_name` en PostgreSQL (`sales_active_trip_seat_unique`) y a la comparación por igualdad exacta de columnas en SQLite (`unique constraint failed: sales_seatassignment.trip_id, sales_seatassignment.seat_id`), sin comparaciones por subcadena ni fallbacks genéricos de texto que clasifiquen erróneamente errores ajenos con texto adicional. Errores ajenos se propagan intactos manteniendo el rollback atómico completo.
  - En condiciones concurrentes normales de servicio sobre PostgreSQL, el bloqueo exclusivo `Trip.objects.select_for_update()` serializa las operaciones, por lo que la prevalidación Python (`SeatAssignment.objects.filter(...).exists()`) detecta la butaca ocupada y lanza `SeatUnavailableError` antes de emitir el `INSERT`. La restricción condicional única actúa como defensa en profundidad a nivel de base de datos.
- Confirmación, liberación y expiración:
  - `confirm_booking`: bloquea inmediatamente la reserva (`Booking.objects.select_for_update()`) sin lecturas previas fuera de transacción (`b_pre`). La verificación de expiración se realiza dentro de la transacción con el reloj efectivo tras adquirir el lock. Si la reserva ya expiró, se transiciona atómicamente la reserva a `EXPIRED` y las asignaciones a `RELEASED`, se confirma ese cambio en la base de datos (commit) y sólo después se lanza `BookingExpiredError`, impidiendo que la liberación se revierta. El servicio es idempotente si la reserva ya está confirmada y sincroniza la instancia en memoria provista (`status`, `confirmed_at`, `updated_at`) incluso si ya estaba confirmada en la base.
  - `expire_booking`: servicio transaccional individual con semántica segura. Rechaza con `InvalidBookingError` reservas en estado `CONFIRMED` (protegiendo pasajes confirmados frente a cualquier intento de liberación) y reservas `RELEASED`. Rechaza con `ValidationError` reservas `HELD` aún no vencidas. Transiciona reservas `HELD` vencidas a `EXPIRED` y sus asignaciones a `RELEASED`. Es idempotente para reservas ya expiradas y sincroniza en todos los casos la instancia en memoria provista.
  - `release_booking` opera únicamente sobre reservas en estado `HELD` con comportamiento idempotente y sincronización de instancia. Rechaza con excepción de dominio (`InvalidBookingError`) cualquier intento de liberar reservas `CONFIRMED`, protegiendo pasajes confirmados de reventas no autorizadas a la espera de una política comercial de cancelaciones aprobada.
  - La expiración oportunista se acota estrictamente a los viajes afectados (`release_expired_bookings(trip_ids=...)`), evitando bloquear o evaluar globalmente todas las reservas vencidas de la base.
  - Compatibilidad PostgreSQL en `release_expired_bookings`: dado que PostgreSQL rechaza `SELECT DISTINCT ... FOR UPDATE`, la consulta sobre `Booking` prescinde de `DISTINCT` filtrando la clave primaria mediante una subconsulta sobre `BookingLeg` (`pk__in=BookingLeg.objects.filter(trip_id__in=trip_ids).values('booking_id')`), garantizando bloqueo determinístico por `order_by('pk')` y alcance exacto.
- Precios e importes:
  - El precio se congela como snapshot histórico en `Decimal` a partir de la tarifa activa (`TripFare.amount`).
  - Fechas conscientes de zona horaria (`America/Argentina/Buenos_Aires`).

### Panel personalizado de reservas manuales

- **Acceso y autorización**:
  - Exclusivo para usuarios autenticados pertenecientes a los grupos `Administrador`, `Vendedor` o superusuarios (`can_manage_reservations`).
  - Usuarios comunes o `is_staff` sin grupo reciben HTTP 403 Forbidden.
  - Usuarios anónimos son redirigidos a la pantalla de inicio de sesión (`panel:login`).
  - Navegación integrada en el panel personalizado con enlaces a "Reservas" y "Nueva reserva" en la barra lateral e indicadores en el resumen.

- **Datos personales de pasajeros (`BookingPassenger`)**:
  - Se incorporan al modelo los campos de identificación aprobados: `first_name`, `last_name`, `document_type`, `document_number`, `normalized_document`, `birth_date`, `nationality` y `gender` (opcional). Los datos de contacto (`email` y `phone`) corresponden a la reserva (`Booking`) y no se almacenan en el pasajero.
  - Se genera una única migración (`sales/migrations/0002_bookingpassenger_birth_date_and_more.py`).
  - `normalized_document`: almacena el documento sin puntos, espacios ni guiones en mayúsculas mediante `normalize_document()` con índice (`db_index=True`). No impone restricción de unicidad global para no bloquear compras recurrentes del mismo pasajero.
  - Los datos definitivos obligatorios permanecen en consulta con Sandro; el panel y el servicio validan campos completos para reservas manuales mientras se mantiene retrocompatibilidad (`passengers_data=None`) en el dominio base.

- **Ciclo de vida y creación de reservas manuales**:
  - Las reservas manuales se crean estrictamente en estado `HELD` mediante el servicio de dominio `sales.services.create_manual_booking` (nunca insertando `SeatAssignment` directamente).
  - Vencimiento automático configurable a 24 horas (`SALES_MANUAL_HOLD_HOURS`).
  - Se excluyen viajes en estados iniciados (`STARTED`) o finales (`COMPLETED`, `CANCELLED`); solo se admiten viajes vigentes (`SCHEDULED`, `BOARDING`).
  - Validación estricta en servidor de origen, destino, paradas intermedias, sentido, cronología (en ida y vuelta, la salida del regreso debe ser posterior a la llegada de la ida), disponibilidad en tiempo real, categorías y límite de pasajeros (`SALES_MAX_PASSENGERS_PER_BOOKING`).
  - Los precios se determinan exclusivamente en el servidor a partir de las tarifas activas (`TripFare`); no se confía en importes provistos por el cliente.
  - Quedan a la espera de pagos y confirmación en etapas posteriores; no se implementa confirmación económica, cobro, caja ni pasarelas en esta etapa.

- **Listado y filtros**:
  - Presenta identificador público (`public_id`), fecha y hora local, canal, estado con distintivos visuales (`HELD`, `CONFIRMED`, `EXPIRED`, `RELEASED`), correo de contacto, resumen de tramos/viajes, cantidad de pasajeros, vencimiento y vendedor.
  - Filtros por estado, fecha, viaje y vendedor.
  - Búsqueda por identificador público, correo de contacto o documento normalizado de cualquier pasajero asociado.
  - Paginación de 20 elementos por página con preservación de filtros en los enlaces.

- **Detalle y liberación segura**:
  - Detalle completo con datos de contacto, vendedor, tramos, paradas, pasajeros, butacas asignadas por planta/categoría, precios unitarios históricos y total en `Decimal`.
  - Historial de auditoría visible reutilizando `panel.AuditEvent`.
  - Acción de liberación (`release`) disponible **únicamente** para reservas en estado `HELD`, ejecutada mediante POST con protección CSRF a través de `panel.reservation_services.release_panel_booking`.
  - No se ofrece confirmación económica ni cancelación comercial de reservas `CONFIRMED`.

- **Auditoría e integridad transaccional**:
  - Cada creación y liberación de reserva manual registra un evento en `panel.AuditEvent` dentro de la misma transacción atómica (`transaction.atomic`). Si la auditoría falla, la operación completa se revierte.
  - Interfaz responsive desarrollada con HTML semántico y CSS local; sin dependencias externas ni CDNs.
  - Plano de asientos generado dinámicamente según la configuración de plantas (`Seat.Deck`) y coordenadas de butacas; sin esquemas rígidos hardcodeados.

## Fundación de pagos manuales (2026-09-18)

- Se crea `payments`, dependiente de `sales`; `operations` y `sales` no dependen de ella.
- Cada `Payment` cubre el importe total de una reserva manual HELD. No hay pagos parciales.
- Efectivo queda APPROVED y confirma la reserva y butacas en una sola transacción. Transferencia requiere comprobante y queda UNDER_REVIEW hasta aprobación o rechazo con motivo.
- Una transferencia vencida no puede aprobarse, no extiende el vencimiento y deja el pago sin modificar; la reserva se libera como EXPIRED antes del error de dominio.
- Comprobantes permitidos: PDF, JPG, JPEG y PNG, con límite configurable inicial de 10 MB y almacenamiento privado.
- Una restricción condicional impide dos pagos UNDER_REVIEW o APPROVED para una reserva. Rechazados y pendientes no cuentan en métricas.
- Quedan fuera de alcance caja, reembolsos, Mercado Pago, Payway, tarjetas, checkout público y pagos parciales.
- Pendientes con Sandro: política de vencimiento de transferencias durante revisión en checkout público. Antes de Railway debe definirse almacenamiento persistente para comprobantes.

## Fundación de checkout público (2026-09-19)

- `core` actúa como frontend público sin alterar la home institucional ni sus estilos locales.
- `operations` es la fuente única de verdad para recorridos, viajes, paradas, colectivos, butacas y tarifas.
- `sales` es la fuente transaccional para `Booking`, `BookingLeg`, `BookingPassenger`, `SeatAssignment` y `create_booking`.
- `payments` permanece como módulo de panel interno y no se expone públicamente.
- `core` no importa componentes ni vistas de `panel`.
- Búsqueda pública por origen, destino, fechas (ida y vuelta), pasajeros (1 a `SALES_MAX_PASSENGERS_PER_BOOKING`) evaluando horarios programados en `America/Argentina/Buenos_Aires` y corte online estricto (`SALES_ONLINE_CUTOFF_MINUTES`).
- Mapa de butacas construido dinámicamente según `Seat.Deck` y coordenadas `position_x` / `position_y` sin planos rígidos hardcodeados, para viajes de ida y vuelta.
- Formulario de pasajeros que captura datos requeridos de identidad (`first_name`, `last_name`, `document_type`, `document_number`, `birth_date`, `nationality`, `gender`) y contacto en la reserva (`email`, `phone`).
- `create_online_booking` corregido para aceptar `passengers_data` opcional manteniendo retrocompatibilidad.
- Revalidación estricta de todos los parámetros en el servidor: no se confía en identificadores, precios, categorías ni paradas enviadas por el navegador.
- Protección integral contra bots y abusos: CSRF obligatorio en todos los POST, honeypot invisible (`website`) y limitación razonable de holds en sesión (máximo 3 por cada 15 minutos) sin dependencias externas de Redis o Celery.
- Privacidad estricta: prohibido almacenar datos personales de pasajeros en cookies, sesiones o registros de logs.
- Resumen público protegido por token de sesión y `public_id` para prevenir accesos no autorizados (IDOR).
- Temporizador regresivo en cliente basado en `expires_at` (15 minutos para retención online `HELD`), con expiración mediante los servicios de dominio existentes (`sales.services.expire_booking`) al vencer el plazo.
- Botón de pago en el resumen que únicamente informa que las pasarelas públicas online se encuentran en proceso de integración técnica.

## Fundación de pasajes, generación de PDF y verificación QR (2026-09-19)

- Se adopta ReportLab y `qrcode` puro Python por máxima portabilidad y estabilidad entre Windows, Linux y Railway, evitando motores de renderizado HTML nativos o dependencias de navegadores headless.
- Se excluye `pyzbar` y cualquier dependencia nativa/C de requirements, CI y tests; la validación de QR en tests se realiza de forma estructural sobre la imagen extraída con Pillow (`PIL.Image`, `ImageChops`) y `qrcode` puro Python.
- Arquitectura desacoplada en tres capas para pasajes: estructura de datos inmutable (`TicketData`), generador PDF (`build_ticket_pdf`) y plantilla provisional reemplazable (`draw_provisional_ticket`). La plantilla visual provisional incluye la leyenda obligatoria y puede sustituirse en el futuro sin modificar el modelo de datos ni la lógica de dominio.
- `Booking` actúa como agregado transaccional bloqueado. Los pasajes congelan instantáneas inmutables: pasajero, documento enmascarado, tramos, horarios, butacas, categorías y el importe histórico pagado en `Decimal` proveniente directamente de `SeatAssignment.price` (sin consultar `TripFare`).
- Emisión atómica top-level: `issue_tickets_for_booking` rechaza anidamiento en bloques atómicos previos para evitar inconsistencias de archivos en caso de rollback exterior. Si ocurre cualquier fallo durante la emisión (PDF, almacenamiento, base de datos o auditoría), se revierte la transacción de base de datos y se eliminan físicamente todos los archivos creados.
- No se permite la reemisión de pasajes anulados (`VOID`) ni la reemisión parcial silenciosa. Si todos los pasajes ya están emitidos, el servicio opera de forma idempotente retornando las instancias existentes.
- Seguridad de tokens:
  - Generación de tokens opacos de alta entropía (>= 256 bits mediante `secrets.token_urlsafe(32)`).
  - Separación de tokens: el token de verificación QR (`verification_token_hash`) solo permite consultar el estado básico de validez y datos de viaje, sin dar acceso a la descarga del PDF ni exponer precios ni datos de contacto.
  - La descarga pública exige un token de descarga diferenciado (`download_token_hash`).
  - La base de datos almacena exclusivamente hashes SHA-256; los tokens en texto plano nunca se guardan en la base ni en registros de auditoría o logs.
- Control de acceso a descargas:
  - Descarga interna habilitada para usuarios activos con roles `Administrador`, `Vendedor` o superusuarios.
  - Usuarios comunes autenticados o miembros de staff sin esos roles reciben HTTP 403 Forbidden.
  - Descargas públicas con tokens inválidos o inexistentes devuelven HTTP 404 genérico para evitar enumeración.
  - Archivo físico ausente en almacenamiento devuelve HTTP 404 genérico (nunca 500).
- Verificación estricta de solo lectura:
  - La vista `/tickets/verify/` no realiza mutaciones de estado ni operaciones de embarque.
  - Privacidad total: jamás expone motivo de anulación ni ningún dato fuera de estado, código, pasajero, documento oculto, origen, destino, fecha, butaca y categoría. No expone precio, email, teléfono, documento completo, identificadores internos de base de datos ni datos de pago.
  - Rate limiting defensivo implementado mediante el cache de Django indexado por el hash SHA-256 de la IP remota (sin incluir tokens en las claves).
  - Cabeceras HTTP obligatorias: `Cache-Control: no-store, no-cache, must-revalidate, max-age=0`, `Referrer-Policy: no-referrer` y `X-Robots-Tag: noindex, nofollow`.
- Entrega de correos agrupada:
  - `send_booking_tickets` envía exactamente un correo electrónico a `Booking.email` con todos los pasajes PDF de la reserva adjuntos.
  - Registro de intentos en `TicketEmailAttempt` (estados `PENDING`, `SENT`, `FAILED`).
  - El estado `PENDING` se persiste atómicamente antes de iniciar cualquier operación I/O de red, evitando duplicaciones concurrentes. Los estados ambiguos no se auto-reintentan.
  - Reintentos permitidos exclusivamente tras un estado `FAILED` mediante el parámetro explícito `retry=True`.
  - El historial de intentos no almacena el cuerpo del correo y los mensajes de error se sanitizan de forma genérica (sin volcar excepciones del sistema).
  - El envío se ejecuta fuera de transacciones activas para no generar correos si hay un rollback posterior.
- Auditoría propia: `TicketAuditEvent` registra las acciones sensibles (`ISSUE`, `DOWNLOAD`, `VOID`, `EMAIL`, `VERIFY`) sin acoplarse a la aplicación `panel` y sin almacenar información personal identificable (PII).
## Flujo público de transferencia con comprobante (2026-09-19)

- `Payment` incorpora los estados `AWAITING_VOUCHER` (pendiente de comprobante) y `EXPIRED` (expirado sin comprobante).
- Plazos estrictos y configurables:
  - Ventana para transferir y subir comprobante: 5 minutos (`PAYMENTS_PROOF_WINDOW_MINUTES = 5`).
  - Plazo para revisión manual de operadores: 24 horas (`PAYMENTS_REVIEW_WINDOW_HOURS = 24`).
- Inicio del flujo: POST idempotente sobre reservas `ONLINE` en estado `HELD`, protegido contra IDOR mediante token de sesión (`booking_access_{public_id}`) y CSRF. Si ya existe un pago activo para la reserva, se reutiliza idempotentemente sin duplicar registros.
- Validación de comprobantes: extensión permitida PDF, JPG, JPEG y PNG de hasta 10 MB, con validación de contenido en servidor mediante firmas mágicas (magic bytes) y almacenamiento privado compatible con `default_storage`.
- Extensión del vencimiento de la reserva: el plazo de la reserva (`Booking.expires_at`) se extiende 24 horas únicamente tras guardar exitosamente el comprobante en estado `UNDER_REVIEW`. Si la carga falla, la transacción se revierte completa sin extender el plazo ni cambiar de estado.
- Expiración oportunista: si transcurren los 5 minutos sin subir el comprobante o si expira el plazo de retención, el pago en `AWAITING_VOUCHER` pasa a `EXPIRED`, la reserva pasa a `EXPIRED` y las butacas se liberan atómicamente a `RELEASED`.
- Revisión en panel interno: el panel permite aprobar o rechazar transferencias de forma atómica bajo bloqueos ordenados (`Booking` luego `Payment`), registrando eventos en `panel.AuditEvent`. La aprobación confirma pago, reserva y butacas; el rechazo exige motivo obligatorio y mantiene la reserva en `HELD` si aún está vigente.
- Integración en checkout público: en la interfaz pública solo se habilita el pago por transferencia bancaria; el efectivo no aparece y las pasarelas automáticas (Mercado Pago QR, Payway) se muestran como "Próximamente disponible" sin procesamientos reales.

## Pendiente de consultar con Sandro

- Datos obligatorios definitivos de cada pasajero.
- Límite máximo de pasajeros por compra.
- Tarifas y reglas para menores, niños y adolescentes.
- Importe o fórmula de penalización por cambios.
- Política ante cancelación del viaje por parte de la empresa.
- Saldo a favor o devolución.
- Cambio de nombre del pasajero.
- Distribución definitiva del colectivo y butacas inhabilitadas.
- Horarios habituales y duración entre paradas.
- Tiempo adicional de preparación del colectivo entre viajes.
- Tarifas reales por origen, destino y categoría.
- Hojas, columnas, relaciones y calidad de datos de AppSheet/Google Sheets.
- Plantilla definitiva en blanco del pasaje PDF y pasaje real de referencia a entregar por el cliente.
- Proveedor definitivo para envío de correo.

No resolver estas decisiones por suposición. Registrar la respuesta aprobada antes de implementar la regla correspondiente.

## Fulfillment de pasajes después de la confirmación económica (2026-09-20)

- `payments` confirma pago, reserva y butacas en una transacción; una importación perezosa crea el trabajo durable `TicketFulfillment` antes del commit para evitar perder la intención si el proceso cae.
- `transaction.on_commit` procesa el trabajo fuera de la transacción. Los fallos de PDF o correo se registran por separado, no revierten el pago y se recuperan mediante `process_booking_fulfillment(..., retry=True)` o `reconcile_confirmed_fulfillments`.
- No se usan signals, Celery, Redis, Mercado Pago QR ni Payway.
## Cuentas, acceso y consentimiento (2026-09-20)

- Se mantiene el usuario estándar de Django; no se reemplaza `AUTH_USER_MODEL`.
- Comprar como invitado sigue permitido y no vincula reservas por coincidencia de email.
- El cliente puede registrarse e iniciar sesión con email y contraseña. Recuperación mediante enlace seguro de Django; no se usan OTP ni códigos.
- Google queda preparado mediante OAuth con credenciales exclusivamente en entorno y sin secretos versionados. Apple OIDC usa Authorization Code + PKCE, `state` y `nonce` de un solo uso, validación RS256 de issuer/audience/expiración contra JWKS cacheado en memoria y credenciales exclusivamente en entorno. Nunca se persisten ni registran tokens; una identidad Apple no se vincula automáticamente por email a una cuenta existente.
- El reclamo de una compra invitada usa un token aleatorio, guardado como hash, de un solo uso y con vencimiento. La entrega automática por correo queda pendiente de la infraestructura de correo.
- El consentimiento comercial es opcional, separado, desmarcado por defecto y auditable; retirarlo no elimina reservas ni pasajes. Campañas y envíos masivos quedan pendientes.

## Endurecimiento operativo de fulfillment (2026-09-21)

## Storage privado y correo SMTP (2026-10-02)

- Se adopta `django-storages[s3]` como integración configurable para un bucket privado S3-compatible, sin acoplarse a AWS ni migrar archivos existentes.
- `filesystem` permanece como backend explícito para desarrollo/tests; producción debe usar `s3` y superar los checks de bucket, endpoint, HTTPS y credenciales.
- No se habilitan URLs públicas permanentes para pasajes o comprobantes; las vistas existentes continúan autorizando y abriendo los archivos directamente.
- SMTP se configura por variables de entorno. TLS y SSL son excluyentes, y se validan host, credenciales, puerto y timeout sin imprimir secretos.
- La migración futura de archivos requiere inventario, copia verificada y rollback; no forma parte de esta entrega.

## Mantenimiento operativo programado (2026-10-02)

- Se adopta `python manage.py run_operational_maintenance` como proceso efímero para un Cron futuro de Railway, con tareas explícitas de expiración de reservas, pagos públicos y fulfillment.
- La expiración selecciona `HELD` vencidas y delega en `expire_booking` o `expire_public_transfer_if_expired`; nunca modifica `CONFIRMED`, pagos aprobados ni viajes iniciados/completados.
- Cada trabajo se procesa de forma independiente, con límite máximo de elementos y tiempo, tolerancia a fallos y leases existentes de `TicketFulfillment`.
- `--dry-run` es estrictamente de lectura: no persiste trabajos ni auditoría y no realiza generación de PDF o correo; los logs sólo incluyen métricas y estados sin PII.

## Reportes básicos de ventas (2026-10-01)

- Se confirma que el primer reporte es informativo y de solo lectura: no implementa contabilidad, cierre de caja, devoluciones ni conciliación.
- Se contabiliza una única vez cada `Payment` APPROVED cuya reserva esté CONFIRMED, usando `Payment.created_at` como fecha del cobro.
- El rango máximo es configurable (`PANEL_REPORTS_MAX_RANGE_DAYS`, 366 por defecto); los filtros y la exportación se ejecutan server-side.

- `payments` depende explícitamente de `tickets` mediante una importación perezosa para crear el trabajo durable; la dependencia no es circular.
- `TicketFulfillment` conserva intentos, último intento, `next_attempt_at` y una concesión `lease_until`. Las concesiones vencidas se pueden recuperar sin cambiar tickets, UUID, QR ni pagos.
- La reconciliación manual se realiza con `python manage.py reconcile_fulfillments [--dry-run] [--limit N] [--status ...] [--booking UUID]`. El comando no ejecuta migraciones ni requiere infraestructura externa.
- La bandeja global del panel usa POST+CSRF, roles de Administrador/Vendedor y auditoría. No se habilitan acciones mutables por GET.
## Manifiesto operativo de pasajeros (2026-10-01)

## Notificaciones transaccionales (2026-10-02)

- Se adopta un outbox durable en `notifications`, separado del correo único de pasajes y de `TicketEmailAttempt`.
- La unicidad se define por evento, tipo y destinatario; el payload contiene sólo snapshots mínimos, nunca tokens, comprobantes, contraseñas ni secretos.
- Los callbacks `transaction.on_commit`, locks de fila y leases evitan crear envíos por transacciones revertidas y permiten recuperar fallos SMTP sin revertir pagos.
- Los eventos cubren reserva online retenida, transferencia iniciada, comprobante, aprobación/rechazo, vencimiento y confirmación manual con correo válido. Son transaccionales y no dependen del consentimiento comercial.
- La reconciliación y el reintento del panel usan límites configurables y no configuran proveedores reales.

## Bandeja operativa (2026-10-02)

Se adopta una bandeja unificada de consulta para Administrador/Vendedor, sin escrituras ni auditoría. Expone referencias públicas, estados y tiempos, no PII, comprobantes, tokens ni datos de pago. No reemplaza caja, contabilidad ni mantenimiento programado.

- El manifiesto se limita a un viaje solicitado y muestra solamente asignaciones de butaca `CONFIRMED` pertenecientes a reservas `CONFIRMED`; no incluye estados `HELD`, `EXPIRED` ni `RELEASED`.
- Los pasajeros de una compra de ida y vuelta aparecen una vez por cada viaje y utilizan los `TripStop` y la butaca del tramo correspondiente.
- El formato legal definitivo del manifiesto sigue pendiente de confirmación con Sandro. La vista operativa, de impresión y CSV incluye ahora el estado `Embarcado`/`Pendiente`; QR, caja, cancelaciones y envío automático siguen fuera de alcance.

## Ciclo de vida y corte de ventas (2026-10-01)

- Se confirman únicamente las transiciones `SCHEDULED -> STARTED` y `STARTED -> COMPLETED`; no se habilita cancelar viajes.
- El cambio se realiza desde una pantalla explícita de confirmación y luego por POST protegido con CSRF, con bloqueo `select_for_update()` y auditoría atómica, para Administrador y Vendedor. Se conservan `started_at` y `completed_at` para presentar el momento de cada transición en la zona horaria argentina.
- No se liberan automáticamente reservas HELD ni pagos en revisión al iniciar o finalizar. Se muestran advertencias operativas para resolverlos manualmente.
- La venta online conserva el corte configurable de una hora antes de la subida y el servidor rechaza nuevas reservas cuando el viaje ya inició. Las reservas manuales preexistentes tampoco pueden confirmarse mediante nuevos pagos una vez iniciado el viaje.
- La cancelación de viajes y sus consecuencias económicas (reembolsos, saldos, reubicaciones o liberación de reservas) siguen pendientes de una decisión de negocio.
### Gestión de clientes en el panel (2026-10-02)

- Se habilita una bandeja de consulta de clientes únicamente para los grupos Administrador y Vendedor.
- El módulo es inicialmente de solo lectura y no altera cuentas, reservas ni consentimientos.
- La relación cliente-reserva debe ser explícita mediante `CustomerBooking`; no se permite vinculación automática por email.
- Se ocultan hashes, tokens de reclamo/OAuth, comprobantes, documentos completos y otros secretos. El historial de consentimiento expone solo estado, versión, origen y fecha.
## Preflight de producción (2026-10-05)

- Se agregan endpoints separados de liveness y readiness. Liveness no consulta base ni proveedores; readiness responde 503 ante una dependencia crítica no disponible.
- `production_preflight` es una validación previa, no un despliegue: no conecta proveedores reales, no ejecuta migraciones y no imprime secretos. La salida distingue `PASS`, `WARNING` y `FAIL`, y `--json` es estable para automatización.
- El request ID puede venir de un header solo si cumple un formato seguro; de lo contrario se genera. Los logs omiten query strings, payloads, credenciales, tokens, comprobantes y PII.

## Worker de mantenimiento (2026-10-06)

Se adopta un worker residente opcional, separado del proceso web, que reutiliza
`run_operational_maintenance`. Para un cron externo se usa exclusivamente
`python manage.py run_maintenance_worker --once`; no se deben activar ambas
estrategias en el mismo entorno. El worker no introduce Celery, Redis, tablas de
heartbeat ni proveedores externos.

La exclusión entre procesos productivos se implementa con un advisory lock de
sesión PostgreSQL. La espera está limitada por configuración y la pérdida de la
conexión libera el lock; SQLite queda explícitamente limitado a comportamiento
funcional de desarrollo sin garantía de concurrencia. Las señales no cancelan
el trabajo actual: sólo impiden iniciar un ciclo nuevo y restauran los handlers
al salir.
- HSTS `includeSubDomains` y `preload` permanecen desactivados hasta una decisión explícita de despliegue.

## Explorador central de auditoría (2026-10-07)

Se reutilizan las bitácoras existentes mediante un servicio de lectura normalizado, sin nueva tabla ni duplicación. El acceso queda restringido a Administrador y superusuario; bandeja, detalle firmado y CSV son GET-only, con referencias públicas UUID únicamente. La retención legal y toda purga permanecen fuera de alcance.
Los campos privados excluidos son descripciones, IDs internos, contactos, documentos, tokens, hashes, comprobantes, credenciales, rutas privadas, IP y user-agent. `operations`, `sales`, `payments`, `cash_register` y notificaciones se observan desde `panel.AuditEvent`; pasajes y embarques desde `tickets.TicketAuditEvent`; consentimientos desde `customers.CustomerConsent`. La caja conserva su auditoría de dominio, pero el explorador sólo muestra su módulo, acción, resultado y referencia pública segura, nunca importes, motivos ni snapshots.

## Fundación de caja diaria (2026-10-07)

- Se adopta un módulo separado `cash_register`; `Payment` permanece como fuente
  de verdad y los pagos en efectivo `APPROVED` requieren una caja abierta del
  vendedor para crear exactamente un movimiento de ingreso.
- La unicidad de caja abierta es por vendedor, no global: PostgreSQL aplica una
  restricción parcial sobre `opened_by` y los servicios usan `select_for_update`.
  SQLite conserva el comportamiento funcional de desarrollo, sin garantía de
  exclusión entre procesos.
- Los movimientos son inmutables, con idempotencia por pago y auditoría. Los
  ajustes solo pueden registrarlos Administradores y requieren motivo. El cierre
  calcula total esperado, importe contado y diferencia sin alterar entidades de
  pagos, reservas, tickets o fulfillment.
- Caja diaria, devoluciones, saldos a favor, comisiones, impuestos, Mercado Pago,
  Payway y conciliación bancaria siguen fuera de alcance.

Se agrega reporte diario informativo con fecha operativa argentina, filtros server-side,
detalle paginado y CSV BOM neutralizado. Los vendedores sólo consultan sus cajas;
Administradores y superusuarios pueden consultar todas. La revisión administrativa
de un cierre es idempotente y persiste actor, fecha y observación en `CashSession`;
la migración `cash_register.0004` es necesaria para conservar ese estado y no se
aplica a bases reales.

## Accesibilidad y responsive (2026-10-08)

Se adopta `@axe-core/playwright` como dependencia de desarrollo para detectar regresiones WCAG en páginas representativas. Las excepciones no se silencian: cualquier violación del análisis axe hace fallar la prueba y las correcciones de contraste, landmarks, headings y mapa de butacas se mantienen en CSS/HTML local. La base E2E se elimina y recrea antes de migrar y sembrar el escenario sintético; no se usan datos reales ni se relajan permisos, CSRF o reglas comerciales.
## Gestión de usuarios internos (2026-10-08)

- Se mantiene el `AUTH_USER_MODEL` existente para usuarios internos y se conserva
  la separación estricta de `Customer`.
- El panel no usa Django Admin: la gestión se realiza mediante servicios y vistas
  personalizadas, con roles limitados a `Administrador` y `Vendedor`.
- Las invitaciones de Administrador requieren un superusuario autorizado; las de
  Vendedor pueden ser emitidas por un Administrador. No se gestionan superusuarios.
- No se eligen ni almacenan contraseñas por otro administrador. El enlace de
  establecimiento es de un solo uso, con expiración, y sólo se persiste su hash.
- El último Administrador activo queda protegido mediante bloqueos de filas
  PostgreSQL. Las sesiones se revocan al desactivar o cambiar el rol.
