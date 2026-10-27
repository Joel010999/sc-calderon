# Registro de decisiones de SC Viajes

Este documento registra las decisiones confirmadas, los antecedentes históricos superados y las definiciones comerciales pendientes con Sandro, consolidado tras la integración del PR #48.

---

## 1. Decisiones confirmadas e integradas

| Tema | Decisión confirmada | Referencia de especificación |
|---|---|---|
| **Recorridos y tramos** | Recorrido Córdoba ↔ Jujuy con parada Güemes (`GUE`, Salta) en ambos sentidos. Prohibición estricta de tramos dentro de una misma provincia (`Stop.province`). Sin reventa de una butaca en tramos intermedios para el MVP. | [Recorridos](PROJECT_SPEC.md#recorridos-y-paradas) |
| **Tarifas por salida** | Tarifa definida por salida (`Trip`) y categoría (`CAMA` y `SEMI_CAMA`), uniforme en todos los tramos permitidos. Edición exclusiva para Administradores antes de la partida. Inmutabilidad de reservas existentes. | [Tarifas por salida](PROJECT_SPEC.md#tarifas-por-salida) |
| **Butacas prioritarias 51 y 54** | Butacas 51 y 54 bloqueadas para venta general hasta las 00:00 de Argentina de dos días calendario antes de la salida local. Verificación en tiempo real sin worker. | [Butacas y prioridades](PROJECT_SPEC.md#butacas-colectivos-y-prioridades) |
| **Menores de cinco años** | Gratuidad sin butaca para menores estrictamente menores a 5 años (0 a 4 años cumplidos) a la fecha de compra. Acompañante obligatorio, importe $0,00 y sin comisión. En compras ida/vuelta conserva gratuidad en ambos tramos. | [Menores](PROJECT_SPEC.md#compra-y-pasajeros) |
| **Horarios por parada** | Cada parada almacena fecha/hora programada/estimada (`TripStop.scheduled_at`). Carga y edición por Administradores para viajes no iniciados, con validación de secuencia temporal creciente y sin superposiciones. | [Horarios por parada](PROJECT_SPEC.md#horarios-estimados-por-parada) |
| **Reservas manuales** | Reserva rápida sin vencimiento automático (`expires_at = None`). Butaca retenida hasta cobro o liberación manual. Liberación restringida exclusivamente al creador de la reserva. | [Disponibilidad](PROJECT_SPEC.md#disponibilidad-vencimientos-y-cortes) |
| **Alcance por vendedor** | Queryset base acotado por vendedor: el vendedor únicamente visualiza, filtra y descarga sus propias operaciones y balances. Administradores acceden a la visión global. | [Alcance vendedor](PROJECT_SPEC.md#disponibilidad-vencimientos-y-cortes) |
| **Transferencias bancarias** | Revisión de transferencias exclusiva de Administradores activos. Rechazos preservan el intento fallido y retienen la butaca para nuevo comprobante. Aprobación terminal con fulfillment durable. | [Pagos](PROJECT_SPEC.md#pagos-transferencias-y-comisiones) |
| **Balance y comisiones** | Comisión fija de `Decimal("5000.00")` por cada asignación confirmada con precio positivo (por pasajero y tramo). Muestra ventas brutas, unidades comisionables, comisión y neto operativo. | [Balance](PROJECT_SPEC.md#pagos-transferencias-y-comisiones) |
| **Pasaje PDF oficial y serie AAA** | Plantilla de 1 página con dos talones (empresa y pasajero) con logo oficial. Eliminación total de QR y códigos de barras visuales en el PDF. Numeración secuencial persistente `AAA-000000001` con `TicketSequence`. | [Pasajes](PROJECT_SPEC.md#pasajes-y-emisión) |
| **Validación de embarque** | Registro durable en `tickets.BoardingRecord` validado internamente en `/panel/embarques/` por Administrador y Vendedor. Tokens criptográficos preservados internamente en base de datos. | [Arquitectura embarque](ARCHITECTURE.md#módulo-de-pasajes-y-embarque-tickets) |
| **Cortes de venta** | Corte de venta online a 60 minutos de la subida. Corte de venta manual a 30 minutos de la subida; rechazo a 29 minutos. Bloqueo total tras inicio del viaje. | [Cortes](PROJECT_SPEC.md#disponibilidad-vencimientos-y-cortes) |
| **Caja diaria** | Módulo `cash_register` con restricción PostgreSQL de 1 sesión abierta por vendedor. Movimientos inmutables vinculados a cobros en efectivo aprobados. | [Caja diaria](ARCHITECTURE.md#caja-diaria-cash_register) |
| **Mantenimiento y outbox** | Outbox transaccional `notifications.TransactionalNotification`. Worker de mantenimiento con advisory locks PostgreSQL (`pg_try_advisory_lock`). | [Mantenimiento](ARCHITECTURE.md#notificaciones-y-mantenimiento-notifications-core) |

---

## 2. Antecedentes históricos superados

Los siguientes puntos correspondieron a definiciones o implementaciones provisionales en etapas previas del proyecto, las cuales quedaron formalmente superadas y no deben reintroducirse:

1. **Vencimiento de 24 horas en reservas manuales**:
   - *Antecedente*: originalmente se configuró `SALES_MANUAL_HOLD_HOURS = 24` para que las reservas manuales vencieran a las 24 horas.
   - *Estado actual*: sustituido por la regla confirmada por Sandro de reserva manual sin vencimiento automático (`expires_at = None`), conservando la butaca retenida hasta su cobro o liberación manual.
2. **Umbral de menores de 2 años**:
   - *Antecedente*: la gratuidad provisional inicial aplicaba a menores de 2 años.
   - *Estado actual*: sustituido por la regla confirmada de menores de cinco años (0 a 4 años cumplidos, `MINOR_FREE_AGE_YEARS = 5`).
3. **Tarifas por tramo (par de paradas)**:
   - *Antecedente*: cada par de paradas (`origin_stop`, `destination_stop`) definía una tarifa independiente en `TripFare`.
   - *Estado actual*: reemplazado por tarifas por salida y categoría (`trip`, `seat_category`), con precio uniforme en todos los tramos permitidos de la salida.
4. **Plantilla de pasaje con QR visual embebido**:
   - *Antecedente*: la plantilla inicial provisional generaba un código QR impreso en el pasaje PDF.
   - *Estado actual*: sustituido por la plantilla oficial aprobada por Sandro de dos talones, que no incluye QR ni código de barras visible, preservando los tokens y la validación de embarque de forma interna en la base de datos.
5. **Revisión de transferencias por vendedores**:
   - *Antecedente*: en flujos iniciales los vendedores podían interactuar ampliamente con pagos en revisión.
   - *Estado actual*: la revisión, aprobación y rechazo de comprobantes de transferencia quedó restringida con exclusividad a usuarios del grupo `Administrador` activos.

---

## 3. Decisiones comerciales pendientes de Sandro

Las siguientes definiciones permanecen abiertas y **no deben resolverse por suposición**. Cualquier modificación futura del sistema debe contar con la decisión comercial previa documentada:

1. **Fecha comercial de compra del menor**:
   - Definir si la edad del menor debe evaluarse tomando la fecha de creación de la reserva (`Booking.created_at`) o la fecha de confirmación del pago (`Booking.confirmed_at`). Actualmente el sistema utiliza como proxy provisional el instante efectivo de creación.
2. **Seguro de menores gratuitos sin butaca**:
   - Sandro debe confirmar si el menor en falda/brazos debe abonar una prima o tasa de seguro obligatoria, cuál es su importe y qué leyenda legal debe imprimirse en el pasaje. Actualmente se emite con importe $ 0,00 sin cargos adicionales.
3. **Acreditación y validación de menores (RENAPER)**:
   - Confirmar si se integrará validación oficial de identidad mediante RENAPER o si el control del vínculo filial/autorización se delega exclusivamente a la verificación presencial de documentación física en el embarque.
4. **Autorización previa de butacas prioritarias 51 y 54**:
   - Definir qué rol puede autorizar la asignación de las butacas 51 y 54 antes de su liberación comercial a las 00:00 de dos días previos a la salida, qué documentación de discapacidad/movilidad reducida se requiere y qué tarifa comercial corresponde aplicar.
5. **Políticas comerciales de cambios y devoluciones**:
   - Establecer los porcentajes o montos fijos de penalización por cambio de pasaje, la ventana horaria límite para solicitarlo y el régimen aplicable para devoluciones o saldos a favor cuando la cancelación es imputable a la empresa o al pasajero.
6. **Datos fijos fiscales, transportista y aseguradora para emisión**:
   - Confirmar razón social, CUIT, condición de IVA, Ingresos Brutos, inicio de actividades, compañía aseguradora, número de póliza, direcciones físicas exactas de paradas y número oficial de WhatsApp de atención al pasajero. Actualmente el sistema expone marcadores explícitos configurables (`Pendiente de confirmación con Sandro`).
7. **Migración de datos reales de AppSheet / Google Sheets**:
   - Definir la fecha de corte, estructura de campos definitivos y ejecutar la prueba de importación en staging autorizado antes de habilitar pasarelas de pago.
8. **Pasarelas de pago automáticas (Mercado Pago QR y Payway)**:
   - Integración diferida hasta después de la migración de datos históricos.
