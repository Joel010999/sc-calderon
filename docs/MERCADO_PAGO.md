# Mercado Pago Checkout Pro — integración y prueba sandbox

Alcance: **Checkout Pro** (API de preferencias + webhooks firmados) para la
compra pública online. No incluye ni se confunde con el QR presencial
(Checkout API / órdenes QR de sucursal-caja). La transferencia bancaria
manual se conserva como medio de pago independiente.

## Referencias oficiales consultadas

- Preferencias (crear/obtener): `POST /checkout/preferences`, `GET /v1/payments/{id}`
  — https://www.mercadopago.com.ar/developers/es/reference/online-payments/checkout-pro-preferences/overview
- `back_urls` y `auto_return`: https://www.mercadopago.com.ar/developers/es/docs/checkout-pro-preferences/configure-back-urls
  (no aceptan `localhost`; requieren dominio con DNS o túnel HTTPS)
- Webhooks y firma `x-signature`/`x-request-id` (HMAC-SHA256 sobre
  `id:<data.id>;request-id:<x-request-id>;ts:<ts>;`):
  https://www.mercadopago.com.mx/developers/es/docs/your-integrations/notifications/webhooks
- Credenciales y usuarios de prueba: panel de desarrolladores
  https://www.mercadopago.com.ar/developers — *Tus integraciones → aplicación →
  Credenciales* y *Usuarios de prueba*.

## Configuración (todas por entorno, deshabilitado por defecto)

| Variable | Descripción |
|---|---|
| `MERCADO_PAGO_ENABLED` | `False` por defecto; la UI muestra el método deshabilitado y el webhook responde 403. |
| `MERCADO_PAGO_ACCESS_TOKEN` | Access token de la aplicación (test: `TEST-…`/`APP_USR-…` según panel). Solo backend. |
| `MERCADO_PAGO_WEBHOOK_SECRET` | Clave de firma del webhook (Webhooks → clave secreta). |
| `MERCADO_PAGO_COLLECTOR_ID` | `collector_id` esperado de la cuenta receptora; se valida en cada pago. |
| `MERCADO_PAGO_API_BASE` | Base de la API (default `https://api.mercadopago.com`). |
| `MERCADO_PAGO_WEBHOOK_TOLERANCE_SECONDS` | Tolerancia anti-replay del `ts` firmado (default 300). |
| `PUBLIC_BASE_URL` | Base pública HTTPS: alimenta `back_urls` y `notification_url`. |

## Flujo implementado

1. `POST /resumen/<uuid>/mercadopago/iniciar/` (sesión propietaria + CSRF):
   crea `Payment` `MERCADO_PAGO`/`PENDING` con importe server-side y crea la
   preferencia en MP con `external_reference=<booking.public_id>`;
   redirige a `init_point`.
2. MP redirige al comprador a `back_urls` (resumen). La URL de retorno **no**
   confirma nada.
3. `POST /webhooks/mercadopago/` (CSRF-exempt): valida firma HMAC (401 si
   inválida), deduplica por `(provider, external_id, action)` en
   `PaymentGatewayEvent`, consulta `GET /v1/payments/{id}` autenticado y
   valida `collector_id`, `external_reference`→booking, `transaction_amount`
   y `currency_id` antes de transicionar. El intento local se identifica
   por `external_payment_id` cuando ya está registrado; una notificación
   de otro intento de pago se ignora (`IGNORED_OTHER_PAYMENT`) y las de
   topics ajenos a pagos se registran como `IGNORED_TOPIC`.
4. `approved` → confirma reserva/butacas y encola fulfillment (mismo camino
   que la aprobación de transferencia). `rejected`/`cancelled`/`refunded` →
   `REJECTED` y la reserva sigue HELD para reintento. `pending`/`in_process`
   → sin cambios.
5. Aprobación con reserva vencida → `Payment` queda `APPROVED` con
   `conflict_reason="approved_after_expiry"` + `AuditEvent`; la butaca jamás
   se reasigna ni se emite pasaje; requiere resolución operativa.

La preferencia envía `external_reference=<booking.public_id>`,
`expires=true` + `expiration_date_to=<vencimiento de la retención>`,
`payer.email` cuando la reserva lo tiene, `back_urls`/`auto_return`/
`notification_url` solo cuando `PUBLIC_BASE_URL` es HTTPS público.

## Prueba sandbox (pendiente de credenciales)

1. En https://www.mercadopago.com.ar/developers crear aplicación y obtener
   credenciales de **prueba** (Access Token + Public Key) y crear **usuarios
   de prueba** (vendedor=cuenta receptora, comprador=pagador). El token debe
   corresponder a la aplicación del usuario de prueba vendedor.
2. Cargar las variables en el entorno local **sin commitearlas** (export en la
   shell antes de levantar, o `.env` fuera del repo). Nunca pegar secretos en
   chat ni en el repositorio.
3. Los webhooks requieren HTTPS público: levantar un túnel (ngrok,
   cloudflared) apuntando a `http://127.0.0.1:8000` y exportar
   `PUBLIC_BASE_URL=https://<tunel>` + `CSRF_TRUSTED_ORIGINS`/`ALLOWED_HOSTS`.
   En el panel de MP, configurar el webhook de la aplicación a
   `https://<tunel>/webhooks/mercadopago/` (evento: pagos) y copiar la clave
   secreta generada a `MERCADO_PAGO_WEBHOOK_SECRET`.
4. Flujo esperado: buscar viaje → reservar → "Pagar con Mercado Pago" →
   login con usuario de prueba comprador → pagar con tarjeta de prueba →
   vuelta al resumen → webhook confirma → pasaje PDF descargable.
5. Estado actual: **solo validado con mocks** (26 tests en
   `payments/test_mercadopago.py`). El sandbox E2E queda pendiente hasta
   disponer de credenciales de prueba y túnel HTTPS.
