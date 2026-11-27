#!/usr/bin/env bash
# Arranque reproducible de la demo para la reunion con Sandro.
#
# Motor: SQLite en archivo nuevo fuera del repositorio (demostracion de UI).
# La aceptacion sobre PostgreSQL 16 queda respaldada por los workflows de CI
# (sales-postgres, tickets-postgres y browser-e2e con servicio postgres:16).
#
# Endurecimiento: el script deshabilita SIEMPRE Mercado Pago, Payway y los
# proveedores OAuth, y limpia variables de entorno que podrian heredar
# configuracion productiva (tokens, S3, SMTP, URLs publicas). Los datos
# bancarios mostrados son exclusivamente ficticios de demo.
#
# Uso:
#   bash scripts/demo_sandro.sh            # prepara y levanta el servidor
#   SCVIAJES_DEMO_RESET=1 bash scripts/demo_sandro.sh   # recrea la base desde cero
#
# Reset vs borrado: SCVIAJES_DEMO_RESET=1 solo recrea la base de datos.
# Para un borrado completo (base, media, pasajes) eliminar el directorio
# $SCVIAJES_DEMO_HOME manualmente. Las evidencias NO viven dentro del
# directorio de demo: se preservan en $SCVIAJES_DEMO_EVIDENCIA
# (por defecto ~/scviajes-demo-evidencia), separadas del escenario.
#
# Apagado: Ctrl+C en la terminal donde corre runserver. No quedan procesos.
set -euo pipefail

cd "$(dirname "$0")/.."

for candidate in .venv/Scripts/activate .venv/bin/activate venv/Scripts/activate venv/bin/activate; do
    if [ -f "$candidate" ]; then
        source "$candidate"
        break
    fi
done

DEMO_HOME="${SCVIAJES_DEMO_HOME:-$HOME/scviajes-demo-sandro}"
EVIDENCIA="${SCVIAJES_DEMO_EVIDENCIA:-$HOME/scviajes-demo-evidencia}"

if [ "${SCVIAJES_DEMO_RESET:-0}" = "1" ]; then
    echo "Reset solo de base de datos (evidencias preservadas fuera del directorio demo)."
    rm -rf "$DEMO_HOME"
fi
mkdir -p "$DEMO_HOME/media" "$DEMO_HOME/protected" "$DEMO_HOME/tickets" "$DEMO_HOME/secrets" "$EVIDENCIA"

# --- Limpieza de configuracion heredable (nunca usar datos productivos) ---
for var in \
    MERCADO_PAGO_ENABLED MERCADO_PAGO_ACCESS_TOKEN MERCADO_PAGO_WEBHOOK_SECRET \
    MERCADO_PAGO_COLLECTOR_ID MERCADO_PAGO_API_BASE \
    PAYWAY_ENABLED PAYWAY_PUBLIC_API_KEY PAYWAY_PRIVATE_API_KEY PAYWAY_API_BASE \
    PAYWAY_SANDBOX PAYWAY_SCRIPT_URL PAYWAY_PAYMENT_METHODS \
    GOOGLE_OAUTH_CLIENT_ID GOOGLE_OAUTH_CLIENT_SECRET GOOGLE_OAUTH_SIMULATION_ENABLED \
    APPLE_OIDC_CLIENT_ID APPLE_OIDC_TEAM_ID APPLE_OIDC_KEY_ID APPLE_OIDC_PRIVATE_KEY \
    APPLE_OIDC_ENABLED APPLE_OIDC_SIMULATION_ENABLED \
    EMAIL_HOST EMAIL_HOST_USER EMAIL_HOST_PASSWORD EMAIL_PORT \
    SENDGRID_API_KEY MAILGUN_API_KEY \
    PRIVATE_STORAGE_S3_BUCKET PRIVATE_STORAGE_S3_ENDPOINT_URL \
    PRIVATE_STORAGE_S3_ACCESS_KEY PRIVATE_STORAGE_S3_SECRET_KEY \
    AWS_ACCESS_KEY_ID AWS_SECRET_ACCESS_KEY \
    PUBLIC_BASE_URL TICKETS_VERIFICATION_BASE_URL \
    SENTRY_DSN SENTRY_ENVIRONMENT; do
    unset "$var" || true
done

export DEBUG=True
export SCVIAJES_ENVIRONMENT=demo
export DEMO_SCENARIO_ENABLED=True
export SECRET_KEY="demo-sandro-not-for-production"
export ALLOWED_HOSTS="127.0.0.1,localhost"
DEMO_HOME_NATIVE="$DEMO_HOME"
if command -v cygpath >/dev/null 2>&1; then
    DEMO_HOME_NATIVE="$(cygpath -m "$DEMO_HOME")"
fi
export DATABASE_URL="sqlite:///$DEMO_HOME_NATIVE/demo.sqlite3"
export EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend"
export MEDIA_ROOT="$DEMO_HOME_NATIVE/media"
export PROTECTED_MEDIA_ROOT="$DEMO_HOME_NATIVE/protected"
export TICKETS_STORAGE_ROOT="$DEMO_HOME_NATIVE/tickets"
export PRIVATE_STORAGE_BACKEND="filesystem"
export E2E_SECRET_FILE="$DEMO_HOME_NATIVE/secrets/browser-secret.json"
export MERCADO_PAGO_ENABLED=False
export PAYWAY_ENABLED=False
export DEMO_ADMIN_PASSWORD="${DEMO_ADMIN_PASSWORD:-demo-admin-2026}"
export DEMO_SELLER_PASSWORD="${DEMO_SELLER_PASSWORD:-demo-vendedor-2026}"
export DEMO_CASH_SELLER_PASSWORD="${DEMO_CASH_SELLER_PASSWORD:-demo-caja-2026}"
export DEMO_CUSTOMER_PASSWORD="${DEMO_CUSTOMER_PASSWORD:-demo-cliente-2026}"

# Datos bancarios EXCLUSIVAMENTE ficticios: ninguno de estos valores es un
# dato real del negocio. Pasan validate_bank_transfer_configuration() para
# que la opcion de transferencia se muestre en la demo.
export BANK_TRANSFER_ACCOUNT_HOLDER="SC Viajes Demo Ficticio"
export BANK_TRANSFER_ALIAS="scviajes.demo.ficticio"
export BANK_TRANSFER_CVU="0000000000000000000001"
export BANK_TRANSFER_CUIT="20-00000000-1"
export BANK_TRANSFER_ENTITY="Entidad ficticia (demo)"

python manage.py migrate --noinput
python manage.py seed_demo_scenario
python e2e/prepare_browser_scenario.py

python manage.py shell -c "
from django.db import connection
from django.conf import settings
from core.site_config import validate_bank_transfer_configuration
assert connection.vendor == 'sqlite'
errors = validate_bank_transfer_configuration()
assert not errors, f'Datos bancarios demo invalidos: {errors}'
assert not settings.MERCADO_PAGO_ENABLED, 'Mercado Pago debe estar deshabilitado'
assert not settings.PAYWAY_ENABLED, 'Payway debe estar deshabilitado'
print('Motor de la demo:', connection.vendor, '-', connection.settings_dict['NAME'])
print('Gateways: MP=%s Payway=%s | datos bancarios ficticios OK' % (settings.MERCADO_PAGO_ENABLED, settings.PAYWAY_ENABLED))
"

echo ""
echo "============================================================"
echo " Demo lista en http://127.0.0.1:8000/  (solo loopback)"
echo " Base: $DEMO_HOME/demo.sqlite3 (SQLite, datos ficticios)"
echo " Usuarios (grupo):"
echo "   demo-20261005-admin          (Administrador)  $DEMO_ADMIN_PASSWORD"
echo "   demo-20261005-vendedor       (Vendedor)       $DEMO_SELLER_PASSWORD"
echo "   demo-20261005-cash-vendedor  (Vendedor)       $DEMO_CASH_SELLER_PASSWORD"
echo " Cliente web: cliente@demo.scviajes.invalid / $DEMO_CUSTOMER_PASSWORD"
echo " Apagado: Ctrl+C. Reset base: SCVIAJES_DEMO_RESET=1"
echo "============================================================"
echo ""

python manage.py runserver 127.0.0.1:8000 --noreload
