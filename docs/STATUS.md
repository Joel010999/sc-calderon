# Estado de SC Viajes

- Proyecto: SC Viajes.
- Estado: fundación técnica aprobada.
- `main` contiene el PR `#1`, commit `b6e63d1` (`b6e63d15b1e20f56f7d83c3a5909813b37f8250a`).
- Panel personalizado, seguridad, health check, roles y estáticos locales: completados.
- Rama activa de desarrollo: `feature/domain-foundation-20260907`.
- Etapa actual: fundación de dominio de `operations` implementada.
- Documentación creada: `AGENTS.md`, `docs/PROJECT_SPEC.md`, `docs/ARCHITECTURE.md`, `docs/DECISIONS.md` y `docs/STATUS.md`.
- Próxima etapa: panel personalizado de operaciones.
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

## Observaciones para la próxima etapa

- `core` conserva modelos anteriores (`Ruta`, `Parada`, `Bus`, `Salida`, `Reserva` y `ReservaItem`) que se superponen con parte del dominio. No se modificaron, conectaron ni migraron hacia `operations`. Su convivencia o retiro requiere una decisión explícita antes de integrar funcionalidad que los utilice.
- Las operaciones sensibles del futuro panel deben cumplir la auditoría de usuario, fecha, acción y valores relevantes definida en [ARCHITECTURE.md](ARCHITECTURE.md#reglas-de-diseño).
- Continúan pendientes las definiciones con Sandro de [DECISIONS.md](DECISIONS.md#pendiente-de-consultar-con-sandro).

Actualizar este documento cuando finalice cada módulo.
