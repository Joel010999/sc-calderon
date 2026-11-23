# Especificación del proyecto SC Viajes

Este documento consolida las reglas de negocio confirmadas y vigentes tras la integración del PR #48, distinguiendo el estado comprobado en código de los antecedentes históricos y de las definiciones comerciales pendientes de validación con Sandro. Ver [estado](STATUS.md) y [decisiones](DECISIONS.md).

## Producto

SC Viajes comercializa pasajes de colectivos de larga distancia a través de un canal de venta online y gestiona la operación y las ventas mediante un panel interno 100 % personalizado. La caja diaria y el control de embarque fueron retirados operativamente (ver `docs/DECISIONS.md` §1-§2); sus registros históricos se conservan.

## Recorridos y paradas

### Córdoba → Jujuy

1. Córdoba Capital: suben pasajeros.
2. Jesús María: solamente suben; nadie baja.
3. Güemes: solamente bajan; nadie sube.
4. Perico: solamente bajan; nadie sube.
5. Palpalá: solamente bajan; nadie sube.
6. San Salvador de Jujuy: terminal final; bajan todos los pasajeros restantes.

### Jujuy → Córdoba

1. San Salvador de Jujuy: suben pasajeros.
2. Palpalá: solamente suben; nadie baja.
3. Perico: solamente suben; nadie baja.
4. Güemes: solamente suben; nadie baja.
5. Jesús María: solamente bajan; nadie sube.
6. Córdoba Capital: terminal final; bajan todos los pasajeros restantes.

### Reglas de recorrido y tramos interprovinciales

- El sistema valida origen y destino según el orden secuencial y los permisos de subida y bajada de cada recorrido.
- Para el MVP no se revende una misma butaca por tramos intermedios dentro del mismo viaje.
- **Prohibición de tramos intraprovinciales**: origen y destino deben pertenecer a provincias diferentes (`origin.province != destination.province`). La provincia se configura explícitamente en cada parada (`operations.Stop.province`). No se permite la venta de tramos dentro de una misma provincia.

## Compra y pasajeros

- Se permiten compras de pasajes únicamente de ida o de ida y vuelta.
- Una compra puede incluir varios pasajeros (hasta `SALES_MAX_PASSENGERS_PER_BOOKING`, configurable; 4 por defecto).
- **Menores de cinco años (gratuidad sin butaca)**:
  - Todo menor que tenga estrictamente menos de 5 años (`0` a `4` años cumplidos) a la fecha de referencia de compra califica como menor gratuito sin butaca (`BookingPassenger.is_lap_infant = True`).
  - Al cumplir **exactamente 5 años** (o más) a la fecha de compra, el menor deja de ser gratuito: abona tarifa completa y ocupa butaca propia.
  - El menor gratuito no genera `SeatAssignment`, no descuenta disponibilidad del colectivo, mantiene importe `Decimal("0.00")`, no computa unidad comisionable de vendedor y recibe su pasaje sin asiento (`seat_number = None`, categoría `LAP_INFANT`).
  - Requiere obligatoriamente estar asociado a una madre o persona acompañante con butaca propia dentro de la misma compra; se rechazan autorreferencias, ciclos y acompañantes que sean a su vez menores gratuitos.
  - **Compras ida y vuelta**: el snapshot de gratuidad se evalúa al crear la reserva y se congela para ambos tramos. Si la compra de ida y vuelta se realiza antes de cumplir 5 años, conserva la gratuidad en ida y regreso. Si el regreso se compra por separado después de cumplir 5 años, constituye una compra nueva y abona tarifa completa.
  - **Antecedente histórico corregido**: en etapas iniciales se contempló un umbral provisional de menores de 2 años; dicha regla fue formalmente actualizada y sustituida por el umbral de menores de 5 años.
- Datos de contacto y autenticación:
  - Se puede comprar como invitado en el canal online.
  - El cliente puede registrarse con correo y contraseña o iniciar sesión con Google u opcionalmente Apple OIDC.
  - No se utilizan códigos enviados por correo para iniciar sesión.
  - El correo de compra es obligatorio.
  - El consentimiento para comunicaciones comerciales es explícito, separado y auditable.

## Butacas, colectivos y prioridades

- Categorías: Cama y Semicama con precios diferenciados.
- Colectivo de referencia inicial: 60 butacas (planta alta: semicama 1 a 48; planta baja: cama 49 a 60). La distribución se modela dinámicamente mediante coordenadas relativas (`position_x`, `position_y`) y plantas (`Seat.Deck`), sin esquemas rígidos en el código.
- No se cambia el colectivo una vez programado el viaje en la operación comercial informada.
- **Butacas prioritarias 51 y 54** (comportamiento implementado; existe una discrepancia pendiente con el acuerdo posterior de solicitud por correo hasta 48 h antes sin liberación automática acordada, ver `docs/CONTEXTO_COORDINACION.md` §2):
  - Las butacas 51 y 54 están reservadas prioritariamente y bloqueadas para la venta general hasta las 00:00 (hora local de Argentina, `America/Argentina/Buenos_Aires`) de dos días calendario antes de la fecha local de salida del viaje (por ejemplo, salida un jueves a las 20:00 se libera el martes previo a las 00:00).
  - No se liberan calculando 48 horas exactas.
  - La restricción se valida server-side en disponibilidad y en el servicio de creación de reservas con bloqueo transaccional. Ocupaciones previas se preservan.

## Tarifas por salida

- **Tarifa por salida y categoría**: cada salida (`operations.Trip`) define una tarifa activa para `CAMA` y una para `SEMI_CAMA`, con restricción única de base de datos sobre `(trip, seat_category)`.
- **Precio uniforme**: dentro de una misma salida, el importe es idéntico para todos los tramos permitidos del recorrido.
- **Edición administrativa auditada**: sólo usuarios del grupo `Administrador` o superusuarios pueden modificar tarifas antes de la hora de partida (`now < trip.departure_at`).
- **Inmutabilidad histórica**: el cambio de tarifa solo aplica a nuevas reservas sobre disponibilidad libre; reservas existentes (`HELD` o `CONFIRMED`) conservan su snapshot de precio en `SeatAssignment.price`.

## Horarios estimados por parada

- Cada cronograma de viaje registra la fecha y hora programada/estimada por parada (`TripStop.scheduled_at`).
- Los administradores pueden visualizar y editar los horarios por parada desde el panel interno antes de la salida del viaje.
- El servicio exige cronología estrictamente creciente entre paradas consecutivas (admitiendo cruces de medianoche), pertenencia al recorrido y ausencia de superposiciones operativas del mismo colectivo.

## Disponibilidad, vencimientos y cortes

- **Retención online**: una selección online mantiene la butaca durante 15 minutos (`SALES_ONLINE_HOLD_MINUTES`).
- **Reserva manual sin expiración automática (restringida por rol)**:
  - Sólo administradores y superusuarios pueden crear reservas manuales con `expires_at = None`; no vencen por tiempo y retienen la butaca hasta su cobro o liberación manual explícita (`require_can_create_manual_unexpired_reservation`).
  - Los vendedores registran ventas internas por transferencia con vencimiento `min(medianoche AR del día de compra, salida inicial − 1 h)` (`sales/internal_transfer.py`); un intento de forzar el tipo manual se convierte en venta por transferencia con vencimiento.
  - **Antecedentes históricos corregidos**: la regla inicial contemplaba un vencimiento de 24 horas (`SALES_MANUAL_HOLD_HOURS`); luego se definió la reserva manual sin vencimiento para todo el panel (PR #45), y desde `d2aa716` (ventas internas por transferencia) ese privilegio quedó restringido a administración.
- **Alcance y liberación por vendedor**:
  - Las reservas manuales se atribuyen al vendedor autenticado.
  - La acción de liberación de una reserva manual está restringida exclusivamente al vendedor creador de la misma.
  - El alcance por vendedor se aplica en el QuerySet base: los vendedores sólo visualizan, filtran y exportan sus propias reservas y pagos.
- **Corte de ventas**:
  - Venta online: cierra exactamente una hora antes del horario de subida del pasajero (`SALES_ONLINE_CUTOFF_MINUTES = 60`).
  - Venta manual: se permite registrar ventas manuales hasta el corte estricto de 30 minutos antes de la subida (`SALES_MANUAL_CUTOFF_MINUTES = 30`). A los 29 minutos la venta manual es rechazada por el servidor.
  - Una vez iniciado el viaje (`STARTED`), no se admiten nuevas reservas ni confirmaciones de ningún tipo.

## Pagos, transferencias y comisiones

- Métodos vigentes:
  - Transferencia bancaria manual, con comprobante subido por el cliente o vendedor.
  - Mercado Pago Checkout Pro para la compra pública (ver más abajo; deshabilitado por defecto).
- **Efectivo y caja diaria (retirados)**: el cobro en efectivo con sesión de caja fue retirado operativamente; nuevos registros son rechazados y los históricos se conservan para auditoría.
- **Revisión de transferencias — compras públicas (canal ONLINE)** *(decisión confirmada)*:
  - Sólo administradores y superusuarios activos pueden aprobar o rechazar transferencias de compras realizadas desde la página web.
  - Los vendedores no pueden revisarlas por interfaz, URL directa, POST manipulado ni servicio de aplicación (`payments.services.validate_payment_reviewer`, `panel/internal_sales_permissions.py`, `panel/payment_views.py`), aun cuando un dato manipulado los asocie al pago.
- **Cobro de ventas internas (canal MANUAL)** *(decisión confirmada por Joel)*:
  - Cada vendedor cobra en su cuenta personal; no se usan la cuenta bancaria ni Mercado Pago del negocio y el panel no muestra datos bancarios del negocio en ventas internas.
  - El comprobante es obligatorio. Su carga por el vendedor titular de la venta confirma el cobro, aprueba el pago, confirma la reserva y dispara la emisión, en una sola transacción y sin segunda aprobación administrativa ni casilla de "pago verificado".
  - Es una declaración del vendedor, no una verificación bancaria automática.
  - Otro vendedor no ve ni opera ventas ajenas. Si un administrador carga el comprobante de la venta de otro vendedor, el pago queda en revisión hasta aprobación administrativa.
  - *Antecedentes*: PR #45 declaraba revisión exclusiva de administradores; `d2aa716` introdujo la aprobación por el vendedor titular como paso separado (regla 5). Ambos quedan sustituidos por esta decisión.
  - El rechazo de un comprobante preserva el registro del intento y su motivo, manteniendo la butaca retenida para permitir la carga de un nuevo comprobante.
  - La aprobación confirma de forma atómica y terminal el pago, la reserva y las butacas, disparando el fulfillment durable de pasajes.
- **Balance y comisiones de vendedores**:
  - Perfil de vendedor gestionado en `panel.StaffProfile`.
  - La comisión se fija en `Decimal("5000.00")` por cada asignación confirmada con precio positivo (por pasajero y tramo).
  - Los menores gratuitos sin butaca ($0,00) y las ventas web sin vendedor no generan comisión.
  - Los ingresos y comisiones se basan en pagos `APPROVED` de reservas `CONFIRMED`.
  - El sistema muestra ventas brutas, unidades comisionables, comisión total y neto operativo; no realiza transferencias bancarias automáticas ni marca rendiciones automáticamente.
- **Mercado Pago Checkout Pro**: integración autorizada e implementada para la compra pública (preferencia creada en backend con importe server-side, confirmación por webhook firmado + consulta autenticada; nunca por URL de retorno). Deshabilitada por defecto vía `MERCADO_PAGO_ENABLED` y credenciales por entorno. El QR presencial no forma parte del alcance.
- Payway (tarjetas) sigue diferido, pendiente de autorización.

## Pasajes y emisión

- **Plantilla PDF oficial aprobada**:
  - Documento PDF de exactamente 1 página (596 x 842 pt).
  - Estructurado en dos talones: **Talón superior** ("Talón empresa/agencia") y **Talón inferior** ("Talón para el pasajero"), separados por línea punteada de corte.
  - Logo oficial incrustado en ambos talones (`static/assets/scviajes-logo.png`).
- **Supresión de códigos visuales**:
  - No se imprimen códigos QR ni códigos de barras visuales en ninguno de los talones.
  - Los tokens criptográficos (`verification_token_hash`, `download_token_hash`) se conservan internamente en la base de datos. La validación de embarque en `/panel/embarques/` fue retirada; la verificación del pasaje en `/tickets/verify/` **se conserva** como consulta pública de solo lectura con sus permisos actuales (retirar el embarque no retira la verificación). Su URL se calcula al emitir pero hoy no se imprime en el pasaje.
- **Numeración persistente correlativa (serie AAA)**:
  - Numeración única y consecutiva gestionada mediante el modelo transaccional `TicketSequence` con bloqueo `select_for_update()`.
  - Formato `AAA-000000001` en adelante para pasajes reales emitidos (`AAA-000000000` reservado para plantilla vacía). El mismo código se imprime en ambos talones.
- **Entrega**:
  - Se genera pasaje PDF descargable desde la web y se envía agrupado por correo electrónico al titular de la compra. No se envían pasajes por WhatsApp.
  - Para menores de cinco años, el pasaje expresa "Pasaje gratuito — sin butaca", indica el acompañante y consigna importe "$ 0,00".
  - Las condiciones generales del pasaje se reproducen en dos columnas con advertencia de validación comercial pendiente.

## Cambios y cancelaciones (políticas pendientes)

- El pasajero no puede autogestionar la cancelación del pasaje desde la web.
- La solicitud de cambio requiere gestión operativa.
- Se encuentran pendientes de definición comercial con Sandro:
  - Plazos definitivos y porcentaje o monto de penalización por cambio.
  - Política de devolución o saldo a favor ante contingencias de la empresa.
  - Condiciones comerciales y operativas para cambio de titularidad/nombre del pasajero.

## Migración de datos heredados

- SC Viajes operó históricamente con AppSheet y Google Sheets.
- **Decisión posterior (ver `docs/DECISIONS.md` §4)**: la migración fue **cancelada**. La aplicación opera con su propia base de datos como fuente única de verdad y no depende de recibir exports de AppSheet/Google Sheets.
- Se conserva como utilitario histórico el toolkit de solo lectura `inventory_legacy_exports` (sin uso previsto).

## Decisiones comerciales pendientes de Sandro

1. **Fecha comercial de compra del menor**: definir si la edad se evalúa al instante de creación de la reserva (`Booking.created_at`) o al momento de confirmación del pago (`Booking.confirmed_at`).
2. **Seguro de menores gratuitos sin butaca**: confirmar si debe cobrarse póliza o cargo de seguro obligatorio, su importe y leyenda en el pasaje.
3. **Validación de identidad y vínculo (RENAPER)**: definir el mecanismo de acreditación del vínculo del menor y si se integrará validación oficial o control manual al embarque.
4. **Autorización previa de butacas prioritarias**: definir el procedimiento, usuario autorizante y tarifa aplicable para asignar las butacas 51 y 54 antes de su liberación comercial general a 2 días de la salida.
5. **Políticas de devolución y cambios**: aprobar porcentajes de retención, plazos límites y régimen de reembolsos o créditos.
6. **Datos fijos de transporte y legales**: razón social, CUIT, condición de IVA, Ingresos Brutos, fecha de inicio de actividades, compañía aseguradora, número de póliza y direcciones exactas de boletería/plataforma en cada parada.
