# Estado del proyecto SC Viajes

Fecha de consolidación: 27 de octubre de 2026
Commit base integrado: `69297a5` (PR #48: *Actualizar gratuidad de menores, horarios por parada y pasaje PDF*)
Estado global: **Desarrollo e integraciones comerciales de Sandro completados en el repositorio; validaciones de staging real y decisiones comerciales pendientes de confirmación.**
> [!IMPORTANT]
> **No se declara producción aprobada.** El sistema cuenta con cobertura completa en pruebas sintéticas y CI automatizado, pero requiere validación en staging real con base PostgreSQL dedicada, configuración de proveedores externos (Railway, S3, SMTP, OAuth) y la resolución de definiciones comerciales abiertas con Sandro.

---

## 1. Resumen ejecutivo de funcionalidades vigentes comprobadas

Tras la integración sucesiva de los PRs #44, #45, #46, #47 y #48 sobre la base auditada, el sistema refleja el siguiente estado vigente:

1. **Reservas manuales sin expiración automática (restringidas por rol)**:
   - Sólo administradores y superusuarios crean reservas manuales con `expires_at = None`; no vencen por tiempo y mantienen la butaca retenida hasta su cobro o su liberación manual. *(Redacción original de esta consolidación: "creadas por vendedores"; corregida — desde `d2aa716` los vendedores registran ventas internas por transferencia con vencimiento `min(medianoche AR, salida inicial − 1 h)`.)*
   - Las reservas online conservan su hold temporal de 15 minutos (`SALES_ONLINE_HOLD_MINUTES = 15`).
   - El proceso de mantenimiento operativo no expira reservas manuales indefinidas ni reservas con pagos rechazados.
2. **Alcance por vendedor**:
   - El filtro por vendedor autenticado se aplica en el QuerySet base (`sales.Booking.objects.filter(...)` o restricción por actor) antes de cualquier consulta, detalle, filtrado, paginación, dashboard o exportación CSV.
   - Los vendedores sólo visualizan y gestionan sus propias ventas y reservas.
   - La acción de liberación de una reserva manual está permitida exclusivamente al usuario vendedor que la creó.
3. **Revisión de transferencias**:
   - **Compras públicas (ONLINE)**: exclusiva de administradores y superusuarios (decisión confirmada). Regla explícita por canal en servicio, permisos y vistas; vendedores bloqueados por UI, URL, POST y servicio (`payments.tests.PublicTransferReviewPermissionTests`).
   - **Ventas internas (canal MANUAL)** *(decisión confirmada por Joel)*: cada vendedor cobra en su **cuenta personal**; el **comprobante es obligatorio**; su carga por el **vendedor titular** es su declaración de cobro y, en la misma operación, aprueba el pago, confirma la reserva y dispara la emisión de pasajes. **No hay segunda aprobación administrativa** ni casilla adicional, y no se usan la cuenta bancaria ni Mercado Pago del negocio. Es una declaración del vendedor, no una verificación bancaria automática. *(Redacción original de esta consolidación: "exclusiva de administradores" para todas las transferencias.)*
   - Los rechazos de comprobantes preservan el intento de pago (`Payment`), registran el motivo obligatorio y mantienen la reserva y la butaca retenidas, habilitando al cliente o vendedor a cargar un nuevo comprobante.
   - La aprobación es terminal e idempotente: confirma atómicamente la reserva, el pago y las butacas, disparando el fulfillment durable de pasajes sin enviar notificaciones redundantes.
4. **Balance y comisiones de vendedores**:
   - Perfil de staff registrado en `panel.StaffProfile` (nombre, apellido, DNI, WhatsApp).
   - Capacidad propietaria `is_owner` (no expuesta en la interfaz) para autorizar invitaciones o promociones de administradores.
   - Comisión fijada en `Decimal("5000.00")` por cada asignación confirmada con precio positivo (por pasajero y tramo).
   - Los menores gratuitos sin butaca ($0,00) y las ventas web sin vendedor no generan comisión.
   - La liquidación distingue ventas brutas, unidades comisionables, comisión retenida y neto operativo. No se ejecutan transferencias bancarias ni rendiciones automáticas.
5. **Recorridos con Güemes y validación interprovincial**:
   - Se incorporó la parada Güemes (`GUE`, provincia de Salta) en ambos recorridos operativos (Córdoba → Jujuy y Jujuy → Córdoba).
   - Validación estricta en servidor: origen y destino deben pertenecer a provincias diferentes (`origin.province != destination.province`), rechazando la venta de tramos intraprovinciales.
   - Comando `setup_initial_operations` idempotente con soporte `--dry-run`, que preserva viajes e históricos existentes.
6. **Tarifas por salida**:
   - Modelo `operations.TripFare` configurado por salida (`trip`) y categoría de butaca (`seat_category`), con restricción de unicidad `ops_fare_trip_category_unique`.
   - Precio uniforme para todos los tramos permitidos dentro de la misma salida.
   - Edición administrativa exclusiva para usuarios del grupo `Administrador` antes de la hora de partida (`now < trip.departure_at`), con bloqueo transaccional y registro en `panel.AuditEvent`.
   - Congelamiento inmutable del precio en la reserva (`SeatAssignment.price`), de modo que cambios tarifarios posteriores no alteran reservas existentes (ni siquiera impagas).
7. **Butacas prioritarias 51 y 54**:
   - Las butacas 51 y 54 se evalúan dinámicamente como prioritarias por salida y número.
   - Quedan bloqueadas para venta general hasta las 00:00 (hora de Argentina, `America/Argentina/Buenos_Aires`) de dos días calendario antes de la fecha local de salida.
   - La restricción se valida server-side en disponibilidad y en el servicio transaccional de creación de reserva. Ocupaciones previas se preservan.
8. **Menores de cinco años (gratuidad sin butaca)**:
   - Todo pasajero estrictamente menor de 5 años (`0` a `4` años cumplidos) a la fecha de compra viaja gratis en brazos/falda (`is_lap_infant = True`).
   - Al cumplir exactamente 5 años o más, abona tarifa completa con butaca individual.
   - Debe viajar obligatoriamente asociado a una madre o persona acompañante con butaca en la misma compra.
   - No genera `SeatAssignment`, no consume disponibilidad del bus, mantiene importe `$ 0,00`, no suma unidad comisionable y recibe pasaje "Pasaje gratuito — sin butaca".
   - En compras ida/vuelta realizadas antes de los 5 años, conserva la gratuidad en ambos tramos por snapshot inmutable. Si el regreso se compra por separado tras cumplir 5 años, abona tarifa regular.
9. **Horarios estimados por parada**:
   - `operations.TripStop.scheduled_at` almacena el horario programado/estimado para cada parada del viaje.
   - Servicio transaccional `update_trip_schedules` con bloqueo `select_for_update()` sobre `Trip`, `Bus` y `TripStop`, accesible exclusivamente a `Administrador` antes de la salida.
   - Validación de secuencia estrictamente creciente (incluyendo cruces de medianoche) y prevención de solapamientos del mismo colectivo con otros viajes.
   - Formulario e interfaz en `/panel/operaciones/viajes/<int:trip_pk>/horarios/` con formatos estrictos `HH:MM`.
10. **Pasaje PDF oficial de dos talones y numeración serie AAA**:
    - Adaptación fiel a la plantilla oficial aprobada: 1 sola página (596 x 842 pt), dos talones ("Talón superior" para empresa/agencia y "Talón para el pasajero" inferior) con línea de corte punteada y logo oficial (`static/assets/scviajes-logo.png`).
    - **Supresión total de códigos visuales**: no se imprimen códigos QR ni códigos de barras visuales en ninguno de los dos talones.
    - Preservación interna de tokens de verificación (`verification_token_hash`) y descarga (`download_token_hash`), sin uso operativo actual: la validación de embarque en `/panel/embarques/` fue retirada (ver punto 12); `tickets.BoardingRecord` conserva los registros históricos.
    - Numeración persistente correlativa mediante el modelo `TicketSequence` con bloqueo `select_for_update()`, formato `AAA-000000001` en adelante para emisiones reales (`AAA-000000000` en plantilla vacía), replicada exactamente en ambos talones.
    - Snapshot inmutable de datos productivos, vendedor (omitido en web), medio de pago, horarios por parada y condiciones generales reproducidas en dos columnas.
11. **Retiro operativo de Caja y Efectivo**:
    - Endpoints operativos `/panel/caja/` y `/panel/caja/reporte-diario/` deshabilitados (404).
    - Nuevas operaciones de apertura, cierre, arqueo y cobros en efectivo rechazadas con `ValidationError`. Opciones de efectivo removidas de ventas.
    - Modelos históricos, registros contables previos y auditoría preservados para consultas autorizadas. Transferencias bancarias y emisión automática plenamente vigentes.
12. **Retiro de Control de Embarque**:
    - Acción "Validar embarque", rutas `/panel/embarques/` y servicios de validación/reversión deshabilitados (`ValidationError`).
    - Contadores e insignias de estado de embarque removidos de pantallas operativas de viaje.
    - Datos históricos preservados; inicio y finalización de viajes habilitados para administradores sin requerir embarque.
13. **Listado Provisional de Pasajeros en PDF**:
    - Descarga exclusiva para Administradores y Superusuarios desde el detalle del viaje (403 para vendedores).
    - Generación A4 apaisada con ReportLab, paginación "Página X de Y", cabeceras de tabla repetidas por página (`repeatRows=1`), y encabezado superior desde la página 2.
    - Inclusión estricta de reservas confirmadas (excluye retenidas, liberadas y vencidas), deduplicación de joins y vinculación de menores en falda con la butaca de la madre.
    - Rotulado de uso interno provisional (no homologado, no CNRT, sin códigos visuales ni estados de embarque).

14. **Integración Visual del Panel Rojo (UI/UX)**:
    - Adopción íntegra de la identidad visual roja en las interfaces internas reales: tipografía, jerarquía, superficies, colores de acento y estados de interacción (`panel.css` y `operations.css`).
    - Layout responsivo con drawer accesible para dispositivos móviles, soporte de cierre con tecla `Escape`, control de foco y breadcrumbs contextuales.
    - Cobertura completa de vistas: Dashboard, Inbox operativo, Reporte de ventas, Reconciliación de fulfillment, Transferencias pendientes, Clientes, Auditoría y Gestión de Staff.
    - Sin recursos externos (0 CDN), sin alteraciones de modelos, migraciones o lógica de negocio, con formularios y tokens CSRF preservados.

15. **Consolidación de Navegación Operativa y Pasajes de Menores Gratuitos**:
    - Retiro visual definitivo del acceso al resumen "Operaciones" en el menú lateral; la URL `/panel/operaciones/` redirige mediante HTTP 302 a `/panel/operaciones/viajes/`.
    - El menú lateral presenta únicamente "Viajes" (accesible para administradores y en modo sólo lectura/consulta para vendedores) y "Colectivos" (restringido a administración con `can_manage_operations`).
    - "Gestionar recorridos" vive integrado contextualmente dentro de "Viajes" con navegación de retorno ("← Volver a viajes"), sin enlace lateral independiente y preservando intactas sus rutas HTTP, formularios, creación, edición y versionado.
    - Emisión de pasajes para menores de 5 años gratuitos (`is_lap_infant = True`): persistencia en base de datos (`Ticket.companion_seat_reference`) de la butaca de referencia del acompañante asociado (`accompanied_by`) de forma específica por tramo (ida y vuelta con butacas distintas).
    - Migración `tickets.0008_ticket_companion_seat_reference` compatible con históricos preexistentes sin requerir backfill (`blank=True, default=""`), validada con pruebas sobre base temporal con registros históricos (`tickets.test_migration_historical`).
    - Ambos talones del pasaje PDF ("Talón superior" e inferior) imprimen la leyenda exacta: `Sin asiento propio. Butaca de referencia del acompañante: N`.
    - Muestras PDF sintéticas ida/vuelta renderizadas a PNG fuera del repo y revisadas/validadas visualmente por el coordinador confirmando ambos talones, leyenda completa y números correctos por tramo.
    - Sin registros adicionales en `SeatAssignment` para el menor; se mantiene validación estricta y advertencia en logs ante ausencia de butaca válida; recarga desde DB y reenvíos idempotentes no recalculan ni alteran PDFs.

---

## 2. Antecedentes históricos corregidos y superados

Se corrigen de forma expresa las siguientes afirmaciones obsoletas presentes en documentación o notas de trabajo anteriores:

| Concepto histórico obsoleto | Estado corregido y vigente | PR de integración |
|---|---|---|
| **Vencimiento de 24 horas en reservas manuales** (`SALES_MANUAL_HOLD_HOURS = 24`) | Las reservas manuales **no vencen automáticamente** (`expires_at = None`). Permanecen en retención hasta su cobro o liberación por el creador. | PR #45 (`2b47af4`) |
| **Umbral de menores de 2 años** | El umbral de gratuidad sin butaca aplica a **menores de cinco años** (0 a 4 años cumplidos; `MINOR_FREE_AGE_YEARS = 5`). | PR #48 (`69297a5`) |
| **Tarifas por tramo / par de paradas** | Las tarifas se definen **por salida y categoría** (`TripFare` único por `trip` y `seat_category`), con precio uniforme en todos los tramos permitidos. | PR #47 (`1c3e6f1`) |
| **Pasaje provisional con QR visual impreso** | La plantilla oficial aprobada **suprime QR y códigos de barras visuales**, reemplazando la numeración por la serie correlativa `AAA-000000001` y manteniendo los tokens criptográficos de forma interna. | PR #48 (`69297a5`) |
| **Revisión de transferencias por vendedores** | La revisión, aprobación y rechazo de transferencias está **restringida con exclusividad a Administradores activos**. *(Vigente para compras públicas; para ventas internas rige hoy la regla 5, pendiente de definición.)* | PR #45 (`2b47af4`) |
| **Reserva manual sin vencimiento para todo el panel** | Restringida a administradores y superusuarios; los vendedores operan ventas internas por transferencia con vencimiento. | `d2aa716` |
| **Caja diaria, cobro en efectivo y validación de embarque** | Retirados operativamente; históricos preservados (ver puntos 11 y 12). | `804ee14` y siguientes |

---

## 3. Decisiones comerciales pendientes de Sandro

Las siguientes definiciones comerciales no han sido autorizadas y permanecen abiertas, manteniéndose desacopladas en el código mediante configuración o comportamientos provisionales:

1. **Fecha comercial de compra del menor**: definir si la edad al comprar se evalúa al instante de creación de la reserva (`Booking.created_at`) o al momento de confirmación del pago (`Booking.confirmed_at`).
2. **Seguro sobre menores gratuitos sin butaca**: confirmar si debe liquidarse prima de seguro obligatoria, monto a cobrar y leyenda fiscal/legal a imprimir en el pasaje.
3. **Validación de vínculo e identidad (RENAPER)**: confirmar si se integrará validación oficial por servicio externo o si se continuará con acreditación documental física en boletería/embarque.
4. **Autorización previa de butacas prioritarias**: reglamentar el procedimiento, rol autorizante y tarifa aplicable para asignar butacas 51 y 54 antes de las 00:00 de dos días previos a la salida.
5. **Políticas de cambio y cancelación**: definir porcentajes de penalización, ventanas horarias de tolerancia y régimen de reembolsos/créditos.
6. **Datos fijos de transporte y legales**: confirmación de CUIT, razón social, Ingresos Brutos, inicio de actividades, aseguradora, póliza, direcciones físicas exactas de paradas y WhatsApp oficial de atención.
7. **Pasarelas de pago online**: ~~Mercado Pago QR~~ reemplazado por **Mercado Pago Checkout Pro** (implementado, deshabilitado por defecto, revisado contra documentación oficial vigente — commit `88df44f`; validado con mocks; falta prueba sandbox con credenciales de prueba y túnel HTTPS). Payway sigue diferido.
8. ~~**Migración de datos AppSheet / Google Sheets**~~: **CANCELADA** — la aplicación usa su propia base de datos y no depende de recibir exports (ver `docs/DECISIONS.md` §4).

---

## 4. Matriz de evidencia de CI por SHA

A continuación se detalla la evidencia técnica verificable de las entregas integradas, contrastando el entorno de validación (pruebas sintéticas locales, workflows CI en GitHub Actions y validaciones pendientes de staging real):

| PR / Entrega | SHA integrado | Cobertura en pruebas sintéticas locales (SQLite efímero) | Workflows CI automatizados (GitHub Actions) | Validación en Staging Real (PostgreSQL / Proveedores) |
|---|---|---|---|---|
| **PR #43** (Auditoría Release Candidate) | `00b9660` (base `34389a7`) | 550 tests OK, 7 skipped (PostgreSQL). Migraciones y checks limpios. | Quality Gate, Browser E2E, Restore Drill en verde para base RC. | Marcada como histórica. Requiere revalidación integral en staging. |
| **PR #44** (Inventario datos heredados) | `3398bb5` | Toolkit read-only verificado con fixtures sintéticos (CSV, TSV, XLSX). Sin conexión a Google. | Quality Gate verificado. | Pendiente de ejecución con exports reales de AppSheet/Sheets. |
| **PR #45** (Reglas Sandro: reservas, pagos, comisiones) | `2b47af4` | 560 tests OK, 7 skipped. Migraciones `sales.0003` y `panel.0003` aplicadas en base efímera. | `sales-postgres.yml` ejecutó concurrencia de pagos y bloqueos en PostgreSQL 16. | Pendiente: provisioning real de cuenta propietaria de Sandro. |
| **PR #46** (Rutas Güemes y menores sin butaca) | `16a6808` | 567 tests OK, 7 skipped. Migraciones `sales.0004` y `tickets.0006` en base efímera. | Quality Gate, Sales Postgres y Tickets Postgres en verde. | Pendiente: ejecución de comando `setup_initial_operations` en base de staging. |
| **PR #47** (Tarifas salida y prioritarias 51/54) | `1c3e6f1` | 586 tests OK, 7 skipped. Migración `operations.0003` con validación de conflictos históricos. | Concurrencia transaccional verificada en CI de base de datos. | Pendiente: validación de datos tarifarios reales de producción. |
| **PR #48** (Menores a 5 años, horarios, pasaje AAA) | `69297a5` | 172 tests afectados (169 OK, 3 PostgreSQL skipped). 71 tests de `tickets` al 100% OK. Migración `tickets.0007`. | Workflows de repositorio completados sobre rama integrada. | Pendiente: generación de pasajes con storage S3 real y envío SMTP productivo. |
| **Etapa Permisos y Navegación** | Rama `Joel010999/scviajes-permisos-navegacion-20261104` | Corrección y alineación de validación tras suite completa (13 fallos y 2 errores resueltos en pruebas y vistas de ventas internas/navegación). Migración `panel.0004`. | Verificación local: Ruff, Django check y makemigrations dry-run en verde. Tests afectados OK. | Pendiente: validación en base PostgreSQL real de staging sin aplicar migraciones destructivas. |
| **Integración Panel Rojo (UI/UX)** | Rama `Joel010999/panel-rojo-integracion` | 692 tests OK, 6 skipped (PostgreSQL). Ruff (0 errores), Django check (0 problemas), makemigrations dry-run (sin cambios). 23/23 pruebas CDP en Chrome headless (móvil/desktop, contraste, teclado, overflow 0px). | 18 capturas PNG generadas fuera del repo. Cero dependencias externas (sin CDN). Formularios y tokens CSRF intactos. | Pendiente: despliegue e inspección visual en ambiente staging con PostgreSQL. |
| **Gráficos de Ventas y Comisiones** | Rama `Joel010999/panel-graficos-comisiones-20261106` | Validación final: Found 707, Ran 702, OK (skipped=6): 696 pruebas aprobadas, 6 omitidas (PostgreSQL). Ruff (0 errores), Django check (0 problemas), makemigrations dry-run (sin cambios). Validación CDP en Chrome headless (móvil/desktop, 0 errores JS, overflow 0px, 6 gráficos con porcentajes). | 5 capturas PNG generadas fuera del repo. Importes retirados del reporte y preservados en Dashboard. | Pendiente: validación en base PostgreSQL real de staging. |
| **Pendientes Navegación y Menores** | Rama `Joel010999/pendientes-nav-menor-20261107` | Found 711, Ran 706 tests OK, 6 skipped (PostgreSQL). Ruff (0 errores), Django check (0 problemas), makemigrations dry-run (sin cambios), git diff --check (0 errores). Logs guardados fuera del repo en `full_test_suite_output.log`. Muestras PDF sintéticas ida/vuelta renderizadas fuera del repo y revisadas/aprobadas por el coordinador. | Migración `tickets.0008` probada sobre base temporal con registros históricos. Reporte técnico sobre `concurrent_cash_payments_serialization` ratificado. | Pendiente: validación en base PostgreSQL real de staging. |
| **Corrección Fallos CI PR #50** | Rama `Joel010999/pendientes-nav-menor-20261107` | Tests afectados al 100 % OK (`sales.test_internal_transfer` con 32 tests, `panel.test_reservations` con 36 tests, `panel.test_integration_ventas_recorridos` con 12 tests, `core.test_restore_drill` con 8 tests). Django check (0 problemas), git diff --check (0 errores). | Contrato de butacas explícito usando `seat_numbers` en `panel/reservation_views.py`, resolución inequívoca número vs PK y validación de colectivo en `sales/internal_transfer.py` y `panel/reservation_services.py`, regresiones de colisión número/PK cubiertas para ventas pendientes, inmediatas y flujo HTTP. Evidencia verificable de versiones efectivas de `pg_dump`, `pg_restore` y servidor PostgreSQL 16 en `.github/workflows/restore-drill.yml`. | Pendiente: re-ejecución de workflows en GitHub Actions tras actualización del PR. |
| **Botón descarga pasajes en panel** | `1bc0d4d` (PR #50, rama `Joel010999/pendientes-nav-menor-20261107`) | `panel.test_reservations`: 44/44 OK (incluye regresión de enlace solo con pasajes ISSUED y alcance por vendedor). git diff --check limpio. | Verificación E2E local con Playwright: admin descarga desde el botón real, vendedor titular descarga el suyo y otro vendedor recibe 404 en detalle y en URL directa. | Validado en CI por los workflows de PostgreSQL sobre el SHA. |
| **Corrección expiración de reservas sin pago** | `5642156` (PR #50) | `operations.test_maintenance_command`: 5/5 OK, incluida regresión `ExpireTaskWithoutPaymentTests` (fallaba antes del fix). Suite `operations` 66 OK, `sales`+`payments` 167 OK. | Evidencia CI en `5642156`: workflows **Sales PostgreSQL tests**, **Tickets PostgreSQL CI**, **PostgreSQL restore drill**, **Browser acceptance E2E** y **Quality gate** todos en verde. **La evidencia CI sobre PostgreSQL no sustituye la validación del despliegue real en staging** (siguen pendientes los ítems de la sección siguiente). | Verificado en CI; pendiente validación en despliegue real de staging. |

### Verificaciones E2E locales posteriores al PR #50 (localhost, SQLite de prueba, datos ficticios)

Ejecutadas con Playwright/HTTP sobre `http://127.0.0.1:8000` sin tocar producción. Evidencia (logs, capturas y PDFs) conservada fuera del repositorio.

1. **Compra pública de punta a punta**: búsqueda desde portada, retención de butaca como invitado, verificación de ruta/horarios/categoría/importe, butaca bloqueada en otra sesión, transferencia con comprobante ficticio, aprobación administrativa, emisión de pasaje y descarga del PDF con datos consistentes (8/8 verificaciones).
2. **Vencimiento automático**: reserva pública retenida, `expires_at` adelantado solo en la SQLite de prueba, ejecución de `run_operational_maintenance --task expire` → procesa la reserva, la deja `EXPIRED`, libera la asignación (`RELEASED`) y la butaca vuelve a ser elegible en otra sesión. Antes del fix `5642156` el comando reportaba `candidates: 0` para reservas HELD sin registros `Payment` (subquery `NULL` excluida por `NOT (NULL IN (...))`); la expiración sólo ocurría de forma oportunista dentro de `create_booking`.
3. **Permiso de reserva manual sin vencimiento**: verificado por UI y POST directo forjado como vendedor. La UI del vendedor sólo ofrece venta por transferencia (`sale_type` forzado a `transfer`, incluido `?sale_type=manual` por GET); un POST forjado con `sale_type=manual` es coaccionado al flujo de transferencia y produce una reserva `HELD` **con vencimiento**, nunca una reserva sin vencimiento (doble control: coacción en `panel/reservation_views.py` y `require_can_create_manual_unexpired_reservation` en `panel/reservation_services.py`, ya cubierto por `sales.test_internal_transfer`). El administrador sí dispone en UI de "Nueva reserva manual" sin vencimiento. No se encontró vulnerabilidad.
4. **Descarga de pasajes emitidos desde el panel**: botón "Descargar PDF" por pasaje con pasajero, tramo y butaca; alcance verificado (admin todo, vendedor sólo sus ventas, otro vendedor recibe 404).
5. **Mercado Pago Checkout Pro (rama `Joel010999/mercadopago-checkout-pro`)**: implementado (`b393348`) y **revisado contra la documentación oficial vigente** (`88df44f`): identificación del intento de pago por `external_payment_id` (notificación de otro intento → `IGNORED_OTHER_PAYMENT`), topics ajenos a pagos ignorados (`IGNORED_TOPIC`), preferencia con `expires`/`expiration_date_to` igual a la retención de butaca y `payer.email`. Validado **únicamente con mocks** — 30 tests en `payments/test_mercadopago.py` cubren preferencia server-side, aprobado/pendiente/rechazado, firma inválida/ausente/vencida, importe/moneda/collector/referencia incorrectos, notificación duplicada y fuera de orden, notificación de intento ajeno, aprobación con reserva vencida (conflicto registrado, sin reasignación de butaca ni pasaje) y deduplicación de fulfillment. **Sandbox E2E pendiente**: requiere credenciales de prueba y túnel HTTPS para webhooks (ver `docs/MERCADO_PAGO.md`). No declarado listo para producción.

### Aceptación funcional local (19/11/2026 — localhost, SQLite de prueba, Playwright, datos ficticios)

Sesiones separadas por rol (administrador, vendedor, segundo vendedor, invitado público, móvil iPhone 13). Mercado Pago deshabilitado; datos bancarios ficticios sólo en el lanzador local. Evidencia fuera del repositorio en `C:\Users\joelj\scviajes-e2e\acept\` (scripts `a_`…`e_*.py`, logs `run_*.log`, `shots/`, `pdfs/`). **Es evidencia SQLite local: no sustituye PostgreSQL ni staging.**

#### Implementado y verificado

| Flujo | Resultado | Evidencia |
|---|---|---|
| Recorrido versionado desde "Nueva versión" (renombrado), viaje nuevo sobre v2, el viaje existente conserva v1 | OK (tras fix `2847344`) | `run_a2.log` A1–A2 |
| Horarios por parada: edición válida y rechazo de secuencia no creciente | OK | A4 |
| Tarifas Cama/Semicama por salida; duplicado por categoría rechazado; viaje publicado en búsqueda pública con su tarifa; viaje sin tarifas no se ofrece | OK | A3, A5, `f_nofare.py` |
| Vendedor: 403 por URL y POST en alta de viaje, tarifas, horarios, recorridos e inicio de viaje; sin efecto en DB | OK | A6 |
| Disponibilidad: butaca retenida bloqueada en tramo intermedio del mismo viaje; libre en otro viaje | OK | `run_b.log` B2 |
| Compra pública por transferencia: rechazo con motivo (intento preservado, reserva retenida, cliente ve motivo y "Reintentar pago"), reintento, aprobación, un único pasaje, PDF con pasajero/fecha/hora/butaca/importe/código | OK | B1–B4, `pdfs/B4-publico-reintento.pdf` |
| Fulfillment fallido recuperado con "Reintentar pasajes" del panel; segundo reintento no duplica | OK | `b_retry.py`, `pdfs/B5-recuperado-panel.pdf` |
| Reserva manual sin vencimiento sólo administrador; vendedor obtiene venta interna con vencimiento y comprobante | OK | `run_c.log` C1–C2 |
| Mantenimiento real `run_operational_maintenance --task expire` no vence la manual sin vencimiento | OK | C5 |
| Liberación sólo por el creador (otro usuario 404); butaca vuelve a estar disponible | OK | C6 |
| Aislamiento entre vendedores: detalle de reserva/pago/comprobante, lista de pendientes, POST aprobar/liberar, reproceso de fulfillment ajeno | OK (404 / sin efecto) | C3, D4c |
| Comisiones del dashboard por rol = cálculo SQL independiente (ARS 5.000 por asignación confirmada positiva de venta interna) | OK | `run_d.log` D1 |
| Reporte CSV del vendedor acotado aun forzando `?vendedor=<otro>`; admin filtra por vendedor | OK | D2 |
| Listado provisional de pasajeros y manifiesto: 403 para vendedor, sin botón; admin descarga; incluye confirmados y menor sin asiento, excluye no confirmados; rótulo provisional no CNRT | OK | D3, `pdfs/D3-listado-viaje*.pdf` |
| Secciones administrativas (usuarios, auditoría, clientes, colectivos, finalizar viaje, cambio de rol) bloqueadas al vendedor por URL y POST | OK | D4 |
| Ida/vuelta con menor sin asiento: frase exacta en ambos talones, butaca 11 (ida) y 25 (vuelta) | OK (re-verificada sobre PDFs del 14/11) | `scviajes-e2e/pdfs/B-*-menor-*.pdf` |
| Móvil: portada, resultados, checkout, dashboard, venta interna y listados sin desborde horizontal; validación de formulario vacío; drawer abre, cierra con Escape y devuelve el foco | OK | `run_e.log` E1–E4 |
| Caja, efectivo y embarque retirados: rutas 404, sin opción de efectivo ni validación de embarque, fuera del menú | OK | E5 |
| Antiabuso de checkout (3 reservas / 15 min por IP) activo | OK (observado; se amplió sólo en el lanzador local) | `django_local.log` |

#### Defectos encontrados y corregidos (`2847344`)

1. **Nueva versión de recorrido con cambio de nombre era rechazada** ("no se puede modificar directamente") porque el formulario validaba la instancia en uso. Ahora se valida sin ella y el servicio versiona. Regresión `test_new_version_with_renamed_route_is_created` (falla sin el fix).
2. **`/panel/operaciones/recorridos/<pk>/` devolvía 500** (plantilla `route_detail.html` inexistente). Plantilla agregada; regresión `test_route_detail_renders_for_seller_and_admin`.
3. **Fallo de emisión de pasajes sin rastro en logs**: el error quedaba sólo como texto genérico en el trabajo. En `2847344` se agregó `logger.exception`; en la revisión posterior se reemplazó por un registro `ERROR` con `fulfillment`, `reserva` (pk interno), `error_type` y `origen` (archivo:línea:función del proyecto), **sin** mensaje de la excepción ni traceback, porque los formatters del proyecto descartan `exc_info` y el mensaje puede contener correos, documentos o URLs firmadas. Regresión `test_issue_failure_is_logged_without_personal_data` (formatter JSON real).

Hallazgo de configuración (no defecto del código): `PUBLIC_BASE_URL` con `http://` hace fallar la emisión (el código exige HTTPS por diseño). El lanzador local usa el valor por defecto de DEBUG (`https://scviajes.test`).

#### Validación de transferencias sobre PostgreSQL 16 (21/11/2026)

- **Entorno**: en la notebook no hay PostgreSQL disponible (sin servicio, binarios, Docker ni WSL); no se instaló ninguno. La validación PostgreSQL se ejecuta **sólo en GitHub Actions** (`postgres:16`, base efímera `test_*`), no hay aceptación visual local sobre PostgreSQL.
- **Pruebas nuevas** `payments/test_transfer_postgres.py` (paso explícito en *Sales PostgreSQL tests*): motor PostgreSQL 16; comprobante obligatorio y confirmación por vendedor titular; aislamiento entre vendedores y separación ONLINE/MANUAL; fallo de storage del comprobante sin confirmar el cobro; confirmación repetida y concurrente (titular + administrador, dos administradores en compra web), ventas inmediatas concurrentes por la misma butaca y reintentos concurrentes de fulfillment sin duplicar pagos, reservas confirmadas, asignaciones ni pasajes; emisión fallida y recuperada.
- **Defecto demostrado y corregido**: si la operación fallaba después de guardar el comprobante (confirmación, auditoría o notificación), la base se revertía pero el archivo quedaba huérfano en el storage privado. Ahora `discard_vouchers_on_error` elimina los comprobantes guardados en el bloque fallido (registro interno, carga pública, venta interna y venta inmediata), funciona con filesystem o S3 y no registra nombres ni mensajes. Tres regresiones fallan sin la corrección.
- **Decisión de ventas internas aplicada (22/11/2026)**: la diferencia real era que la carga del comprobante por el titular dejaba el pago en revisión (y la casilla "pago verificado" condicionaba la confirmación). Ahora la carga del titular confirma el cobro y dispara la emisión en una sola transacción, sin casilla ni segunda aprobación; si la confirmación falla, se revierte la carga y se elimina el comprobante. Las compras web siguen en revisión hasta aprobación de administración.
- **Alcance de la evidencia**: PostgreSQL 16 está validado en CI. Eso es distinto de una aceptación visual local sobre PostgreSQL (no realizada) y del futuro despliegue en staging (pendiente); no se registra "PostgreSQL local" como pendiente propio.
- **Prueba visual de gráficos reparada (22/11/2026)**: `test_sales_report_browser_visual_and_overflow` tenía rutas fijas a `C:\Users\JOEL\...` y fallaba con `PermissionError` en cualquier otra máquina. Ahora usa `tempfile` (capturas en el temporal del sistema, perfil de Chrome descartable eliminado al cerrar), puerto CDP libre, detección portable del binario de Chrome/Chromium (env `CHROME_PATH`, `google-chrome`, `chromium`, ruta de Windows) y cierre garantizado del navegador, el WebSocket y la sesión aunque falle una aserción. Verificaciones conservadas sin cambios: 6 gráficos de admin / 5 de vendedor sin el gráfico de vendedores, aislamiento por rol, 0 px de desborde horizontal en desktop y móvil, 0 errores JS, y las capturas PNG fuera del repositorio. Se ejecuta explícitamente en el workflow *Browser acceptance E2E* (ubuntu-latest, motor **SQLite** vía `DATABASE_URL: sqlite:///e2e.sqlite3`, no PostgreSQL) instalando `requests==2.32.4` y `aiohttp==3.12.15` sólo como dependencias de test; `requirements.txt` de producción no se modificó. En las suites completas de los workflows PostgreSQL sigue omitiéndose porque allí no están esas dependencias ni es el objetivo de ese entorno. Evidencia: corre y pasa con Chrome real en Windows (local) y en CI con `google-chrome` de ubuntu-latest (`Ran 1 test ... OK` en *Browser acceptance E2E*, SHA `cb9f786`).

#### Defectos o discrepancias por resolver (no modificados en esta tarea)

1. ~~**Aprobación de transferencias de ventas internas**~~: **resuelto** por decisión confirmada (cuenta personal del vendedor, comprobante obligatorio, confirmación por el titular sin segunda aprobación). Compras web: sólo administración.
2. **Butacas prioritarias 51/54**: el código libera automáticamente a las 00:00 AR dos días antes; el acuerdo vigente es solicitud por correo hasta 48 h antes sin liberación automática acordada (`docs/CONTEXTO_COORDINACION.md` §2). Sin cambios hasta la definición.
3. ~~**Reservas manuales sin vencimiento**~~: **reconciliado** en `PROJECT_SPEC.md`, `DECISIONS.md` y §1.1 (restricción por rol confirmada, desde `d2aa716`).
4. ~~**Documentación de embarque y efectivo desactualizada**~~: **reconciliado** (§1.10, `PROJECT_SPEC.md` y `DECISIONS.md` §2.4).
6. **La emisión exige `PUBLIC_BASE_URL` HTTPS aunque la URL de verificación no se imprime**: `build_verification_url` se calcula para cada pasaje (sólo se guarda el hash del token) y falla con `http://`. `/tickets/verify/` **se conserva** como consulta de solo lectura (decisión); queda pendiente sólo si en el futuro la URL se imprime en el pasaje. La exigencia HTTPS no se cambió.
5. El checkout móvil con 60 butacas resulta muy extenso (~17.000 px de alto); usable, mejora de UX opcional.

#### Enlaces en pasajes y correos (verificado 19/11/2026)

| Salida | Enlace | localhost | Dominio final |
|---|---|---|---|
| PDF de pasaje y listado provisional | Ninguno (sin QR, anotaciones ni URLs impresas) | n/a | n/a |
| Correo de pasajes | Sin enlaces; PDFs adjuntos | Adjuntos verificados por consola | Requiere SMTP real |
| Notificaciones transaccionales | Sin enlaces (texto + referencia de reserva) | n/a | Requiere SMTP real |
| Restablecer contraseña de cliente | `request.build_absolute_uri` (host de la petición) | **Navegable** (`http://127.0.0.1:8000/cliente/recuperar-clave/…`, 200) | Depende de `ALLOWED_HOSTS` y `SECURE_PROXY_SSL_HEADER` (ya configurado) detrás del proxy |
| Invitación de personal | `public_url()` → `PUBLIC_BASE_URL` | Apunta a `https://scviajes.test` (**no navegable**); la misma ruta en localhost responde 200 | Requiere `PUBLIC_BASE_URL` del dominio final |
| URL de verificación de pasaje | `build_verification_url` → `TICKETS_VERIFICATION_BASE_URL`/`PUBLIC_BASE_URL` | Se calcula pero no se imprime ni se envía; sólo se guarda el hash del token. El endpoint `/tickets/verify/` se conserva (solo lectura) | Requiere el dominio final si se publica la URL |
| Descarga de pasajes desde la web/panel | Rutas relativas (`/tickets/guest/…`, `/tickets/download/…`) | Navegables (verificado B4, B5) | Sin dependencia del dominio |

`PUBLIC_BASE_URL=https://scviajes.test` sólo habilita la emisión local; no demuestra la navegabilidad de enlaces absolutos (invitaciones), que requieren el dominio final.

#### Decisiones pendientes de Sandro

- Credenciales de prueba de Mercado Pago (cuenta dueña de la aplicación) para completar el sandbox Checkout Pro.
- Butacas prioritarias 51/54 (procedimiento, horario límite, liberación).
- Menores: fecha de referencia de edad, seguro y documentación/RENAPER.
- Cambios, cancelaciones y devoluciones.
- Datos reales fiscales, bancarios, aseguradora y paradas; Payway.

#### Preparación de publicación

- Staging sobre PostgreSQL dedicado: migraciones (incluida `payments.0003`), concurrencia y restricciones reales.
- Storage privado S3, SMTP real, `PUBLIC_BASE_URL` HTTPS y datos bancarios reales (los checks `tickets.E008/E019/E020` bloquean el arranque sin ellos).
- `collectstatic` en el despliegue (con `DEBUG=False` el manifest de WhiteNoise es obligatorio).
- Drill de backup/restauración sobre dump real.
- **Disparo de CI**: los workflows sólo corren en `pull_request` hacia `main` y en `push` a `main`/`feature/**`. Un PR con base en la rama del PR #50 **no dispara checks**; publicar la rama como `feature/…` o validar sobre el PR hacia `main`.
- Sandbox Mercado Pago con túnel o dominio HTTPS y webhook firmado; luego decisión explícita para habilitarlo.

### Validaciones pendientes antes de producción
1. **Validación de despliegue real sobre PostgreSQL**: los tests de concurrencia y restricciones avanzadas (omitidos en SQLite) ya corren en GitHub Actions sobre PostgreSQL 16 — verificados en verde para `5642156` en los workflows *Sales PostgreSQL tests*, *Tickets PostgreSQL CI* y *PostgreSQL restore drill*. Queda pendiente la **validación del despliegue real en staging** (instancia dedicada, migraciones, concurrencia bajo carga real), que la evidencia CI no sustituye.
2. **Validación en Staging**: desplegar la aplicación en Railway Staging con `PRIVATE_STORAGE_BACKEND=s3` y backend SMTP configurado para validar la descarga autenticada de pasajes y el envío transaccional de correos.
3. **Inspección visual independiente de pasajes**: realizar la validación visual de los PDFs renderizados con un visor PDF independiente y verificar la lectura manual de los datos impresos.
4. **Drill de backup y restauración**: ejecutar el comando `verify_database_backup` sobre un dump PostgreSQL real en el entorno de staging.
