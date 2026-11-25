# Payway — integración con tarjetas (implementada, deshabilitada por defecto)

Estado: **implementado y deshabilitado** (`PAYWAY_ENABLED=False`). Payway es
medio de pago con tarjetas de crédito y débito para **compras públicas web**
(canal ONLINE), independiente de Mercado Pago: credenciales, contrato,
liquidación y circuito de confirmación son distintos.

**Validación:** el servicio, las vistas y los estados están cubiertos por
mocks del cliente HTTP (`payments/test_payway.py`) y por las suites de CI en
PostgreSQL 16. **El sandbox real NO fue validado**: requiere las credenciales
del comercio que Sandro debe solicitar a Payway (ver sección 8). Las pruebas
con mocks no reemplazan esa validación.

## Implementación

- Servicio: `payments/payway.py` — cliente HTTP mínimo sin SDK, autenticación
  por header `apikey` (clave privada), timeout corto; nunca registra payloads
  (contienen el token).
- Flujo público: `payment_pending.html` (opción "Pagar con Tarjeta") →
  `POST iniciar_payway` persiste el `Payment` PENDING con
  `site_transaction_id` **antes de cualquier contacto con el proveedor** →
  `payway_checkout.html` tokeniza con `decidir.js` (clave pública) →
  `POST ejecutar_payway` ejecuta el cobro server-side.
- El navegador nunca envía PAN/CVV al servidor; el token tampoco se guarda
  en base, logs ni auditoría (`PaymentGatewayEvent` sólo registra el
  `site_transaction_id` como `external_id` y la acción).
- Confirmación: sólo por respuesta del proveedor o consulta autenticada
  (`GET /payments?siteOperationId=<site_transaction_id>`); el retorno del
  navegador no confirma nada.
- Resultado desconocido (timeout/red/5xx): el intento queda PENDING con
  evento `UNKNOWN_RESULT`, se bloquea un nuevo cobro de la reserva —incluso
  por otro medio de pago— y el comando `run_operational_maintenance
  --task payments` concilia por `site_transaction_id`.
- **Una lista vacía no prueba ausencia de cobro**: la conciliación filtra
  los resultados por correspondencia exacta de `site_transaction_id`,
  detecta ambigüedad (>1 candidato → bloqueo manual) y, si la fila es
  parcial, reconsulta el detalle autenticado `GET /payments/{id}` antes de
  aplicarla. Sin coincidencia verificable, el intento permanece bloqueado
  (`STILL_UNKNOWN`); no existe reintento automático por "no encontrado".
- **Antes de aplicar una aprobación** se exigen `site_transaction_id` igual
  al del intento, `id` del pago, `amount` y `currency` coincidentes; campos
  ausentes o inválidos → `INCOMPLETE`, pendiente y bloqueado.
- **Un nuevo POST de cobro sólo es posible** tras `VALIDATION_ERROR`, que
  se clasifica exclusivamente como HTTP 400 con `error_type` estructurado
  `invalid_request_error` (rechazo de parámetros documentado por la API
  Decidir2, antes de procesar el cobro). Cualquier otro error —otros 4xx
  (401/403, 404, 408, 409, 429), 5xx, cuerpos vacíos/malformados o sin
  `error_type`— se trata como resultado desconocido: conciliación, sin
  reintento ni habilitación de otro medio. El código HTTP por sí solo no
  demuestra ausencia de cobro.
- **Vencimiento revalidado al aplicar**: la hora efectiva se toma dentro
  de la transacción; si la reserva venció durante la llamada al proveedor,
  la aprobación se registra como conflicto sin confirmar ni emitir.
- Orden de bloqueos unificado con los demás servicios: **Booking →
  Payment** (sin bloqueos durante llamadas de red).
- Aprobación tardía (reserva vencida o pago en estado terminal): se registra
  `conflict_reason` **sin** reasignar butacas, emitir pasajes ni asumir
  devolución.
- Anti doble cobro entre métodos: la restricción existente
  `payments_active_booking_unique` impide un segundo pago activo de
  cualquier método por reserva; `start_payway_payment` reusa el intento
  PENDING existente.
- Config: `PAYWAY_ENABLED`, `PAYWAY_SANDBOX`, `PAYWAY_PUBLIC_API_KEY`,
  `PAYWAY_PRIVATE_API_KEY`, `PAYWAY_API_BASE`, `PAYWAY_SCRIPT_URL`,
  `PAYWAY_PAYMENT_METHODS` (JSON id → etiqueta de los medios habilitados
  por el comercio). Secretos sólo por entorno; ver `.env.example`.

## Fuentes oficiales

- Portal de desarrolladores y onboarding: <https://developers.payway.com.ar/documentation>
- Referencia de API (ventas online / gateway): <https://docs.payway.com.ar/api-reference/ventas-online/payway-gateway-api>
- SDK JavaScript oficial (tokenización en navegador): <https://github.com/payway-ar/sdk-javascript-ventaonline>
- SDKs backend oficiales (Java, PHP, .NET, Node): <https://github.com/payway-ar/sdk-node-ventaonline> (mismo alcance funcional que los demás)
- Portal de documentación de SDKs: <https://documentacion-ventasonline.payway.com.ar/>
- Soporte/alta de API keys: soporte@payway.com.ar, integraciones-ventasonline@payway.com.ar

## 1. Solución que corresponde al checkout web de SC Viajes

Payway ofrece dos modalidades documentadas:

### A. Tokenización + API de pagos (Decidir2 / Gateway API) — propuesta

Flujo de dos pasos según el SDK oficial:

1. **Frontend**: el script `decidir.js` servido por Payway
   (`https://ventasonline.payway.com.ar/static/v2.6.4/decidir.js`, versión
   fija) captura los datos de tarjeta en campos `data-decidir` y genera un
   **token de pago** con la **API key pública**. El PAN, vencimiento y CVV
   viajan del navegador directo a Payway; **nuestro servidor nunca recibe ni
   almacena datos de tarjeta** (alcance PCI reducido).
2. **Backend**: se ejecuta el cobro con `POST /api/v2/payments` autenticado
   con la **API key privada** (header `Apikey`), enviando `token`,
   `site_transaction_id` (identificador del comercio, ≤ 39 caracteres),
   `amount` (entero; **los últimos dos dígitos son decimales**),
   `currency` (`"ARS"`), `payment_method_id`, `installments` y
   `payment_type: "single"`.
3. La respuesta es **sincrónica**: `status` (`approved`/`rejected`),
   `status_details` (ticket, `card_authorization_code`, `error`), `id`
   (identificador del pago Payway), `card_brand`, `bin`.

Ventaja para este sistema: la confirmación llega en la respuesta misma de la
ejecución server-side, sin depender de webhooks. `GET /payments/{id}` permite
reconsulta autenticada de estado.

### B. Formulario de pago hospedado (link de pago)

El backend genera un `checkoutHash` y llama a `payments/link`; la API devuelve
un `payment_link` que redirige a un formulario hospedado en
`https://live.decidir.com/web/checkout/{payment_id}`
(sandbox: `https://developers.decidir.com/web/checkout/{payment_id}`).
Acepta `success_url`, `cancel_url`, `redirect_url` y `notifications_url`, y
requiere `template_id` y credenciales específicas del formulario (`site`,
form API key/site). Es el análogo más cercano a Checkout Pro de Mercado
Pago, pero exige alta de plantilla y validar en sandbox el formato, firma y
contenido real de las notificaciones — **sin esas credenciales no puede
verificarse**.

**Propuesta:** modalidad A como integración principal (confirmación sincrónica,
sin circuito nuevo de webhooks, consistente con el modelo de retención de
butacas). La B queda como alternativa si el comercio prefiere checkout
hospedado; su notificación debe verificarse en sandbox antes de diseñarla.

## 2. Datos de tarjeta y CVV

Con la modalidad A, los datos sensibles los captura el script oficial en el
navegador y viajan directo a Payway. El servidor de SC Viajes sólo recibe el
**token de un solo uso**, el `bin` y metadatos no sensibles de la respuesta.
El CVV no se almacena nunca. No se deben capturar datos de tarjeta en campos
propios.

## 3. Alta del comercio, sandbox y credenciales

- Registro en el portal de desarrolladores (`developers.payway.com.ar`),
  alta comercial con Payway/Prisma y provisión de **site id**, **API key
  pública** y **API key privada** por ambiente.
- Sandbox: `https://developers.decidir.com/api/v2`. La documentación oficial
  publica una API key pública de ejemplo para sandbox, pero la **ejecución de
  pagos requiere la API key privada del comercio**: sin el alta de Sandro no
  hay sandbox real (los mocks no validan al proveedor).
- Producción: `https://ventasonline.payway.com.ar/api/v2`.
- Si se elige el formulario hospedado: además `template_id` y credenciales
  `formApiKey`/`formSite`.

## 4. Qué se puede hacer sin Sandro y qué requiere su intervención

**Sin Sandro** (implementable y verificable localmente/CI):

- `PaymentMethod.PAYWAY` (o `CARD`), servicio `payments/payway.py` con el
  mismo diseño que `payments/mercadopago.py` (cliente HTTP mínimo, flag
  `PAYWAY_ENABLED` deshabilitado por defecto, credenciales sólo por entorno).
- Toda la lógica de estados, idempotencia, vencimiento y fulfillment con
  pruebas unitarias y de concurrencia (PostgreSQL en CI) con el cliente
  simulado — **las pruebas con mocks no validan al proveedor**.

**Requiere Sandro:**

- Alta comercial y credenciales sandbox (site id + API keys pública/privada).
- Decisión de producto: tokenización (A) vs. formulario hospedado (B).
- Política de cuotas (esta propuesta asume **1 cuota**; cuotas promocionales
  no se implementan sin decisión).
- Política de devoluciones/anulaciones (la API las soporta; **no se
  implementan** sin decisión comercial).
- Vertical de detección de fraude aplicable (Payway opera con Cybersource;
  existen verticales por rubro — confirmar cuál corresponde a transporte).
- Coexistencia con Mercado Pago en el checkout público (¿ambas opciones
  visibles u orden/preferencia?).

## 5. Confirmación, importe, moneda, idempotencia y emisión (diseño propuesto)

- `PAYWAY_ENABLED` deshabilitado por defecto; credenciales sólo por variables
  de entorno; secretos jamás en logs ni respuestas.
- Botón "Pagar con tarjeta" en `payment_pending.html`, visible sólo si la
  integración está configurada, igual que Mercado Pago.
- El cobro se ejecuta **únicamente** sobre reserva ONLINE `HELD` vigente,
  verificando `expires_at` inmediatamente antes del `POST /payments`.
- **Importe y moneda server-side**: `amount` se calcula de
  `calculate_booking_total` (snapshots) y se envía en unidades mínimas
  (`Decimal` × 100 → entero); jamás se acepta importe del cliente. La
  respuesta se verifica: `status`, `site_transaction_id`, `amount` y
  `currency` deben coincidir con el pago local.
- **Idempotencia**: `site_transaction_id` determinístico por intento de pago
  (p. ej. derivado de `Payment.public_id`). Payway rechaza un
  `site_transaction_id` repetido; ante ese error o timeout, se consulta
  `GET /payments/{id}`/historial en lugar de ejecutar un segundo cobro.
  El `Payment` local guarda `external_payment_id` con el `id` de Payway.
- `approved` → en una transacción: pago `APPROVED`, reserva `CONFIRMED`,
  `_enqueue_ticket_fulfillment` (misma emisión durable e idempotente de
  hoy). `rejected` → el intento queda registrado y la reserva sigue en
  retención hasta su vencimiento para un nuevo intento.
- El `reviewed_by` queda vacío como en Mercado Pago (aprobación automática):
  `Payment.clean()` debe extender la excepción actual al método Payway.
- Estados intermedios (p. ej. revisión de fraude): el pago queda `PENDING`
  y se reconsulta autenticado por el comando de mantenimiento existente;
  sin worker nuevo.

## 6. Pendientes, rechazados, duplicados y aprobado con reserva vencida

- **Duplicados**: unicidad de `Payment` activo por reserva (igual que hoy) +
  `site_transaction_id` determinístico + reconsulta en vez de re-cobro.
- **Rechazados**: conservan el intento con `status_details.error`; la butaca
  sigue retenida hasta `expires_at` para permitir otro intento.
- **Aprobación sobre reserva vencida**: la ventana es mínima (cobro
  sincrónico verificado antes de ejecutar), pero si ocurre se registra el
  conflicto para resolución operativa — **jamás** se reasigna una butaca ni
  se emite ni se inventa una devolución automática (mismo criterio que
  `mercadopago.py`).
- **Pendientes**: reconsulta por mantenimiento; el cumplimiento del
  vencimiento de la reserva no se altera por el pago pendiente.

## 7. Pruebas

**Implementadas (unitarias/integración HTTP, con cliente simulado) en
`payments/test_payway.py`, corren también en las suites PostgreSQL 16 de CI:**

- Cobro aprobado → pago `APPROVED` + reserva `CONFIRMED` + fulfillment
  encolado; payload con `amount` en unidades mínimas, `installments: 1`,
  `payment_type: "single"`.
- Timeout tras el cobro → conciliación por `site_transaction_id` confirma
  sin un segundo POST; lista vacía → `STILL_UNKNOWN` bloqueado (no prueba
  ausencia de cobro); aparición tardía del cobro → conciliación confirma
  sin nuevo POST.
- `STILL_PENDING`/`MISMATCH`/`INCOMPLETE`/`AMBIGUOUS` bloquean otro POST;
  sólo `VALIDATION_ERROR` lo habilita.
- Aprobación con `id`/`amount`/`currency`/`site_transaction_id` ausentes
  → `INCOMPLETE`, no confirma.
- Vencimiento durante la llamada al proveedor → `CONFLICT_LATE_APPROVAL`.
- Concurrencia ejecución/conciliación (PostgreSQL, CI) → un solo cobro.
- Resultado desconocido o estado no reconocido → queda PENDING de
  conciliación, nunca tratado como rechazo.
- Reserva vencida → no se ejecuta el `POST`; aprobación tardía registrada
  como `conflict_reason` sin reasignar butaca ni emitir.
- Aprobación sobre estado terminal → `CONFLICT_TERMINAL`.
- Rechazo → retención intacta, nuevo intento posible.
- Importe/moneda/`site_transaction_id` de la respuesta validados contra el
  pago local (mismatch → no confirma).
- Unicidad de pago activo por reserva y bloqueo cruzado con transferencia.
- Token nunca persistido ni logueado.
- Vistas: acceso por token de sesión, sólo POST, botón deshabilitado sin
  credenciales, ejecución aprobada confirma la reserva.

**Sandbox (con credenciales de Sandro):**

- Aprobación y rechazo con tarjetas de prueba documentadas, crédito **y**
  débito, en `developers.decidir.com/api/v2`.
- Emisión completa de pasaje tras aprobación y comportamiento al vencer la
  retención durante la operatoria de tarjeta.
- Si se elige formulario hospedado: verificar formato y autenticidad real de
  `notifications_url` antes de diseñar su procesamiento.

## 8. Lo que Sandro debe solicitar a Payway (exacto)

1. **Alta del comercio** en el portal de desarrolladores y credenciales de
   **sandbox**: site id + API key pública + API key privada
   (`developers.decidir.com/api/v2`), para crédito **y** débito.
2. **Producto**: confirmar la modalidad de tokenización + API (opción A de
   este documento); si Payway exige el formulario hospedado, entonces
   `template_id`, `formApiKey` y `formSite`.
3. **Medios de pago habilitados**: `payment_method_id` por marca/tipo para
   `PAYWAY_PAYMENT_METHODS`, y si el contrato habilita débito.
4. **Cuotas**: alcance implementado = 1 cuota (`payment_type: "single"`);
   cualquier plan de cuotas es decisión comercial pendiente.
5. **Devoluciones/anulaciones**: no implementadas; si se requieren, definir
   el circuito contractual antes de codear.
6. **Vertical de detección de fraude (Cybersource)**: qué vertical aplica al
   comercio (existe `cs_ticketing`); hoy no se envía `fraud_detection`.
7. **Coexistencia con Mercado Pago**: orden/condición de medios en el
   checkout cuando ambos estén habilitados.
8. **Credenciales de producción** y dominio final para las URLs del sitio.

## Alcance implementado

- Una cuota, un cobro (`payment_type: "single"`), sin cuotas promocionales,
  sin devoluciones/anulaciones automáticas y sin circuito nuevo de
  autorización. Pendientes comerciales diferibles listados en la sección 8.
