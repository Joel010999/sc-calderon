# Escenario demo sintetico

Recorrido local reproducible sin datos reales, proveedores externos, Railway,
AppSheet, Google Sheets, Flecha Bus, SMTP real ni S3 real. Las cuentas usan
`demo.scviajes.invalid`.

```powershell
$env:DEBUG = "True"
$env:EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"
python manage.py check
python manage.py seed_demo_scenario --dry-run
python manage.py seed_demo_scenario
```

No se guardan passwords en el repo: se aceptan `DEMO_ADMIN_PASSWORD`,
`DEMO_SELLER_PASSWORD` y `DEMO_CUSTOMER_PASSWORD`, o se generan aleatoriamente sin
imprimirlas. El seed es idempotente y usa el prefijo `DEMO-20261005`.

Incluye rutas en ambos sentidos, colectivos de dos plantas, cama/semicama, butaca
inhabilitada, viajes futuros, tarifas, roles, cliente, reservas HELD/confirmadas/
vencidas, pagos pendientes/en revision/rechazados, pasajes, fulfillment,
notificaciones, manifiesto, embarque, reportes, bandeja y clientes.

Recorrer busqueda publica, ida/ida y vuelta, disponibilidad, pasajeros, reserva,
transferencia y comprobante sintetico; aprobar en panel, revisar tickets, descarga y
Mis viajes. Crear reserva manual, efectivo, metricas, reporte, manifiesto y embarque;
comprobar segundo escaneo y reversión autorizada. Revisar bandeja/clientes sin
tokens, comprobantes ni PII innecesaria. Ejecutar mantenimiento con `--dry-run`.

Probar doble venta, butaca ocupada, corte online, venta manual posterior al inicio,
comprobante vencido, reserva expirada, IDOR, cliente en panel, vendedor sin permiso
indebido y duplicados. No existen bypasses demo ni Selenium.

Limpiar exclusivamente con `python manage.py seed_demo_scenario --reset`. El reset
usa prefijos y el dominio reservado, una colección explícita de Django y aborta ante
referencias protegidas ajenas; no borra filtros generales ni toca almacenamiento
productivo.

## Aceptación en navegador

La suite Playwright vive en `e2e/acceptance.spec.js` y usa únicamente Chromium
instalado por Playwright. En Windows PowerShell, desde la raíz del proyecto:

```powershell
$env:DEBUG = "True"
$env:SECRET_KEY = "e2e-local-only"
$env:ALLOWED_HOSTS = "127.0.0.1,localhost"
$env:DATABASE_URL = "sqlite:///e2e.sqlite3"
$env:EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"
$env:DEMO_ADMIN_PASSWORD = "e2e-only-admin-password-20261007"
$env:DEMO_SELLER_PASSWORD = "e2e-only-seller-password-20261007"
$env:DEMO_CUSTOMER_PASSWORD = "e2e-only-customer-password-20261007"
python -m pip install -r requirements.txt
npm ci
npx playwright install chromium
python manage.py migrate --noinput
python manage.py seed_demo_scenario
npm run test:e2e
```

La base `e2e.sqlite3` y las raíces de archivos privados son temporales y nunca
deben apuntar a producción. La suite cubre búsqueda y reserva como invitado,
inicio de transferencia pública, acceso Vendedor a caja/embarque, restricción de
auditoría por rol y navegación Administrador por reservas y fulfillment. QR
público, descarga autenticada con token opaco, aprobación de transferencia y
reversión de embarque no se fuerzan desde navegador porque el seed no expone
tokens ni se deben crear bypasses; continúan cubiertos por tests Django. Los
artefactos de Playwright se conservan sólo cuando falla la ejecución.
