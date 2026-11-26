#!/usr/bin/env bash
# Arranque reproducible de la demo para la reunion con Sandro.
#
# Motor: SQLite en archivo nuevo fuera del repositorio (demostracion de UI).
# La aceptacion sobre PostgreSQL 16 queda respaldada por los workflows de CI
# (sales-postgres, tickets-postgres y browser-e2e con servicio postgres:16).
#
# Uso:
#   bash scripts/demo_sandro.sh            # prepara y levanta el servidor
#   SCVIAJES_DEMO_RESET=1 bash scripts/demo_sandro.sh   # recrea la base desde cero
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
DEMO_DB="$DEMO_HOME/demo.sqlite3"

if [ "${SCVIAJES_DEMO_RESET:-0}" = "1" ]; then
    rm -rf "$DEMO_HOME"
fi
mkdir -p "$DEMO_HOME/media" "$DEMO_HOME/protected" "$DEMO_HOME/tickets" "$DEMO_HOME/secrets"

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
export DEMO_ADMIN_PASSWORD="${DEMO_ADMIN_PASSWORD:-demo-admin-2026}"
export DEMO_SELLER_PASSWORD="${DEMO_SELLER_PASSWORD:-demo-vendedor-2026}"
export DEMO_CASH_SELLER_PASSWORD="${DEMO_CASH_SELLER_PASSWORD:-demo-caja-2026}"
export DEMO_CUSTOMER_PASSWORD="${DEMO_CUSTOMER_PASSWORD:-demo-cliente-2026}"
export BANK_TRANSFER_ACCOUNT_HOLDER="${BANK_TRANSFER_ACCOUNT_HOLDER:-SC Viajes (demo ficticio)}"
export BANK_TRANSFER_ALIAS="${BANK_TRANSFER_ALIAS:-scviajes.demo.ficticio}"
export BANK_TRANSFER_CVU="${BANK_TRANSFER_CVU:-0000000000000000000001}"
export BANK_TRANSFER_CUIT="${BANK_TRANSFER_CUIT:-20-00000000-1}"
export BANK_TRANSFER_ENTITY="${BANK_TRANSFER_ENTITY:-Entidad ficticia (demo)}"

python manage.py migrate --noinput
python manage.py seed_demo_scenario
python e2e/prepare_browser_scenario.py

python manage.py shell -c "from django.db import connection; print('Motor de la demo:', connection.vendor, '-', connection.settings_dict['NAME'])"

echo ""
echo "============================================================"
echo " Demo lista en http://127.0.0.1:8000/"
echo " Base: $DEMO_DB (SQLite, datos ficticios)"
echo " Usuarios (grupo):"
echo "   demo-20261005-admin          (Administrador)  $DEMO_ADMIN_PASSWORD"
echo "   demo-20261005-vendedor       (Vendedor)       $DEMO_SELLER_PASSWORD"
echo "   demo-20261005-cash-vendedor  (Vendedor)       $DEMO_CASH_SELLER_PASSWORD"
echo " Cliente web: cliente@demo.scviajes.invalid / $DEMO_CUSTOMER_PASSWORD"
echo " Apagado: Ctrl+C. Reset total: SCVIAJES_DEMO_RESET=1"
echo "============================================================"
echo ""

python manage.py runserver 127.0.0.1:8000 --noreload
