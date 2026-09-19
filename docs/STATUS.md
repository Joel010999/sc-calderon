# Estado de SC Viajes

- Proyecto: SC Viajes.
- Estado: fundación técnica aprobada.
- `main` contiene el PR `#1`, commit `b6e63d1` (`b6e63d15b1e20f56f7d83c3a5909813b37f8250a`).
- Panel personalizado, seguridad, health check, roles y estáticos locales: completados.
- Base de esta etapa: `main` sincronizada por fast-forward y verificada con el commit `fbe3585`.
- Rama activa de desarrollo: `feature/public-checkout-foundation-20260919`.
- Etapa actual: checkout público implementado y verificado.
- Documentación creada/actualizada: `AGENTS.md`, `docs/PROJECT_SPEC.md`, `docs/ARCHITECTURE.md`, `docs/DECISIONS.md` y `docs/STATUS.md`.
- Próxima etapa: pasarelas de pago (Mercado Pago, Payway), emisión de pasajes (PDF, QR) y migración desde Sheets (previa a la activación de pasarelas).
- Ventas comerciales finales, pasarelas de pago (Mercado Pago, Payway), caja, comprobantes, PDF, QR, correo transaccional y migración desde Sheets: todavía no implementados.
- Decisiones pendientes: consultar [DECISIONS.md](DECISIONS.md#pendiente-de-consultar-con-sandro).

## Fundación de operaciones

- Aplicación independiente `operations` registrada en Django, sin Django Admin, vistas, formularios, templates ni APIs.
- Modelos: `Stop`, `Route`, `RouteStop`, `Bus`, `Seat`, `Trip`, `TripStop` y `TripFare`.
- Recorridos con orden y permisos; butacas con posiciones configurables y capacidad calculada contando las activas; viajes con estados y tarifas por tramo y categoría mediante `Decimal`.
- Relaciones protegidas con `PROTECT` y restricciones de base de datos para unicidad, valores positivos y opciones válidas. No se agregaron índices adicionales a los de relaciones y restricciones de unicidad.
- Servicio `operations.services.schedule_trip(route=..., bus=..., schedules=...)`: recibe un mapa de identificadores de `Stop` a fechas con zona horaria, o una secuencia de pares para detectar duplicados. Exige exactamente un horario por parada, valida cronología estricta y crea el viaje y su fotografía de paradas en una única transacción. `departure_at` corresponde a la primera parada. No utiliza signals.
- Las validaciones de dominio se ejecutan explícitamente mediante `full_clean()`; no se sobrescribe `save()`. Los futuros puntos de escritura deben invocarlas, ya que `save()`, `update()` y las operaciones masivas no ejecutan automáticamente las validaciones de Django. Las reglas entre tablas de `TripFare` se validan en el dominio.
- Comando idempotente `setup_initial_operations`: crea únicamente las cinco paradas y los dos recorridos confirmados con sus paradas ordenadas. Conserva los datos existentes y rechaza configuraciones de recorrido incompatibles sin sobrescribirlas. Solo se ejecutó dentro de tests.
- Migración inicial: `operations/migrations/0001_initial.py`. Generada y probada únicamente en la base temporal de tests; no aplicada a la base local ni a bases reales.
- Se agregaron 50 tests de dominio; se mantienen los 14 tests existentes del panel.
- No se crearon colectivos de 60 butacas, horarios ni tarifas productivas. Tampoco reservas, ventas, disponibilidad, pagos, PDF, QR, embarque, autenticación pública, importadores ni workers.

## Panel de configuración operativa

- Resumen operativo con conteos reales de recorridos, colectivos y butacas activos. Los conteos de cama y semicama incluyen solo butacas activas, según su propio estado.
- Recorridos y paradas visibles en modo lectura, con orden y permisos de subida y bajada. Las vistas no cargan datos iniciales automáticamente.
- Colectivos y butacas administrables por superusuarios y miembros de `Administrador`. Vendedores y usuarios `is_staff` sin ese grupo tienen acceso de consulta. Ver [política inicial](DECISIONS.md#permisos-iniciales-del-panel-de-configuración-operativa).
- Formularios con campos explícitos; el colectivo de una butaca se obtiene de la URL y no puede cambiarse desde el formulario. Activación y desactivación mediante POST con CSRF, sin eliminación física.
- Mapa de butacas por planta construido con CSS Grid a partir de posiciones almacenadas. Interfaz responsive con CSS local, navegación, estados vacíos y errores accesibles.
- Auditoría `panel.AuditEvent` implementada con actor, acción, entidad, descripción, datos anteriores y posteriores y fecha. Los cambios y su auditoría se guardan en una única transacción explícita, sin signals. Si falla la auditoría, se revierte el cambio. No hay interfaz para editar ni eliminar eventos.
- Migración nueva `panel/migrations/0001_initial.py`: crea únicamente `AuditEvent`. Solo se aplica en las bases temporales del runner de tests; no se ejecuta sobre bases persistentes.
- Se agregaron 38 pruebas de permisos, formularios, estados, auditoría y regresión; se conservan las 66 pruebas anteriores.
- En la etapa de configuración base no se agregaron pantallas de viajes ni tarifas; se incorporan en la etapa siguiente, registrada a continuación.

## Panel de viajes y tarifas

- Listado de viajes con recorrido, colectivo, estado, salida y llegada locales: próximos primero y pasados del más reciente al más antiguo, con desempate por identificador. Las consultas cargan relaciones de forma conjunta para evitar N+1.
- Creación en dos pasos: selección de recorrido y colectivo válidos; luego horarios de todas las paradas y confirmación visual. La confirmación está firmada y vinculada a los datos revisados. No se precargan horarios productivos.
- `schedule_trip` rechaza recorridos y colectivos inactivos, colectivos sin butacas activas y superposición del mismo colectivo. Usa los extremos de la fotografía de paradas, ignora viajes cancelados y permite intervalos contiguos, sin inventar tiempos de preparación.
- La validación y escritura usan una transacción con bloqueo del colectivo. El panel exige salida futura; el dominio conserva la posibilidad de programar fechas históricas para una futura importación controlada.
- Detalle con cronograma completo, permisos de subida y bajada y tarifas por tramo y categoría. Recorrido, colectivo, horarios y estado del viaje no son editables.
- Tarifas creables y editables por administradores, con origen y destino limitados al viaje de la URL. Importes `Decimal`, validación de segmentos y duplicados, activación/desactivación POST con CSRF e idempotente, sin eliminación física.
- Auditoría extendida a viajes y tarifas, con cronograma completo en un evento de creación de viaje, importes como texto decimal y fechas ISO 8601. Un fallo de auditoría revierte la modificación completa; no se auditan formularios inválidos ni acciones sin efecto.
- Resumen operativo ampliado con viajes programados futuros, viajes en estado de embarque y acceso a Viajes. No se implementa gestión de embarque ni edición de estados.
- Se agregaron 45 pruebas específicas. Las 104 anteriores se conservan; se adaptaron únicamente sus datos de prueba a la exigencia de butacas activas, intervalos no superpuestos y los nuevos conteos del resumen.
- Continúan fuera de alcance pagos, vistas públicas, checkout, panel de ventas, caja, comprobantes, PDF, QR, correo transaccional, cambios comerciales, autenticación pública, AppSheet y workers.

## Fundación de reservas y disponibilidad de butacas

- Aplicación independiente `sales` registrada en Django (`sales.apps.SalesConfig`). `operations` permanece intacta como fuente de verdad y no depende de `sales`.
- Modelos agregados como núcleo del dominio de reservas: `Booking`, `BookingLeg`, `BookingPassenger` y `SeatAssignment`. No se crearon entidades separadas para `Compra`, `Reserva` ni `Venta`.
- `Booking`: agregado principal con identificador público `public_id` (UUID4 indexado y único), canal (`ONLINE` o `MANUAL`), estado (`HELD`, `CONFIRMED`, `EXPIRED`, `RELEASED`), correo obligatorio, teléfono opcional, vendedor asignado únicamente en canal `MANUAL`, fecha de expiración consciente y fecha opcional de confirmación. Índices compuestos en `[status, expires_at]` y `[created_at]`.
- `BookingLeg`: secuencia 1 o 2 (solo ida o ida/vuelta) con unicidad por reserva, relación con `operations.Trip`, paradas de subida y bajada (`TripStop`), y snapshots congelados de los nombres de paradas y horarios programados de subida y bajada. En reservas de dos tramos, se exige obligatoriamente que ambos correspondan a viajes distintos, que la vuelta invierta los `Stop` de origen y destino de la ida (`TripStop.stop_id`) y que la salida de la vuelta sea estrictamente posterior a la llegada de la ida.
- `BookingPassenger`: posición relativa de 1 a N con unicidad por reserva, sin campos de datos personales definitivos, a la espera de la validación con Sandro.
- `SeatAssignment`: asignación de butaca por tramo y pasajero; valida coincidencia entre el viaje y el tramo, y entre el colectivo y la butaca; registra snapshots históricos de número de butaca, categoría, precio en `Decimal` y moneda. Mantiene los registros en estado `RELEASED` para auditoría histórica.
- Restricción única condicional en base de datos: `UniqueConstraint(fields=['trip', 'seat'], condition=Q(status__in=['HELD', 'CONFIRMED']))`, lo cual impide sobreventa o colisiones mientras las butacas estén retenidas o confirmadas, permitiendo la reutilización inmediata cuando pasan a `RELEASED`.
- Servicios de dominio en `sales.services`:
  - `create_booking`, `create_online_booking`, `create_manual_booking`: creación transaccional atómica (`transaction.atomic`) con orden determinístico de bloqueos por PK: primero sobre los viajes (`Trip.objects.select_for_update()`), luego expiración oportunista inicial acotada a los viajes involucrados, y luego sobre las butacas solicitadas (`Seat.objects.select_for_update()`), coordinando con los bloqueos de edición de butacas del panel operativo. Relectura de `timezone.now` tras adquirir bloqueos (salvo inyección explícita en tests) para evaluar el corte online y la fecha de expiración con el tiempo efectivo real.
  - Recarga estricta desde la base de datos de los datos vigentes de `Trip`, `Seat`, `TripStop` y `TripFare`, sin confiar en instancias provistas por el llamador.
  - Autoridad de paradas y orden delegada exclusivamente en el snapshot `TripStop` del viaje programado; cambios posteriores en `RouteStop` no invalidan reservas ni disponibilidad.
  - El cierre de venta online opera a la hora exacta de corte (`now >= cutoff_time`). Tanto el canal `ONLINE` como el `MANUAL` excluyen viajes en estados iniciados (`STARTED`) y finales (`COMPLETED`, `CANCELLED`); `ONLINE` admite viajes en estado `BOARDING` antes del horario límite de la parada.
  - Traducción selectiva de errores de integridad: restringida estrictamente al `constraint_name` en PostgreSQL (`sales_active_trip_seat_unique`) y al mensaje exacto por igualdad en SQLite (`unique constraint failed: sales_seatassignment.trip_id, sales_seatassignment.seat_id`), sin comparaciones por subcadena ni comodines genéricos de texto; errores ajenos se preservan y mantienen el rollback atómico completo.
  - `get_trip_availability`: consulta disponibilidad de butacas con expiración oportunista previa acotada al viaje consultado, garantizando que una butaca reservada ocupa todo el viaje sin reventa por tramos intermedios en el MVP. Exige paradas completas (origen y destino) o ninguna, rechazando consultas parciales con `ValidationError`.
  - `confirm_booking`: confirmación transaccional e idempotente que bloquea `Booking` directamente sin lecturas `b_pre` fuera de transacción y verifica la expiración bajo bloqueo con el reloj efectivo. Si está vencida, transiciona atómicamente la reserva a `EXPIRED` y las asignaciones a `RELEASED`, confirma el cambio (commit) y sólo después lanza `BookingExpiredError` para no revertir la liberación. Sincroniza la instancia en memoria provista incluso si la reserva ya estaba confirmada en base.
  - `release_booking`: liberación que pasa reservas `HELD` y sus asignaciones a `RELEASED`, sincronizando la instancia en memoria. Rechaza con `InvalidBookingError` reservas `CONFIRMED` para impedir la reventa no autorizada de pasajes confirmados sin una política de cancelación aprobada.
  - `expire_booking` y `release_expired_bookings`: transición atómica de reservas vencidas a `EXPIRED` y butacas a `RELEASED`. `expire_booking` mantiene semántica segura individual rechazando con `InvalidBookingError` reservas `CONFIRMED` o `RELEASED`, rechazando reservas `HELD` vigentes con `ValidationError` y sincronizando la instancia en memoria. La selección en `release_expired_bookings` no usa `DISTINCT` para ser plenamente compatible con PostgreSQL `select_for_update`, filtrando por PK mediante subconsulta en `BookingLeg`.
- Reglas configurables en settings:
  - `SALES_MAX_PASSENGERS_PER_BOOKING = 4` (validado en servidor).
  - `SALES_ONLINE_HOLD_MINUTES = 15`.
  - `SALES_ONLINE_CUTOFF_MINUTES = 60` (cierre de venta online a la hora exacta previa a la subida).
  - `SALES_MANUAL_HOLD_HOURS = 24` (permitido en viajes `SCHEDULED` y `BOARDING`).
  - Validación de vendedor manual: requiere usuario activo perteneciente al grupo `Vendedor`, `Administrador` o superusuario, sin ampliar permisos operativos.
- Migración inicial `sales/migrations/0001_initial.py` creada y probada únicamente en bases temporales de tests; no aplicada a base local ni a bases reales.
- Se incorporaron 79 pruebas en `sales/tests.py`: sales descubrió 79, ejecutó 76 en SQLite, OK con skipped=1 (la clase PostgreSQL contiene tres métodos no ejecutados en SQLite); suite completa descubrió 228, ejecutó 225 en SQLite, OK con skipped=1. Las pruebas PostgreSQL verifican terminación de hilos tras `join()`, diferenciación de errores de sincronización en barrera frente a colisiones de butaca, serialización real bajo `Trip` lock, y provocación directa de la restricción condicional única (`sales_active_trip_seat_unique`) con verificación de `diag.constraint_name` y traducción selectiva de `IntegrityError` a `SeatUnavailableError`.
- Quedan explícitamente fuera de alcance en esta entrega: ventas finales, pasarelas de pago (Mercado Pago, Payway), vistas públicas, checkout, panel de ventas, caja, comprobantes, PDF, QR, correo transaccional, migración desde Google Sheets/AppSheet, Celery y Redis.

## Panel personalizado de reservas manuales

- Acceso y permisos:
  - Exclusivo para usuarios autenticados pertenecientes a los grupos `Administrador`, `Vendedor` o superusuarios (`can_manage_reservations`).
  - Usuarios comunes o `is_staff` sin grupo reciben HTTP 403 Forbidden. Usuarios anónimos son redirigidos a la pantalla de inicio de sesión (`panel:login`).
  - Navegación lateral integrada en el panel con enlaces a "Reservas" y "Nueva reserva", además de indicador de reservas pendientes en el panel principal (`dashboard`).
- Modelo de datos y migración:
  - Extensión de `BookingPassenger` con campos de datos personales aprobados de identidad: `first_name`, `last_name`, `document_type`, `document_number`, `normalized_document`, `birth_date`, `nationality` y `gender` (opcional). Los datos de contacto (`email` y `phone`) residen exclusivamente en `Booking`.
  - Normalización de documentos mediante `normalize_document()` con índice de base de datos (`db_index=True`) sin unicidad global, permitiendo reservas reiteradas del mismo pasajero.
  - Generación de una única migración: `sales/migrations/0002_bookingpassenger_birth_date_and_more.py`. Verificada con `makemigrations --check --dry-run` y probada únicamente en bases temporales de tests.
- Listado de reservas:
  - Tabla completa con identificador público (`public_id`), fecha y hora local, canal, estado con distintivos visuales (`HELD`, `CONFIRMED`, `EXPIRED`, `RELEASED`), correo de contacto, resumen de tramos/viajes, cantidad de pasajeros, vencimiento y vendedor.
  - Filtros por estado, fecha de reserva, viaje y vendedor.
  - Búsqueda unificada por identificador público, correo de contacto o documento normalizado de cualquier pasajero de la reserva.
  - Paginación de 20 registros por página manteniendo los parámetros de filtrado.
- Detalle y liberación segura:
  - Ficha de reserva con datos de contacto, vendedor, tramos, paradas, pasajeros, butacas por planta y categoría, precios unitarios históricos y total en `Decimal`.
  - Historial de eventos de auditoría integrado reutilizando `panel.AuditEvent`.
  - Acción de liberación (`release`) exclusiva para reservas en estado `HELD`, protegida por POST con CSRF mediante `panel.reservation_services.release_panel_booking`.
  - No se ofrece confirmación económica ni cancelación comercial de reservas `CONFIRMED`.
- Creación de reserva manual:
  - Flujo guiado para solo ida o ida y vuelta; búsqueda de viajes activos excluyendo viajes iniciados (`STARTED`) o finalizados (`COMPLETED`, `CANCELLED`).
  - Mapa interactivo de butacas generado dinámicamente por plantas (`Seat.Deck`) y coordenadas relativas sin planos rígidos hardcodeados, reflejando disponibilidad en tiempo real (`AVAILABLE`, `HELD`, `CONFIRMED`, `INACTIVE`) y categorías (cama/semicama).
  - Formularios de pasajeros (hasta `SALES_MAX_PASSENGERS_PER_BOOKING`) y contacto.
  - Validación estricta en servidor de paradas, horarios cronológicos, inversión de recorrido y salida posterior en viajes de vuelta, colisiones y tarifas vigentes.
  - Invocación estricta de `sales.services.create_manual_booking` (nunca insertando `SeatAssignment` directamente).
  - Las reservas se crean en estado `HELD` con vencimiento a 24 horas (`SALES_MANUAL_HOLD_HOURS`), a la espera de pagos y confirmación en etapas posteriores.
- Auditoría transaccional:
  - Registro de eventos en `panel.AuditEvent` para la creación y la liberación dentro de la misma transacción atómica (`transaction.atomic`). Si la auditoría falla, la operación se revierte.
- Pruebas y cobertura:
  - Se incorporaron 31 pruebas en `panel/test_reservations.py` cubriendo control de acceso (admin, vendedor, anónimo, usuario común, staff sin rol), listado, filtros, búsqueda por documento normalizado, detalle, creación solo ida e ida y vuelta, colisiones concurrentes, butacas ajenas, cálculo de tarifas en servidor, exclusión de viajes iniciados, liberación HELD, CSRF, IDOR y ausencia de recursos externos o CDNs.
  - La suite completa de Django ejecuta 256 pruebas en SQLite: `OK (skipped=1)` (259 descubiertas).
  - El módulo `sales` ejecuta 76 pruebas: `OK (skipped=1)` (79 descubiertas).
  - El módulo `panel` ejecuta 128 pruebas: `OK`.
  - El workflow CI de PostgreSQL (`.github/workflows/sales-postgres.yml`) fue actualizado para incluir la rama `feature/manual-reservations-panel-20260918`.
- Quedan explícitamente fuera de alcance en esta entrega: pagos, confirmación económica, checkout público, emisión de pasajes (PDF, QR), cancelaciones comerciales, migración desde Google Sheets/AppSheet, Celery y Redis.

## Retiro del prototipo y pendientes

- El dominio antiguo de `core` (`Ruta`, `Parada`, `Bus`, `Salida`, `Reserva` y `ReservaItem`) fue retirado del código mediante una nueva migración de eliminación posterior a `0001_initial`, que permanece intacta. La migración de retiro solo se aplicó en la base temporal del runner de tests; no en la base local ni en Railway.
- El propietario confirmó que no hay información real que conservar en esas tablas. `operations` es la única fuente de verdad del dominio operativo; se conserva `core` para el sitio y la infraestructura. Ver la [decisión confirmada](DECISIONS.md#retiro-del-dominio-del-prototipo).
- Se conservan las dos pruebas del registro de aplicaciones en `core/tests.py`: ausencia de los seis modelos antiguos y presencia de los ocho modelos de `operations`.
- Continúan pendientes las definiciones con Sandro de [DECISIONS.md](DECISIONS.md#pendiente-de-consultar-con-sandro).

## Pagos manuales y confirmación económica

Estado: implementado en `feature/manual-payments-20260918`, pendiente de revisión final.

- Se agregó la aplicación `payments` con migración inicial y modelo `Payment`.
- El panel permite registrar efectivo, presentar y revisar transferencias, descargar comprobantes protegidos y consultar métricas de pagos aprobados.
- Efectivo y aprobación de transferencia confirman atómicamente pago, reserva y asignaciones; el rechazo mantiene HELD mientras la reserva siga vigente.
- La restricción condicional de pagos activos, los bloqueos ordenados y las transacciones tienen cobertura SQLite y PostgreSQL.
- No están terminados caja, pagos parciales, Mercado Pago, Payway, tarjetas, checkout público, reembolsos, PDF, QR, correo ni pasarelas externas.
- Antes de producción se debe configurar almacenamiento persistente para comprobantes. La política de vencimiento de transferencias del checkout público sigue pendiente con Sandro.

## Fundación de checkout público

Estado: implementado en `feature/public-checkout-foundation-20260919`.

- **Frontend público y arquitectura**:
  - `core` gestiona las rutas y vistas públicas (`home`, `buscar_viajes`, `checkout`, `crear_reserva`, `resumen_reserva`, `expirar_reserva`) sin romper la home institucional ni sus secciones visuales.
  - `operations` permanece como única fuente de verdad para recorridos, viajes, paradas, colectivos, butacas y tarifas.
  - `sales` gestiona el agregado transaccional `Booking`, `BookingLeg`, `BookingPassenger` y `SeatAssignment`.
  - `payments` no se expone al público; el resumen muestra un aviso claro de que los pagos públicos online se encuentran en proceso de integración.
  - No se importa `panel` en `core`.
  - No se agregaron modelos nuevos ni migraciones (0 migraciones pendientes).

- **Búsqueda y disponibilidad**:
  - Búsqueda por origen, destino, fechas de ida y vuelta y cantidad de pasajeros (1 a `SALES_MAX_PASSENGERS_PER_BOOKING`).
  - Consulta de viajes vigentes (`SCHEDULED`, `BOARDING`) con fechas locales en `America/Argentina/Buenos_Aires`.
  - Aplicación del corte de venta online concreto (`SALES_ONLINE_CUTOFF_MINUTES` = 60 minutos antes de la subida).
  - Cálculo de disponibilidad real y tarifas activas por categoría (`TripFare`).

- **Selección de butacas dinámica y formulario**:
  - Distribución por planta (`Seat.Deck`) y coordenadas de grilla (`position_x`, `position_y`) construida dinámicamente con CSS Grid local.
  - Soporte para ambos tramos (ida y regreso) en compras de ida y vuelta.
  - Formulario de pasajeros con campos de identidad completos (`first_name`, `last_name`, `document_type`, `document_number`, `birth_date`, `nationality`, `gender`) y datos de contacto en la reserva (`email`, `phone`).
  - `create_online_booking` ampliado para recibir `passengers_data` opcional preservando compatibilidad.

- **Seguridad, atomicidad y protección de sesión**:
  - POST de creación protegido con CSRF, honeypot (`website`) y limitación razonable de holds en sesión (máximo 3 en 15 minutos) sin Redis ni Celery.
  - Revalidación estricta de todos los parámetros en servidor sin confiar en datos enviados por el navegador.
  - Privacidad: no se almacenan datos personales en cookies ni logs.
  - Resumen protegido mediante token seguro de sesión asociado al `public_id`, previniendo vulnerabilidades IDOR.
  - Temporizador regresivo en cliente basado en `expires_at` (15 minutos para retención `HELD`). Al expirar el tiempo, se transiciona automáticamente la reserva a `EXPIRED` y se liberan las butacas mediante `expire_booking`.
  - Acción para cancelar y liberar el hold voluntariamente antes del vencimiento.

- **Pruebas y verificación**:
  - Se añadieron pruebas exhaustivas en `core/test_checkout.py` y `core/tests.py` cubriendo: renderizado institucional, validaciones de búsqueda, corte online, selección dinámica de butacas, honeypot, limitación de tasa, atomicidad y rollback ante colisiones, protección IDOR de sesión, temporizador, expiración automática y límites arquitectónicos.
  - El workflow `.github/workflows/sales-postgres.yml` fue actualizado para incluir la rama `feature/public-checkout-foundation-20260919`.
  - Pruebas ejecutadas con SQLite y verificaciones de Django (`check`, `makemigrations --check --dry-run`).

Actualizar este documento cuando finalice cada módulo.
