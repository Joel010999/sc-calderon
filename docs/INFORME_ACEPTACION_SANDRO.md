# Informe de aceptación — reunión con Sandro (viernes 27/11)

Candidato: rama `feature/mercadopago-checkout-pro` (PR #51, borrador).
Este documento separa **lo demostrable** de **lo pendiente para producción**.
No constituye habilitación productiva.

## Evidencia de CI reutilizada

Último push con el código funcional completo: `fa4dd82` — 4 workflows
verdes, incluida la suite completa **844 tests OK / 1 omitida** sobre
`postgres:16` (PostgreSQL 16.16) en `sales-postgres` y `tickets-postgres`.
El omitido es la prueba visual de gráficos con Chrome, que corre dentro
del workflow `browser-e2e`. Los cambios de este paquete (workflow
browser-e2e a PostgreSQL, script y docs de demo) no modifican lógica de
negocio; el push de este documento (`43615a7` + actualización de este
informe) volvió a correr los mismos workflows — **los 4 en verde** —
y ahora el recorrido de navegador también corre sobre `postgres:16`
efímero: el `runserver` de Playwright hereda `DATABASE_URL` y el job
verificó en el log `Motor E2E verificado: postgresql - scviajes_e2e`.

Post-ensayo (`11a39fb` + `4faa103` + ajustes E2E): se corrigió
el checkout ida/vuelta (radios del buscador) y se agregó el recorrido
público ida/vuelta con menor a `acceptance.spec.js`. Dos defectos más
que el nuevo test expuso en CI: (1) `ABUSE_CHECKOUT_LIMIT=3` por IP
estaba al borde con las 3 retenciones existentes — se elevó a 10 **solo
en el entorno del workflow E2E**, sin tocar la protección productiva;
(2) `seed_demo_scenario --reset` fallaba con `ProtectedError` al borrar
reservas con menor (`accompanied_by` es FK PROTECT a sí mismo) — se
corrige cortando la referencia antes del delete, con regresión
`test_reset_deletes_bookings_with_accompanied_minors`. Spec completo
de 16 tests verificado localmente.

**SHA del código validado: `eae97ca`** — 4 workflows en verde:
suite completa **846 tests OK / 1 omitida** sobre `postgres:16`
(sales y tickets), browser E2E 16/16 sobre `postgres:16` con
`Motor E2E verificado: postgresql` en el log, quality gate.
La omitida es la prueba visual de gráficos con Chrome (corre dentro
de browser-e2e). Esta versión queda **congelada** para la reunión.

Sandbox Payway (evidencia real, separada de los mocks de CI): aprobado
Visa crédito y débito, rechazado Visa (HTTP 402 con cuerpo de pago),
reintento tras rechazo, conciliación `GET /payments` — todo verificado
en `fa4dd82`. Mastercard del sandbox **no** se valida (error interno
`auth-3ds-svc` del entorno de Payway). WAF de Payway bloquea navegadores
headless: el E2E de Payway corrió con Chrome real, no en CI.

## Matriz de aceptación por flujo

| Flujo | Estado | Evidencia |
|---|---|---|
| Búsqueda, paradas permitidas, disponibilidad por tramo, categorías | Probado | Tests de búsqueda/checkout; browser E2E (búsqueda invitado, ahora en PG16) |
| Ida e ida/vuelta, múltiples pasajeros, menor sin asiento | Probado (defecto corregido) | `tickets/test_minors.py`, checkout tests, seed demo ida/vuelta. **Defecto encontrado en el ensayo de reunión:** el checkout descartaba `return_trip_radio` (los parámetros reales que envía el buscador) y la compra ida/vuelta pública redirigía a `/buscar/`; corregido en `core/views.py` sin debilitar validaciones de regreso, con regresión `test_checkout_roundtrip_accepts_radio_params_from_search` y recorrido público ida/vuelta con menor agregado a `e2e/acceptance.spec.js` (corre sobre `postgres:16` en CI) |
| Retención, vencimiento, liberación por mantenimiento | Probado | `operations/test_maintenance_command.py` + `test_maintenance_worker.py` (expira HELD sin pago, preserva pagos en revisión, advisory lock PG) |
| Venta interna: comprobante del titular confirma; otro vendedor no accede; sin datos bancarios del negocio | Probado | `payments/test_transfer_postgres.py` (owner confirma con voucher, no-owner queda en revisión), browser E2E panel vendedor |
| Compra web: transferencia en revisión; aprobación/rechazo solo admin/superusuario | Probado | browser E2E (upload público → revisión → aprobar/rechazar), `test_transfer_postgres.py` concurrencia admin |
| Payway: aprobado, rechazado, reintento, conciliación | Probado (sandbox + regresiones) | Sandbox real `fa4dd82`; `payments/test_payway.py` (46+ casos, incl. concurrencia PG-only); Payway **deshabilitado** por defecto |
| Emisión, recuperación de errores, correo, descarga protegida | Probado | `tickets/test_fulfillment_integration.py` (fallo PDF→reconciliar, fallo email recuperable, descarga invitado sin IDOR), browser E2E descarga PDF real |
| PDFs individuales y listado provisional; históricos preservados | Probado | Tests de emisión/listado; reemisión no altera PDF histórico |
| Viajes, recorridos versionados, tarifas, permisos por rol | Probado | `operations` tests (versionado de rutas, tarifas por salida), permisos de panel en E2E y tests |
| Comisiones, reportes, CSV, aislamiento por vendedor | Probado | `panel` tests de reportes/comisiones; E2E aislamiento de sesión vendedor |
| Butacas 51/54: límite exacto 48 h sin afectar reservas válidas | Probado | `core/test_priority_seats.py` (POST manipulado no crea reserva antes de liberación; POST en el instante de liberación sí) |
| Caja/efectivo y embarque retirados | Probado | Browser E2E verifica 404 en rutas de caja y embarque |
| Formularios y navegación escritorio/móvil | Probado | `e2e/accessibility.spec.js` (axe) + viewport móvil en E2E; capturas en evidencia |

## Pruebas operativas solicitadas — revisión de brechas

Todos los ítems ya tienen cobertura; no se detectaron defectos nuevos:

- Reserva que vence durante el pago → `test_payway.py`
  (CONFLICT_LATE_APPROVAL con `expires_at` mutado durante el POST) y
  reglas de expiración en checkout/transferencia.
- Doble clic y concurrencia → `test_transfer_postgres.py` (aprobación
  por dos admins, doble upload del titular, ventas por misma butaca,
  fulfillment concurrente emite una sola vez) y `test_payway.py`
  clase concurrencia (PostgreSQL-only).
- Confirmación/fulfillment repetidos sin duplicados → fulfillment
  idempotente + reconciliación.
- Reinicio/recuperación de mantenimiento y fulfillment → reconciliación
  recupera tras fallo de PDF/email; worker con advisory lock.
- Comprobante que falla al guardar → 4 tests: ningún archivo huérfano
  tras rollback (registro, confirmación, upload público).
- Permisos por URL, POST y servicio → E2E + tests de vistas/servicios.
- Archivos privados sin autorización → storage privado, descarga
  protegida, rechazo IDOR.
- Secretos en logs → `test_issue_failure_is_logged_without_personal_data`,
  `test_request_logging_contains_id_without_query_secret`, métricas
  sanitizadas del worker.

Plan de migraciones `main`→candidato: 5 migraciones nuevas
(`operations.0004`, `panel.0004`, `payments.0003`, `payments.0004`,
`tickets.0008`), lineales, sin conflictos de numeración; verificadas
aplicando desde cero en la base demo. `restore-drill` existe y cubre
archivos privados + checksums; no se amplió porque no hay brecha
demostrada (corre por su trigger cuando cambian migraciones).

## Listo para demostrar vs pendiente para producir

**Listo para demostrar (viernes):** búsqueda y compra web con
transferencia, venta interna, panel admin/vendedor, aprobación y
rechazo, emisión y descarga de pasaje, reportes/CSV, listados, permisos,
vencimiento. Base demo nueva con datos ficticios (SQLite declarado).

**Pendiente para producción (no tocar el viernes):** despliegue real,
PostgreSQL gestionada, storage privado (S3), correo transaccional real,
Mercado Pago productivo, Payway productivo, Google/Apple con
proveedores reales, dominio/HTTPS, backups operativos, datos bancarios
reales.

## Decisiones pendientes — preguntas concretas para Sandro

1. ¿Qué medio de pago sale primero en producción: transferencia manual,
   Mercado Pago o Payway?
2. ¿Payway sigue como opción o se descarta? Si sigue: ¿qué medios
   (crédito/débito, qué redes) y cuotas?
3. ¿Política de devoluciones/cambios? (hoy no implementada; no se
   inventó ninguna).
4. ¿Menores: confirmar la regla vigente (menor sin asiento acompañado).
5. ¿Datos fiscales/facturación requeridos en el pasaje o reportes?
6. ¿Caja/efectivo queda definitivamente fuera o vuelve en otra etapa?
7. ¿Embarque/check-in queda fuera o se retoma luego?
8. ¿Hosting e infraestructura definitivos (reemplazo de Railway)?

## Accesos y datos que Sandro debe proporcionar

- **Mercado Pago:** credenciales productivas del comercio, URL pública
  de webhook, medios/cuotas habilitados.
- **Payway (si sigue):** credenciales propias sandbox + producción del
  comercio, `payment_method_id` de sus medios, cuotas, política de
  devoluciones, vertical de fraude Cybersource, coexistencia con MP.
- **Correo real:** proveedor transaccional (SMTP o API) y remitente
  verificado — hoy solo `locmem`.
- **Google/Apple login:** client ID/secret reales de ambos proveedores —
  hoy solo auditoría ficticia de Apple; Google no validado.
- **Datos bancarios** para transferencias: titular, alias, CVU/CBU, CUIT.
- **Datos operativos reales:** recorridos, paradas, colectivos/butacas,
  tarifas, horarios.
