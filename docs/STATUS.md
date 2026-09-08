# Estado de SC Viajes

- Proyecto: SC Viajes.
- Estado: fundación técnica aprobada.
- `main` contiene el PR `#1`, commit `b6e63d1` (`b6e63d15b1e20f56f7d83c3a5909813b37f8250a`).
- Panel personalizado, seguridad, health check, roles y estáticos locales: completados.
- Base de esta etapa: `main` sincronizada por fast-forward y verificada con el commit `a4bb423`.
- Rama activa de desarrollo: `feature/operations-panel-20260908`.
- Etapa actual: panel operativo base implementado.
- Documentación creada: `AGENTS.md`, `docs/PROJECT_SPEC.md`, `docs/ARCHITECTURE.md`, `docs/DECISIONS.md` y `docs/STATUS.md`.
- Próxima etapa: viajes programados y tarifas en el panel. Todavía no son administrables desde el panel.
- Pagos, ventas, PDF, autenticación pública y migración: todavía no implementados.
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
- No se agregaron pantallas de viajes, horarios, tarifas, ventas, reservas, pasajeros, pagos, PDF, QR, disponibilidad, autenticación pública, AppSheet ni workers.

## Retiro del prototipo y pendientes

- El dominio antiguo de `core` (`Ruta`, `Parada`, `Bus`, `Salida`, `Reserva` y `ReservaItem`) fue retirado del código mediante una nueva migración de eliminación posterior a `0001_initial`, que permanece intacta. La migración de retiro solo se aplicó en la base temporal del runner de tests; no en la base local ni en Railway.
- El propietario confirmó que no hay información real que conservar en esas tablas. `operations` es la única fuente de verdad del dominio operativo; se conserva `core` para el sitio y la infraestructura. Ver la [decisión confirmada](DECISIONS.md#retiro-del-dominio-del-prototipo).
- Se conservan las dos pruebas del registro de aplicaciones en `core/tests.py`: ausencia de los seis modelos antiguos y presencia de los ocho modelos de `operations`.
- Continúan pendientes las definiciones con Sandro de [DECISIONS.md](DECISIONS.md#pendiente-de-consultar-con-sandro).

Actualizar este documento cuando finalice cada módulo.
