# Estado del proyecto SC Viajes

Fecha de consolidación: 27 de octubre de 2026
Commit base integrado: `69297a5` (PR #48: *Actualizar gratuidad de menores, horarios por parada y pasaje PDF*)
Estado global: **Desarrollo e integraciones comerciales de Sandro completados en el repositorio; validaciones de staging real y decisiones comerciales pendientes de confirmación.**
> [!IMPORTANT]
> **No se declara producción aprobada.** El sistema cuenta con cobertura completa en pruebas sintéticas y CI automatizado, pero requiere validación en staging real con base PostgreSQL dedicada, configuración de proveedores externos (Railway, S3, SMTP, OAuth) y la resolución de definiciones comerciales abiertas con Sandro.

---

## 1. Resumen ejecutivo de funcionalidades vigentes comprobadas

Tras la integración sucesiva de los PRs #44, #45, #46, #47 y #48 sobre la base auditada, el sistema refleja el siguiente estado vigente:

1. **Reservas manuales sin expiración automática**:
   - Las reservas manuales creadas en el panel por vendedores tienen `expires_at = None`. No vencen por tiempo y mantienen la butaca retenida hasta su cobro o su liberación manual.
   - Las reservas online conservan su hold temporal de 15 minutos (`SALES_ONLINE_HOLD_MINUTES = 15`).
   - El proceso de mantenimiento operativo no expira reservas manuales indefinidas ni reservas con pagos rechazados.
2. **Alcance por vendedor**:
   - El filtro por vendedor autenticado se aplica en el QuerySet base (`sales.Booking.objects.filter(...)` o restricción por actor) antes de cualquier consulta, detalle, filtrado, paginación, dashboard o exportación CSV.
   - Los vendedores sólo visualizan y gestionan sus propias ventas y reservas.
   - La acción de liberación de una reserva manual está permitida exclusivamente al usuario vendedor que la creó.
3. **Revisión administrativa de transferencias**:
   - Exclusiva para usuarios pertenecientes al grupo `Administrador` activos (o superusuarios). Los vendedores no tienen permisos para revisar o aprobar transferencias.
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
    - Preservación interna de tokens de verificación (`verification_token_hash`) y descarga (`download_token_hash`), manteniendo operativa la validación de embarque interna en `/panel/embarques/` (`tickets.BoardingRecord`).
    - Numeración persistente correlativa mediante el modelo `TicketSequence` con bloqueo `select_for_update()`, formato `AAA-000000001` en adelante para emisiones reales (`AAA-000000000` en plantilla vacía), replicada exactamente en ambos talones.
    - Snapshot inmutable de datos productivos, vendedor (omitido en web), medio de pago, horarios por parada y condiciones generales reproducidas en dos columnas.

---

## 2. Antecedentes históricos corregidos y superados

Se corrigen de forma expresa las siguientes afirmaciones obsoletas presentes en documentación o notas de trabajo anteriores:

| Concepto histórico obsoleto | Estado corregido y vigente | PR de integración |
|---|---|---|
| **Vencimiento de 24 horas en reservas manuales** (`SALES_MANUAL_HOLD_HOURS = 24`) | Las reservas manuales **no vencen automáticamente** (`expires_at = None`). Permanecen en retención hasta su cobro o liberación por el creador. | PR #45 (`2b47af4`) |
| **Umbral de menores de 2 años** | El umbral de gratuidad sin butaca aplica a **menores de cinco años** (0 a 4 años cumplidos; `MINOR_FREE_AGE_YEARS = 5`). | PR #48 (`69297a5`) |
| **Tarifas por tramo / par de paradas** | Las tarifas se definen **por salida y categoría** (`TripFare` único por `trip` y `seat_category`), con precio uniforme en todos los tramos permitidos. | PR #47 (`1c3e6f1`) |
| **Pasaje provisional con QR visual impreso** | La plantilla oficial aprobada **suprime QR y códigos de barras visuales**, reemplazando la numeración por la serie correlativa `AAA-000000001` y manteniendo los tokens criptográficos de forma interna. | PR #48 (`69297a5`) |
| **Revisión de transferencias por vendedores** | La revisión, aprobación y rechazo de transferencias está **restringida con exclusividad a Administradores activos**. | PR #45 (`2b47af4`) |

---

## 3. Decisiones comerciales pendientes de Sandro

Las siguientes definiciones comerciales no han sido autorizadas y permanecen abiertas, manteniéndose desacopladas en el código mediante configuración o comportamientos provisionales:

1. **Fecha comercial de compra del menor**: definir si la edad al comprar se evalúa al instante de creación de la reserva (`Booking.created_at`) o al momento de confirmación del pago (`Booking.confirmed_at`).
2. **Seguro sobre menores gratuitos sin butaca**: confirmar si debe liquidarse prima de seguro obligatoria, monto a cobrar y leyenda fiscal/legal a imprimir en el pasaje.
3. **Validación de vínculo e identidad (RENAPER)**: confirmar si se integrará validación oficial por servicio externo o si se continuará con acreditación documental física en boletería/embarque.
4. **Autorización previa de butacas prioritarias**: reglamentar el procedimiento, rol autorizante y tarifa aplicable para asignar butacas 51 y 54 antes de las 00:00 de dos días previos a la salida.
5. **Políticas de cambio y cancelación**: definir porcentajes de penalización, ventanas horarias de tolerancia y régimen de reembolsos/créditos.
6. **Datos fijos de transporte y legales**: confirmación de CUIT, razón social, Ingresos Brutos, inicio de actividades, aseguradora, póliza, direcciones físicas exactas de paradas y WhatsApp oficial de atención.
7. **Pasarelas de pago online**: integración real de Mercado Pago QR y tarjetas por Payway (diferidas hasta después de la migración de datos).
8. **Migración de datos AppSheet / Google Sheets**: estructuración de hojas y ejecución controlada en staging previa a la salida a producción.

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

### Validaciones pendientes antes de producción
1. **Ejecución de suite PostgreSQL completa**: correr los 7 tests de concurrencia y restricciones avanzadas omitidos en SQLite sobre una instancia real de PostgreSQL 16.
2. **Validación en Staging**: desplegar la aplicación en Railway Staging con `PRIVATE_STORAGE_BACKEND=s3` y backend SMTP configurado para validar la descarga autenticada de pasajes y el envío transaccional de correos.
3. **Inspección visual independiente de pasajes**: realizar la validación visual de los PDFs renderizados con un visor PDF independiente y verificar la lectura manual de los datos impresos.
4. **Drill de backup y restauración**: ejecutar el comando `verify_database_backup` sobre un dump PostgreSQL real en el entorno de staging.
