# Despliegue en Railway — entorno de aceptación

Entorno de aceptación con datos ficticios, **sin operaciones económicas
reales**. Gateways (Mercado Pago, Payway) deshabilitados. No es una
habilitación productiva.

- **URL:** https://sc-calderon-production.up.railway.app
- **Código desplegado:** rama `feature/mercadopago-checkout-pro` —
  commit `15aca6e` (código idéntico al candidato validado `32ba087`;
  los commits posteriores son solo documentación).
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
- `backup_*` resuelto instalando `postgresql-client`, **pero**:
  pg_dump/pg_restore 17.12 en la imagen < servidor 18.6 → `pg_dump`
  aborta por *server version mismatch*. El preflight solo compara
  cliente-vs-cliente. **Ruta de backup vigente:** `pg_dump` 18.6
  dentro del servicio Postgres (vía `railway ssh`), verificado con
  dump custom válido (404 TOC entries) antes de migrar. Pendiente:
  imagen con cliente PG18 o revisar el alcance del check.

`/health/live/` → 200. `/health/ready/` → **503 por diseño**: la
readiness exige SMTP real (no se falsea).

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
| Reinicio web → reservas, comprobantes y PDFs persisten | OK (PG volumen + bucket S3) |
| `e2e/acceptance.spec.js` completo contra la URL | OK — 16/16 (sesión paralela) |
| Reinicio de los 3 servicios (Postgres incluido) | OK — datos conservados (sesión paralela) |

Observaciones de la verificación combinada:

- Si Postgres se reinicia, el worker termina con `InterfaceError` y
  Railway lo reinicia; retoma con `MAINTENANCE_WORKER_LOCK_WAIT_SECONDS=120`.
- Las rutas `MEDIA_ROOT`/`PROTECTED_MEDIA_ROOT`/`TICKETS_STORAGE_ROOT`
  deben setearse con cuidado en Git Bash: una conversión de path dejó
  `C:/Program Files/Git/...` en un intento previo; valores vigentes
  verificados (`/var/lib/scviajes/...`).
- `SCVIAJES_ENVIRONMENT=demo` + `DEMO_SCENARIO_ENABLED=true` habilitan
  el seeder del escenario ficticio (entorno de aceptación).
- Viajes semilla: los originales quedaron `COMPLETED` por la suite de
  la sesión paralela; se crearon dos `SCHEDULED` nuevos (9 y 12 dic)
  para la aceptación y la demo.

## Pendientes externos

- SMTP real (correo de pasajes) — sin él, `ready` queda 503 por diseño.
- Datos bancarios reales del comercio.
- Credenciales Mercado Pago y Payway del comercio (gateways off).
- Cliente pg_dump 18 en la imagen para restore drill in-container.
- Dominio propio (hoy `*.up.railway.app` de Railway).
