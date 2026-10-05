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
