# Checkpoint — despliegue Railway en curso

Estado al momento del traspaso a sesión cloud. Sin secretos ni credenciales.

## Código

- Repo: `Joel010999/sc-calderon` (app Django SC Viajes / `sslviajes`).
- Rama local `Joel010999/mercadopago-checkout-pro` → remota
  `feature/mercadopago-checkout-pro`, PR #51 (draft).
- **Candidato a desplegar: `32ba087`** (code-frozen para la demo; este
  checkpoint solo agrega docs). CI en verde en ese SHA: Quality gate,
  Sales PostgreSQL (846 tests, 1 skip), Tickets PostgreSQL, Browser E2E
  (16/16 sobre `postgres:16` efímero).
- Sin merge a `main`, sin force-push, sin PR #50.

## Estado Railway (nada creado todavía)

- Railway CLI instalado localmente (`@railway/cli` 5.x). **Sin
  autenticación persistida**: `railway whoami` → Unauthorized.
- Proceso de login previo cancelado (callback local, inutilizable desde
  otra máquina). **Ningún proyecto, servicio, base, bucket ni volumen fue
  creado o modificado — Railway está intacto.**
- No hay migraciones ni despliegues en curso: nada que detener ni
  esperar.

## Plan acordado (autorizado por Joel)

Objetivo: URL HTTPS funcional del candidato en Railway, entorno de
**aceptación** (datos ficticios, sin cobros reales, gateways off).

Arquitectura (detalle completo en `docs/DEPLOY_RAILWAY.md`):

1. `railway login --browserless` (desde iPhone: abrir la URL de
   activación e ingresar el código que muestra el CLI).
2. `railway link` / inventario del proyecto existente — reutilizar lo
   compatible, no duplicar.
3. PostgreSQL 16: si existe base, verificar versión/esquema y respaldo
   antes de migrar (NO vaciar); si no, crear y usar `DATABASE_URL`
   privada. Aplicar migraciones del candidato (5 nuevas desde `main`,
   lineales). Confirmar motor desde Django (`connection.vendor`).
4. Storage privado: **bucket S3 de Railway** (`PRIVATE_STORAGE_BACKEND=s3`)
   — los volúmenes no se comparten web↔worker y el check `tickets.E008`
   rechaza filesystem en producción. `core/private_storage.py` ya soporta
   S3 (django-storages en requirements). Fallback autorizado: volumen,
   solo si bucket no está disponible — requiere resolver arquitectura
   (worker no comparte volumen) antes de improvisar.
5. Servicios: `web` (`gunicorn sslviajes.wsgi --bind 0.0.0.0:$PORT`,
   Procfile) + `worker` (`python manage.py run_maintenance_worker`)
   desde la misma imagen del repo.
6. Variables clave (sin valores acá): `DEBUG=False`, `SECRET_KEY` y
   `ABUSE_HASH_SECRET` **generadas nuevas al setearlas** (no imprimir),
   `ALLOWED_HOSTS`, `CSRF_TRUSTED_ORIGINS`, `PUBLIC_BASE_URL` con el
   dominio Railway HTTPS, `DATABASE_URL` por referencia, gateways off,
   `EMAIL_BACKEND` console declarado pendiente, `BANK_TRANSFER_*`
   ficticios declarados, `MEDIA_ROOT`/`PROTECTED_MEDIA_ROOT`/
   `TICKETS_STORAGE_ROOT` fuera del repo, `SITE_BRAND_TAGLINE` marcando
   "ENTORNO DE ACEPTACIÓN — DATOS FICTICIOS".
7. Credenciales demo NUEVAS para admin + 2 vendedores (las del repo no
   se publican ni reutilizan). Seeder existente: `seed_demo_scenario`.
8. `collectstatic` (manifest WhiteNoise), health `/health/live/` y
   `/health/ready/`, `production_preflight` documentando FAILs reales
   esperados (SMTP real, datos bancarios reales).
9. Aceptación en la URL: logins (admin+2 vendedores), venta interna
   con comprobante→confirma→PDF, compra pública→revisión→aprobación→PDF,
   ida/vuelta con menor, aislamiento vendedores, descargas protegidas,
   reportes, listado pasajeros, fulfillment tras reinicio, persistencia
   tras redeploy. **Jamás suites que reinicialicen la base** (las pruebas
   automáticas usan bases separadas en CI).

## Accesos necesarios

- `railway login` interactivo (browser) — el único bloqueo actual.
- Credenciales demo nuevas decidirlas/generarlas en la sesión cloud
  (no transportar las del repo).

## Concurrencia

- Esta sesión local no mantiene procesos Railway. Para evitar doble
  operación: la sesión cloud es la única autorizada a tocar Railway.
- Windows local dejó corriendo únicamente la demo SQLite en
  `127.0.0.1:8000` (irrelevante para Railway; apagable sin efecto).
- Assets locales NO transferidos (fuera del repo): `~/scviajes-e2e/`
  (scripts Playwright), `~/scviajes-demo-evidencia/` (capturas/PDFs),
  `~/scviajes-demo-sandro/` (base demo), helpers en `%TEMP%`. El cloud
  los regenera si los necesita; la suite `e2e/acceptance.spec.js` sí
  está en el repo.

## Reporte esperado al volver

URL HTTPS + SHA desplegado, servicios/motor/versión PG, persistencia y
worker verificados, resultados de aceptación, acceso seguro, pendientes
externos (SMTP real, datos bancarios, credenciales MP/Payway del
comercio). Actualizar `docs/DEPLOY_RAILWAY.md` con el despliegue
efectivo y `docs/STATUS.md`.
