# Despliegue en Railway — entorno de aceptación

Entorno de aceptación con datos ficticios, **sin operaciones económicas
reales**. Gateways (Mercado Pago, Payway) deshabilitados. No es una
habilitación productiva.

Candidato validado: `32ba087`; desplegado como `c930b87` (mismo código de
aplicación + documentación del checkpoint), rama
`feature/mercadopago-checkout-pro`, sin merge a `main`.

## Arquitectura

| Servicio | Rol | Comando |
|---|---|---|
| `web` | Django + WhiteNoise | `gunicorn sslviajes.wsgi --bind 0.0.0.0:$PORT` |
| `worker` | Mantenimiento + fulfillment + outbox | `python manage.py run_maintenance_worker` |
| `postgres` | PostgreSQL 18 (≥16) | servicio Railway (`DATABASE_URL` privada) |
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
| `MAINTENANCE_WORKER_LOCK_WAIT_SECONDS` | `120` (solo worker; espera el advisory lock tras reinicios) |

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

## Despliegue efectivo (26/11/2026)

| Elemento | Valor verificado |
|---|---|
| URL | https://sc-calderon-production.up.railway.app |
| SHA | `c930b87` (contiene el candidato `32ba087`) desde `feature/mercadopago-checkout-pro` |
| Servicios | `sc-calderon` (web), `worker`, `Postgres` — proyecto existente, sin duplicados |
| PostgreSQL | imagen `postgres-ssl:18`, servidor 18.6, volumen persistente 5 GB en `/var/lib/postgresql/data` |
| Storage privado | bucket Railway S3 `scviajes-privados` (`PRIVATE_STORAGE_BACKEND=s3`) |
| Gateways | `MERCADO_PAGO_ENABLED=False`, `PAYWAY_ENABLED=False`; sin cobros reales |
| `TESTING` | no definida; `DEBUG=False`; `SCVIAJES_ENVIRONMENT=acceptance` |
| Respaldo | `pg_dump -Fc` previo a la siembra, guardado fuera del repo |

Resultados:

- Migraciones: ya aplicadas por el candidato (52); sin migraciones pendientes.
- Datos ficticios: `seed_demo_scenario` sin `--reset` (no se borraron datos);
  contraseñas demo nuevas aleatorias en variables Railway (no publicadas).
- `e2e/acceptance.spec.js` contra la URL Railway: **16/16 aprobadas**
  (ida/vuelta con menor, retención de butaca, transferencia pública,
  aprobación/rechazo, permisos y aislamiento, reportes, fulfillment, QR).
- Reinicio controlado de `Postgres`, `sc-calderon` y `worker`: Postgres
  recuperó automáticamente; usuarios, reservas, pagos, viajes y pasajes
  se conservaron y el último PDF sigue presente en el bucket. El worker
  continuó procesando (emitió un pasaje y venció reservas) sin
  intervención local.
- `/health/live/` 200. `/health/ready/` 503 **esperado**: exige SMTP real
  (`EMAIL_BACKEND` console) — pendiente externo.

Observaciones:

- Si Postgres se reinicia, el proceso worker termina con
  `InterfaceError: connection already closed` y Railway lo reinicia; el
  nuevo proceso retoma con `MAINTENANCE_WORKER_LOCK_WAIT_SECONDS=120`.
- Se corrigieron rutas `MEDIA_ROOT`/`PROTECTED_MEDIA_ROOT`/
  `TICKETS_STORAGE_ROOT` que habían quedado con prefijo Windows
  (`C:/Program Files/Git/...`) por conversión de Git Bash.

Pendientes externos: SMTP real, datos bancarios reales, credenciales
propias de Mercado Pago/Payway del comercio, dominio propio.
