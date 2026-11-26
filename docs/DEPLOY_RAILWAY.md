# Despliegue en Railway — entorno de aceptación

Entorno de aceptación con datos ficticios, **sin operaciones económicas
reales**. Gateways (Mercado Pago, Payway) deshabilitados. No es una
habilitación productiva.

Candidato desplegado: `32ba087` (rama `feature/mercadopago-checkout-pro`,
sin merge a `main`).

## Arquitectura

| Servicio | Rol | Comando |
|---|---|---|
| `web` | Django + WhiteNoise | `gunicorn sslviajes.wsgi --bind 0.0.0.0:$PORT` |
| `worker` | Mantenimiento + fulfillment + outbox | `python manage.py run_maintenance_worker` |
| `postgres` | PostgreSQL 16 | servicio Railway (`DATABASE_URL` privada) |
| `bucket`/volumen | Comprobantes y PDFs privados | S3-compatible (`PRIVATE_STORAGE_BACKEND=s3`) |

- Los volúmenes de Railway no se comparten entre servicios: comprobantes
  (sube `web`) y PDFs de pasajes (genera `worker`, descarga `web`)
  requieren storage compartido → bucket S3.
- `web` y `worker` corren desde la misma imagen del repo.

## Variables de entorno (web y worker salvo donde se indique)

| Variable | Valor |
|---|---|
| `DEBUG` | `False` |
| `TESTING` | (no definir — jamás `True` en el despliegue) |
| `SCVIAJES_ENVIRONMENT` | `acceptance` |
| `SECRET_KEY` | generada ≥50 chars (no publicar) |
| `ABUSE_HASH_SECRET` | generada (no publicar) |
| `ALLOWED_HOSTS` | dominio Railway + `localhost` |
| `CSRF_TRUSTED_ORIGINS` | `https://<dominio>` |
| `PUBLIC_BASE_URL` | `https://<dominio>` |
| `DATABASE_URL` | `${{Postgres.DATABASE_URL}}` (referencia privada) |
| `EMAIL_BACKEND` | `console` (sin SMTP real — declarado pendiente) |
| `DEFAULT_FROM_EMAIL`, `SERVER_EMAIL` | `no-reply@<dominio>` |
| `PRIVATE_STORAGE_BACKEND` | `s3` |
| `PRIVATE_STORAGE_S3_*` | credenciales del bucket Railway |
| `PRIVATE_STORAGE_S3_QUERYSTRING_EXPIRE` | `300` |
| `MERCADO_PAGO_ENABLED` | `False` |
| `PAYWAY_ENABLED` | `False` |
| `MEDIA_ROOT`, `PROTECTED_MEDIA_ROOT`, `TICKETS_STORAGE_ROOT` | rutas fuera del repo o cubiertas por el storage privado |
| `BANK_TRANSFER_*` | datos ficticios de aceptación (declarado: no es cuenta real) |
| `SECURE_HSTS_SECONDS` | `31536000` |
| `MAINTENANCE_WORKER_MODE` | `resident` (solo worker) |

## Secuencia de despliegue

1. `railway link` al proyecto existente (o crear proyecto si no hay).
2. Inventariar servicios; reutilizar Postgres existente (verificar
   versión y contenido; respaldo antes de migrar si hay datos).
3. Setear variables anteriores en cada servicio.
4. `web`: `railway up` desde la raíz del repo (Procfile `web:`).
5. `worker`: mismo repo, start `python manage.py run_maintenance_worker`.
6. Migraciones: `railway run python manage.py migrate --noinput`
   (o pre-deploy) — verificar `connection.vendor == 'postgresql'`
   y `SELECT version()` desde `manage.py shell`.
7. `collectstatic` en build/start (manifest WhiteNoise requerido).
8. Health: `GET /health/live/` y `/health/ready/` 200.

## Protección del entorno de aceptación

- Datos ficticios declarados; gateways deshabilitados; correo por
  consola (no se envía nada real) — declarado pendiente de SMTP real.
- Acceso externo protegido (credenciales de aceptación provistas por
  canal privado; las contraseñas demo del repo NO se publican).
- `production_preflight` se ejecuta y sus FAILs reales se documentan
  (SMTP, datos bancarios reales): no se disfraza de entorno productivo.

## Verificación post-despliegue

Login admin + 2 vendedores, venta interna con comprobante → confirma →
PDF, compra pública → revisión → aprobación → PDF, ida/vuelta con
menor, aislamiento vendedores, descargas protegidas, reportes, listado
de pasajeros, mantenimiento/fulfillment sin intervención local,
reinicio controlado conservando reservas/comprobantes/PDFs.
