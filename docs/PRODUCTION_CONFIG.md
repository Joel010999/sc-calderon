# Configuración productiva

La aplicación usa variables de entorno para todo dato operativo que cambia por
entorno. `PUBLIC_BASE_URL` debe ser la URL HTTPS canónica de SC Viajes; los
enlaces de invitación del panel y los QR no usan el encabezado `Host` de la
petición.

En producción deben definirse `BANK_TRANSFER_ACCOUNT_HOLDER`,
`BANK_TRANSFER_ALIAS`, `BANK_TRANSFER_CVU`, `BANK_TRANSFER_CUIT` y
`BANK_TRANSFER_ENTITY`. No hay
valores bancarios ficticios por defecto y `production_preflight`/`check` fallan
si falta alguno.

La marca pública se puede ajustar con `SITE_BRAND_NAME`, `SITE_BRAND_TAGLINE`,
`SITE_SUPPORT_EMAIL` y `SITE_WHATSAPP_URL`; el valor predeterminado es SC Viajes.
Los secretos, credenciales OAuth y contraseñas SMTP no se guardan en el
repositorio.

Validación local sin base real:

```text
python manage.py check
python manage.py makemigrations --check --dry-run
python manage.py collectstatic --noinput
python manage.py production_preflight --json
npm audit --omit=optional
```
