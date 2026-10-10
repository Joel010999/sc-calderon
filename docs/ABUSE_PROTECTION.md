# Protección distribuida contra abuso

Los límites públicos se persisten en `core.AbuseCounter`, con ventana, alcance e
identificador HMAC-SHA256; nunca se guarda IP, sesión, token ni otro identificador
en claro. `select_for_update()` serializa el incremento en PostgreSQL (SQLite sólo
se usa en tests/desarrollo). `run_operational_maintenance --task abuse` elimina
ventanas vencidas por lotes.

La verificación QR GET conserva su semántica de lectura: el único write técnico es
el contador antiabuso, sin cambiar dominio ni auditorías. Los límites de checkout,
transferencia y verificación, además de retención y tamaño de limpieza, se configuran
por variables `ABUSE_*`.

`ABUSE_HASH_SECRET` es una clave dedicada y rotatable. En desarrollo, si está vacía,
el código usa `SECRET_KEY` como fallback; `production_preflight` exige una clave
dedicada. Al rotarla se invalidan las ventanas activas de forma controlada y no se
persiste la clave anterior.

La IP se toma de `REMOTE_ADDR`. `X-Forwarded-For` sólo se considera cuando la IP
inmediata pertenece a `ABUSE_TRUSTED_PROXY_IPS`; los encabezados enviados por clientes
directos se ignoran. Los límites cubren checkout, transferencias, verificación QR,
login, registro, recuperación, OAuth, reclamos, comprobantes, embarque e invitaciones.
La limpieza se ejecuta en lotes acotados mediante `run_operational_maintenance`.
PostgreSQL serializa cada contador con `select_for_update` y una restricción única;
SQLite se usa sólo para desarrollo/tests y no ofrece garantías equivalentes entre
procesos.

Configuración disponible: `ABUSE_HASH_SECRET`, `ABUSE_TRUSTED_PROXY_IPS`,
`ABUSE_VERIFY_LIMIT`, `ABUSE_VERIFY_WINDOW_SECONDS`, `ABUSE_TRANSFER_LIMIT`,
`ABUSE_TRANSFER_WINDOW_SECONDS`, `ABUSE_CHECKOUT_LIMIT`,
`ABUSE_CHECKOUT_WINDOW_SECONDS`, `ABUSE_LOGIN_LIMIT`, `ABUSE_LOGIN_WINDOW_SECONDS`,
`ABUSE_REGISTER_LIMIT`, `ABUSE_REGISTER_WINDOW_SECONDS`, `ABUSE_PASSWORD_RESET_LIMIT`,
`ABUSE_PASSWORD_RESET_WINDOW_SECONDS`, `ABUSE_OAUTH_LIMIT`,
`ABUSE_OAUTH_WINDOW_SECONDS`, `ABUSE_CLAIM_LIMIT`, `ABUSE_CLAIM_WINDOW_SECONDS`,
`ABUSE_VOUCHER_LIMIT`, `ABUSE_VOUCHER_WINDOW_SECONDS`, `ABUSE_BOARDING_LIMIT`,
`ABUSE_BOARDING_WINDOW_SECONDS`, `ABUSE_STAFF_INVITATION_LIMIT`,
`ABUSE_STAFF_INVITATION_WINDOW_SECONDS`, `ABUSE_COUNTER_RETENTION_SECONDS` y
`ABUSE_COUNTER_CLEANUP_LIMIT`. Los defaults son conservadores para desarrollo/tests;
producción debe definirlos explícitamente junto con la clave HMAC dedicada.
