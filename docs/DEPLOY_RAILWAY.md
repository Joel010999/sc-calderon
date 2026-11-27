# Despliegue en Railway — entorno de aceptación

Entorno de aceptación con datos ficticios, **sin operaciones económicas
reales**. Gateways (Mercado Pago, Payway) deshabilitados. No es una
habilitación productiva.

- **URL:** https://sc-calderon-production.up.railway.app
- **Código desplegado:** rama `feature/mercadopago-checkout-pro` —
  commit `b3d0387` (candidato `32ba087` + herramientas de backup PG18
  y docs; sin cambios de negocio).
- **Imagen:** `Dockerfile` propio (python:3.12-slim-trixie +
  `postgresql-client-18` PGDG + collectstatic en build). Reemplaza a
  Railpack/mise (elimina la dependencia de
  `MISE_PYTHON_GITHUB_ATTESTATIONS=false`).
- **Proyecto:** `sc calderon` (workspace "Render Byte's Projects"),
  entorno `production`.

## Servicios efectivos

| Servicio | Rol | Origen |
|---|---|---|
| `sc-calderon` | web — gunicorn + WhiteNoise | repo GitHub, rama feature |
| `worker` | `python manage.py run_maintenance_worker` (residente) | mismo repo/rama |
| `Postgres` | **PostgreSQL 18.6** (volumen 5 GB, `postgres-volume`) | imagen Railway |
| `scviajes-privados` | bucket S3 Railway (región sjc) | comprobantes + PDFs |

> El plan original apuntaba a PostgreSQL 16; la instancia existente ya
> estaba en la imagen Postgres 18 — se mantuvo por instrucción de Joel
> (no bajar versión). La aplicación corre y migra correctamente sobre
> PostgreSQL 18.6 (verificado: `connection.pg_version = 180006`, 0
> migraciones pendientes tras aplicar las 5 del candidato).

## Storage privado

- Los volúmenes Railway no se comparten entre servicios y tanto `web`
  como `worker` escriben/leén archivos → **bucket S3 nativo**
  (`PRIVATE_STORAGE_BACKEND=s3`, endpoint `https://t3.storageapi.dev`).
- Verificado post-reinicio: PDFs (`tickets/…`) y comprobantes
  (`vouchers/…`) existen en el bucket; descargas solo por vistas
  autorizadas (anónimo → 302 login / 404).

## Configuración aplicada

`DEBUG=False`, `SECRET_KEY`/`ABUSE_HASH_SECRET` presentes,
`ALLOWED_HOSTS`+`CSRF_TRUSTED_ORIGINS`+`PUBLIC_BASE_URL` con el dominio
HTTPS, HSTS 1 año, cookies seguras, `EMAIL_BACKEND=console`
(**declarado: no hay entrega real de correo**), `BANK_TRANSFER_*`
ficticios (**declarado: no son cuenta de cobro real**), gateways off,
OAuth off, `MEDIA_ROOT`/`PROTECTED_MEDIA_ROOT`/`TICKETS_STORAGE_ROOT`
fuera del árbol, `SITE_BRAND_TAGLINE` marcando el entorno de aceptación,
`RAILPACK_DEPLOY_APT_PACKAGES=postgresql-client` en web (herramientas
de backup), `MISE_PYTHON_GITHUB_ATTESTATIONS=false` (build).

Usuarios demo: `demo-20261005-admin`, `demo-20261005-vendedor`,
`demo-20261005-cash-vendedor` con contraseñas **nuevas generadas**
(no las del repo). Acceso en `~/scviajes-acceptance/ACCESOS.txt`
(local, fuera del repo).

## production_preflight — estado real

PASS: debug, secret_key, allowed_hosts, csrf, https_proxy, cookies,
hsts, engine Postgres, conexión, migraciones, estáticos, WhiteNoise,
storage S3, endpoint HTTPS, URLs firmadas, direcciones de email,
mantenimiento, abuse hash-only, worker, outbox, directorios,
variables, banco (ficticio), backups (salvo nota).

FAIL documentados (reales, no esquivados):
- `smtp` — sin SMTP real: el correo va a consola. Pendiente de
  credenciales SMTP de Sandro.

Resuelto:
- `backup_*` — la imagen ahora es `Dockerfile` propio con
  `postgresql-client-18` del repo PGDG (**pg_dump/pg_restore 18.7**
  contra servidor 18.6). El preflight ganó el check
  `postgres_server_tools_version` (cliente vs servidor) — detecta la
  incompatibilidad antes de ejecutar un backup.
- Restore drill en base desechable aislada (`restore_drill_*`, mismo
  servidor, **nunca sobre la activa**): dump nuevo 200,884 bytes →
  restore completo → 40/40 tablas y conteos idénticos
  (bookings 19, tickets 10, users 5, trips 4, payments 12) → base
  desechable eliminada. Respaldo protegido fuera del repo
  (`~/scviajes-backups/`).

`/health/live/` → 200 — **configurado como healthcheckPath del
servicio** en Railway (antes `null`; Railway no exigía endpoint).
`/health/ready/` → **503 por diseño**: `_production_configuration_ready()`
en `core/health.py` exige backend SMTP + `EMAIL_HOST`; sin SMTP real
queda not_ready. No se falsea.

## Aceptación ejecutada en la URL

| Ítem | Resultado |
|---|---|
| Login admin + vendedor + cash-vendedor | OK |
| Venta interna: reserva manual → comprobante titular → confirma → PDF | OK (`pasaje-AAA-000000011.pdf`) |
| Compra pública: retención → comprobante → admin aprueba → confirmada → PDF | OK (`pasaje-AAA-000000012.pdf`) |
| Ida/vuelta con menor + referencia de butaca por tramo | OK |
| Aislamiento vendedores (404 en reserva ajena; admin 200) | OK |
| Descargas protegidas sin sesión (302→login / 404) | OK |
| Reporte de ventas + CSV + listado pasajeros PDF | OK |
| Mantenimiento (expire de retenciones, corrió en vivo) | OK — worker residente `run_maintenance_worker` |
| Fulfillment (issue+email, 1 intento, SUCCEEDED) | OK en todas las confirmadas |
| Reinicio web → reservas, comprobantes y PDFs persisten | OK (PG volumen + bucket S3) — esta sesión (`railway service restart`, re-verificado) |

### Evidencia no verificable / atribución

- El recorrido de aceptación de la tabla anterior fue ejecutado en esta
  sesión con `railway_acceptance.py` (Playwright) — log local en
  `~/scviajes-e2e/` y PDFs en `~/scviajes-acceptance/evidencia/`.
- La sesión de ChatGPT reportó haber corrido `e2e/acceptance.spec.js`
  16/16 contra la URL y un reinicio de los 3 servicios, **pero según
  Joel esa sesión solo verificó infraestructura por el conector, sin
  ejecutar Playwright ni reinicios** → esos ítems quedan **no
  verificados**. Existe evidencia indirecta en la base (reservas
  `boarding-e2e-*`, `roundtrip-*`, `e2e-*` creadas el 26/11 ~22:21 UTC)
  sin log de ejecución identificable; podría ser de la otra sesión o de
  una corrida anterior. No se presenta como validación.
- El seed inicial del escenario demo (~22:20 UTC del 26/11) tampoco fue
  ejecutado por esta sesión; se asumió y rotaron las credenciales a
  valores nuevos generados.

Observaciones operativas verificadas en esta sesión:

- `MAINTENANCE_WORKER_LOCK_WAIT_SECONDS=120` vigente en el worker
  (configurado por la sesión paralela).
- Las rutas `MEDIA_ROOT`/`PROTECTED_MEDIA_ROOT`/`TICKETS_STORAGE_ROOT`
  deben setearse con cuidado en Git Bash: una conversión de path dejó
  `C:/Program Files/Git/...` en un intento previo; valores vigentes
  verificados (`/var/lib/scviajes/...`).
- `SCVIAJES_ENVIRONMENT=demo` + `DEMO_SCENARIO_ENABLED=true` habilitan
  el seeder del escenario ficticio (entorno de aceptación).
- Viajes semilla: los originales quedaron `COMPLETED`; se crearon dos
  `SCHEDULED` nuevos (9 y 12 dic) para la aceptación y la demo.

## Pendientes externos

- SMTP real (correo de pasajes) — sin él, `ready` queda 503 por diseño.
- Datos bancarios reales del comercio.
- Credenciales Mercado Pago y Payway del comercio (gateways off).
- Dominio propio (hoy `*.up.railway.app` de Railway).
