# Registro de decisiones de SC Viajes

Este documento registra las decisiones confirmadas, los antecedentes históricos superados y las definiciones comerciales pendientes con Sandro, consolidado tras la integración del PR #48.

---

## 1. Decisiones confirmadas e integradas

| Tema | Decisión confirmada | Referencia de especificación |
|---|---|---|
| **Recorridos y tramos** | Recorrido Córdoba ↔ Jujuy con parada Güemes (`GUE`, Salta) en ambos sentidos. Prohibición estricta de tramos dentro de una misma provincia (`Stop.province`). Sin reventa de una butaca en tramos intermedios para el MVP. | [Recorridos](PROJECT_SPEC.md#recorridos-y-paradas) |
| **Tarifas por salida** | Tarifa definida por salida (`Trip`) y categoría (`CAMA` y `SEMI_CAMA`), uniforme en todos los tramos permitidos. Edición exclusiva para Administradores antes de la partida. Inmutabilidad de reservas existentes. | [Tarifas por salida](PROJECT_SPEC.md#tarifas-por-salida) |
| **Butacas prioritarias 51 y 54** | Reservadas para personas con discapacidad por solicitud por correo hasta 48 horas antes de la salida; a las 48 horas exactas previas (`departure_at` − 48 h) se liberan automáticamente para venta general si están disponibles. Sin circuito de autorización en la aplicación ni cancelación de ocupaciones previas. Verificación en tiempo real sin worker. *Antecedente sustituido: liberación a las 00:00 AR de dos días calendario antes.* | [Butacas y prioridades](PROJECT_SPEC.md#butacas-colectivos-y-prioridades) |
| **Menores de cinco años** | Gratuidad sin butaca para menores estrictamente menores a 5 años (0 a 4 años cumplidos) a la fecha de compra. Acompañante obligatorio, importe $0,00 y sin comisión. En compras ida/vuelta conserva gratuidad en ambos tramos. | [Menores](PROJECT_SPEC.md#compra-y-pasajeros) |
| **Horarios por parada** | Cada parada almacena fecha/hora programada/estimada (`TripStop.scheduled_at`). Carga y edición por Administradores para viajes no iniciados, con validación de secuencia temporal creciente y sin superposiciones. | [Horarios por parada](PROJECT_SPEC.md#horarios-estimados-por-parada) |
| **Reservas manuales** | Reserva rápida sin vencimiento automático (`expires_at = None`) **sólo para administradores y superusuarios**. Los vendedores registran ventas internas por transferencia con vencimiento `min(medianoche AR, salida inicial − 1 h)` (desde `d2aa716`). Butaca retenida hasta cobro o liberación manual. Liberación restringida exclusivamente al creador de la reserva. | [Disponibilidad](PROJECT_SPEC.md#disponibilidad-vencimientos-y-cortes) |
| **Alcance por vendedor** | Queryset base acotado por vendedor: el vendedor únicamente visualiza, filtra y descarga sus propias operaciones y balances. Administradores acceden a la visión global. | [Alcance vendedor](PROJECT_SPEC.md#disponibilidad-vencimientos-y-cortes) |
| **Transferencias bancarias** | **Compras públicas (ONLINE)**: revisión exclusiva de administradores y superusuarios (decisión confirmada, ver §4). **Ventas internas (MANUAL)**: cobro en la cuenta personal del vendedor; la carga del comprobante obligatorio por el vendedor titular confirma el cobro y dispara la emisión, sin segunda aprobación (decisión confirmada, ver §4). Rechazos preservan el intento fallido y retienen la butaca para nuevo comprobante. Aprobación terminal con fulfillment durable. | [Pagos](PROJECT_SPEC.md#pagos-transferencias-y-comisiones) |
| **Balance y comisiones** | Comisión fija de `Decimal("5000.00")` por cada asignación confirmada con precio positivo (por pasajero y tramo). Muestra ventas brutas, unidades comisionables, comisión y neto operativo. | [Balance](PROJECT_SPEC.md#pagos-transferencias-y-comisiones) |
| **Pasaje PDF oficial y serie AAA** | Plantilla de 1 página con dos talones (empresa y pasajero) con logo oficial. Eliminación total de QR y códigos de barras visuales en el PDF. Numeración secuencial persistente `AAA-000000001` con `TicketSequence`. | [Pasajes](PROJECT_SPEC.md#pasajes-y-emisión) |
| **Validación de embarque (superada)** | Retirada operativamente de interfaces y endpoints; operaciones programáticas lanzan ValidationError. Registros y auditoría históricos preservados; viajes inician y finalizan sin exigir embarque. | [Retiro embarque](STATUS_CAJA_EMBARQUE_LISTADO_PDF_20261105.md) |
| **Cortes de venta** | Corte de venta online a 60 minutos de la subida. Corte de venta manual a 30 minutos de la subida; rechazo a 29 minutos. Bloqueo total tras inicio del viaje. | [Cortes](PROJECT_SPEC.md#disponibilidad-vencimientos-y-cortes) |
| **Caja diaria (superada)** | Retirada operativamente de interfaces y endpoints; apertura, cierre y pagos en efectivo deshabilitados (ValidationError). Modelos, migraciones y registros históricos preservados para auditoría. | [Retiro caja](STATUS_CAJA_EMBARQUE_LISTADO_PDF_20261105.md) |
| **Mantenimiento y outbox** | Outbox transaccional `notifications.TransactionalNotification`. Worker de mantenimiento con advisory locks PostgreSQL (`pg_try_advisory_lock`). | [Mantenimiento](ARCHITECTURE.md#notificaciones-y-mantenimiento-notifications-core) |
| **Listado provisional de pasajeros en PDF** | Descarga en detalle de viajes exclusiva para Administradores y Superusuarios. PDF A4 horizontal con ReportLab, paginado, encabezados repetidos por página, deduplicación de joins, menores en falda vinculados a butaca de la madre y rotulado provisional no CNRT. | [Listado provisional](STATUS_CAJA_EMBARQUE_LISTADO_PDF_20261105.md) |

---

## 2. Antecedentes históricos superados

Los siguientes puntos correspondieron a definiciones o implementaciones provisionales en etapas previas del proyecto, las cuales quedaron formalmente superadas y no deben reintroducirse:

1. **Vencimiento de 24 horas en reservas manuales**:
   - *Antecedente*: originalmente se configuró `SALES_MANUAL_HOLD_HOURS = 24` para que las reservas manuales vencieran a las 24 horas.
   - *Estado intermedio*: sustituido por la regla confirmada por Sandro de reserva manual sin vencimiento automático (`expires_at = None`), conservando la butaca retenida hasta su cobro o liberación manual (PR #45).
   - *Estado actual*: la reserva sin vencimiento quedó restringida a administradores y superusuarios; los vendedores operan ventas internas por transferencia con vencimiento (`d2aa716`).
2. **Umbral de menores de 2 años**:
   - *Antecedente*: la gratuidad provisional inicial aplicaba a menores de 2 años.
   - *Estado actual*: sustituido por la regla confirmada de menores de cinco años (0 a 4 años cumplidos, `MINOR_FREE_AGE_YEARS = 5`).
3. **Tarifas por tramo (par de paradas)**:
   - *Antecedente*: cada par de paradas (`origin_stop`, `destination_stop`) definía una tarifa independiente en `TripFare`.
   - *Estado actual*: reemplazado por tarifas por salida y categoría (`trip`, `seat_category`), con precio uniforme en todos los tramos permitidos de la salida.
4. **Plantilla de pasaje con QR visual embebido**:
   - *Antecedente*: la plantilla inicial provisional generaba un código QR impreso en el pasaje PDF.
   - *Estado actual*: sustituido por la plantilla oficial aprobada por Sandro de dos talones, que no incluye QR ni código de barras visible, preservando los tokens de forma interna en la base de datos. La validación de embarque que usaba esos tokens fue retirada después (ver punto 7).
5. **Revisión de transferencias por vendedores**:
   - *Antecedente*: en flujos iniciales los vendedores podían interactuar ampliamente con pagos en revisión.
   - *Estado intermedio*: la revisión, aprobación y rechazo de comprobantes de transferencia quedó restringida con exclusividad a usuarios del grupo `Administrador` activos (PR #45).
   - *Estado actual*: para **compras públicas** rige la exclusividad de administradores y superusuarios (confirmada, ver §4). Para **ventas internas**, `d2aa716` introdujo la regla 5 (vendedor aprueba sus propias ventas como paso separado), sustituida por la decisión confirmada: la carga del comprobante por el titular confirma la venta (ver §4).

---
6. **Operación de Caja y pagos en efectivo**:
   - *Antecedente*: Módulo operativo de caja diaria con cobro presencial en efectivo en el panel.
   - *Estado actual*: Retirado operativamente; nuevas operaciones y pagos en efectivo deshabilitados (ValidationError). Se mantiene histórico contable y trazabilidad de auditoría.
7. **Control operativo de embarque**:
   - *Antecedente*: Validación y reversión de embarque en /panel/embarques/ con contadores en manifiestos.
   - *Estado actual*: Retirado de pantallas activas y endpoints; inicio y cierre de viajes desacoplados de embarque. Se preserva base histórica.

## 3. Decisiones comerciales pendientes de Sandro

Las siguientes definiciones permanecen abiertas y **no deben resolverse por suposición**. Cualquier modificación futura del sistema debe contar con la decisión comercial previa documentada:

1. **Fecha comercial de compra del menor**:
   - Definir si la edad del menor debe evaluarse tomando la fecha de creación de la reserva (`Booking.created_at`) o la fecha de confirmación del pago (`Booking.confirmed_at`). Actualmente el sistema utiliza como proxy provisional el instante efectivo de creación.
2. **Seguro de menores gratuitos sin butaca**:
   - Sandro debe confirmar si el menor en falda/brazos debe abonar una prima o tasa de seguro obligatoria, cuál es su importe y qué leyenda legal debe imprimirse en el pasaje. Actualmente se emite con importe $ 0,00 sin cargos adicionales.
3. **Acreditación y validación de menores (RENAPER)**:
   - Confirmar si se integrará validación oficial de identidad mediante RENAPER o si el control del vínculo filial/autorización se delega exclusivamente a la verificación presencial de documentación física en el embarque.
4. **Políticas comerciales de cambios y devoluciones**:
   - Establecer los porcentajes o montos fijos de penalización por cambio de pasaje, la ventana horaria límite para solicitarlo y el régimen aplicable para devoluciones o saldos a favor cuando la cancelación es imputable a la empresa o al pasajero.
5. **Datos fijos fiscales, transportista y aseguradora para emisión**:
   - Confirmar razón social, CUIT, condición de IVA, Ingresos Brutos, inicio de actividades, compañía aseguradora, número de póliza, direcciones físicas exactas de paradas y número oficial de WhatsApp de atención al pasajero. Actualmente el sistema expone marcadores explícitos configurables (`Pendiente de confirmación con Sandro`).
6. ~~**Migración de datos reales de AppSheet / Google Sheets**~~: **CANCELADA** (decisión posterior, ver sección 4).
   - Se conserva el registro histórico: originalmente se preveía definir fecha de corte, estructura de campos y ejecutar una prueba de importación en staging antes de habilitar pasarelas.
7. **Pasarelas de pago automáticas**:
   - ~~Mercado Pago QR presencial~~: reemplazado por **Mercado Pago Checkout Pro** (decisión posterior, ver sección 4). El QR presencial (API de QR/órdenes de sucursal/caja) no se implementa ni se confunde con Checkout Pro.
   - **Payway** (tarjetas de crédito/débito): agregado al alcance para compras públicas web; **pendiente de implementación**. Propuesta: tokenización en navegador + `POST /payments` sincrónico (alternativa: formulario hospedado, requiere `template_id` y validar `notifications_url` en sandbox). Necesita de Sandro: credenciales sandbox (site id + API keys), producto elegido, cuotas, devoluciones, vertical de fraude y coexistencia con Mercado Pago. Detalle en `docs/PAYWAY.md`.

## 4. Decisiones confirmadas posteriores (sesión de continuación)

| Decisión | Detalle | Alcance |
|---|---|---|
| **Migración AppSheet / Google Sheets cancelada** | La aplicación opera con su propia base de datos como fuente única de verdad; no depende de recibir exports de AppSheet/Google Sheets. La herramienta `inventory_legacy_exports` queda archivada como utilitario histórico sin uso previsto. | Elimina la etapa de importación y su bloqueo sobre pasarelas; conserva trazabilidad en §2-§3. |
| **Mercado Pago Checkout Pro autorizado** | Se autoriza implementar **Checkout Pro** (API de preferencias + webhooks firmados) para la compra pública y probarlo en sandbox. Se diferencia expresamente del QR presencial (API de QR/órdenes), que no se implementa. La transferencia bancaria manual se conserva intacta como medio de pago separado. | Implementación en `payments/mercadopago.py`, vista `/webhooks/mercadopago/`, deshabilitada por defecto y con credenciales por entorno. |
| **Reserva sin vencimiento restringida por rol** | Sólo administradores y superusuarios crean reservas manuales sin vencimiento; los vendedores, ventas internas por transferencia con vencimiento y comprobante. Verificado en aceptación local (19/11/2026). | Vigente desde `d2aa716`; sustituye la generalización de PR #45 (ver §2.1). |
| **Caja/efectivo y embarque retirados** | Confirmado: sin nuevas operaciones de caja, cobros en efectivo ni validación de embarque; históricos preservados. Rutas 404 verificadas en aceptación local. | Ver §1 (filas superadas) y §2.6-§2.7. |
| **Revisión de transferencias de compras públicas** | Sólo administradores y superusuarios aprueban o rechazan transferencias de compras ONLINE; vendedores bloqueados por UI, URL, POST y servicio. No modifica la revisión de ventas internas. | Regla explícita por canal en servicio, permisos y vistas, con pruebas por rol. |
| **Verificación de pasaje conservada** | `/tickets/verify/` se mantiene como consulta de solo lectura con sus permisos actuales; el retiro del embarque no la alcanza. Sin QR ni cambios al PDF. | Sin cambios de código. |
| **Cobro de ventas internas por el vendedor titular** | **Ventas internas (canal MANUAL)** *(decisión confirmada por Joel)*: cada vendedor cobra en su **cuenta personal**; el **comprobante es obligatorio**; su carga por el **vendedor titular** es su declaración de cobro y, en la misma operación, aprueba el pago, confirma la reserva y dispara la emisión de pasajes. **No hay segunda aprobación administrativa** ni casilla adicional, y no se usan la cuenta bancaria ni Mercado Pago del negocio. Es una declaración del vendedor, no una verificación bancaria automática. | `payments.services.register_transfer_payment` confirma en la misma transacción cuando carga el titular; sin casilla en el panel. |
| **Pendientes que se mantienen** | Menores (fecha de referencia, seguro, RENAPER), cambios/cancelaciones/devoluciones, datos fiscales/bancarios reales y Payway continúan pendientes de Sandro (Payway ya está en alcance; faltan sus decisiones y credenciales, ver `docs/PAYWAY.md`). | Sin cambios respecto de §3. |
