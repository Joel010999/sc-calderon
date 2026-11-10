# Informe de Cierre de Pendientes: Navegación Operativa y Pasajes de Menores

**Fecha:** 6 de noviembre de 2026  
**Rama:** `Joel010999/pendientes-nav-menor-20261107`  
**Base commit:** `4574f01ee08bd02eaaa024104dd6bc4718b09a22`  
**Zona horaria:** `America/Argentina/Buenos_Aires`

---

## 1. Resumen de la Intervención

Se cerraron de manera estricta y quirúrgica los dos únicos pendientes asignados, sin alterar caja, embarque, pagos, vencimientos, comisiones ni gráficos de ventas:

1. **Consolidación de Navegación Operativa (Resumen Operaciones y Recorridos)**:
   - Se verificó y aseguró el retiro visual del resumen "Operaciones" en la barra lateral del panel administrativo (`templates/panel/base.html`).
   - La URL original `/panel/operaciones/` (`panel:operations`) redirige mediante HTTP 302 a `/panel/operaciones/viajes/` (`panel:trips`).
   - La barra lateral expone únicamente:
     - **Viajes**: accesible para `Administrador` y `Vendedor` (los vendedores sólo disponen de acceso en modo consulta, sin botones ni acciones de creación, modificación o gestión operativa).
     - **Colectivos**: reservado exclusivamente para administración bajo permiso `can_manage_operations`.
   - **Gestionar recorridos** vive orgánicamente dentro de "Viajes" (`templates/panel/operations/trips.html`) accesible mediante botón para administradores, sin enlace lateral independiente. Se incorporó en `templates/panel/operations/routes.html` el retorno contextual (`← Volver a viajes`).
   - Se validaron todos los endpoints HTTP, la creación, edición y versionado de recorridos (`Route`, `Stop`, `RouteStop`, paradas intermedias, secuencias y trazabilidad histórica).

2. **Pasaje de Menores Gratuitos con Butaca de Referencia del Acompañante**:
   - En la emisión de pasajes para menores de 5 años sin asiento propio (`is_lap_infant = True`), se incorporó al snapshot del modelo `Ticket` y al de renderizado (`TicketData.companion_seat_number` y `Ticket.companion_seat_reference`) la butaca asignada al adulto acompañante (`accompanied_by`) de manera específica para cada tramo (`leg`).
   - Se incorporó la migración `tickets.0008_ticket_companion_seat_reference`, añadiendo el campo `companion_seat_reference` con `blank=True, default=""` para compatibilidad total con registros históricos preexistentes sin necesidad de rellenarlos.
   - En **ambos talones** del pasaje PDF oficial de una sola página ("Talón superior" para agencia/empresa y "Talón inferior" para el pasajero), se imprime en el recuadro de asiento la leyenda exacta:
     ```
     Sin asiento propio. Butaca de referencia del acompañante: N
     ```
   - No se crean registros adicionales de asignación de butaca (`SeatAssignment`) para el menor en la base de datos (preservando el inventario físico y la disponibilidad del colectivo).
   - Se mantiene la inmutabilidad de los PDFs históricos: las recargas desde DB y reenvíos idempotentes no recalculan ni sobreescriben pasajes preexistentes.
   - En caso de faltar una asignación válida de butaca para el acompañante en el tramo correspondiente, el servicio rechaza la emisión con `InvalidTicketError` y registra advertencia (`logger.warning`) como evidencia de auditoría sin inventar referencias.
   - Se generaron y renderizaron muestras PDF sintéticas ida/vuelta fuera del repo (`C:\Users\JOEL\AppData\Local\Temp\scviajes_pendientes_20261107\muestras_pdf`), y el coordinador revisó visualmente las imágenes PNG a resolución original confirmando ambos talones, la leyenda completa y los números correctos por tramo (12 en ida y 25 en vuelta) sin recortes.

---

## 2. Informe Técnico: `test_postgres_concurrent_cash_payments_serialization`

Se solicitó una revisión técnica exhaustiva sobre `concurrent_cash_payments_serialization` en `payments/tests.py`:

- **Comportamiento en código (`payments/services.py:103`)**:
  ```python
  def register_cash_payment(*, booking, recorded_by, ...):
      raise ValidationError("El registro de nuevos pagos en efectivo ha sido deshabilitado.")
  ```
  La función interrumpe la ejecución en la primera línea mediante el lanzamiento inmediato de `ValidationError`, antes de abrir cualquier transacción de base de datos (`transaction.atomic`), ejecutar consultas o adquirir bloqueos de serialización (`select_for_update`).
- **Comportamiento de la prueba (`payments/tests.py`)**:
  El test lanza dos hilos concurrentes que invocan `register_cash_payment`. Ambas invocaciones son rechazadas al instante a nivel de aplicación por la regla de negocio que retiró el efectivo.
- **Dictamen**:
  La prueba **ya no evalúa concurrencia útil ni serialización en la base de datos** (PostgreSQL/SQLite). Simplemente comprueba que la operación retirada rechaza consistentemente a los llamantes concurrentes.
  De conformidad con las directivas, **no se reactivó el efectivo**, manteniéndose la coherencia con el retiro operativo de caja y pagos en efectivo.

---

## 3. Modificaciones Realizadas

### Frontend / Plantillas
- `templates/panel/operations/routes.html`:
  - Se agregó enlace de retorno contextual `<p><a href="{% url 'panel:trips' %}">← Volver a viajes</a></p>` al inicio del contenedor operativo, consolidando la experiencia de que la gestión de recorridos es un submódulo de Viajes.

### Modelo y Persistencia de Tickets
- `tickets/models.py`:
  - Campo `companion_seat_reference = models.CharField("butaca de referencia del acompañante", max_length=20, blank=True, default="")`.
- `tickets/migrations/0008_ticket_companion_seat_reference.py`:
  - Migración que incorpora el campo de forma no destructiva y compatible con históricos.
- `tickets/test_migration_historical.py`:
  - Prueba de migración sobre base temporal verificando reversión a 0007, inserción de registro histórico previo a 0008, migración forward a 0008 con campo en blanco por defecto, emisión de nuevo pasaje con butaca referenciada y rollback seguro.

### Lógica de Emisión y Renderizado de Pasajes
- `tickets/rendering/data.py`:
  - Se agregó el campo `companion_seat_number: str = ""` a la estructura `TicketData`.
- `tickets/rendering/template.py`:
  - Función `_resolve_seat_display`: cuando `data.is_lap_infant` es verdadero y cuenta con `companion_seat_number`, genera la cadena exacta:
    `f"Sin asiento propio. Butaca de referencia del acompañante: {companion_seat}"`.
  - Recuadro 3 (Talón superior - Asiento): ajuste dinámico mediante `_draw_text_box` multilínea para acomodar la leyenda completa de manera legible sin desbordar el casillero.
  - Recuadro 8 (Talón inferior - Asiento): ajuste dinámico mediante `_draw_text_box` multilínea con tipografía y espaciado calibrados.
- `tickets/services.py`:
  - En `issue_tickets_for_booking`: cuando un pasajero es `is_lap_infant`, localiza su acompañante (`accompanied_by`), busca la asignación de butaca del acompañante para el tramo actual (`leg`), valida su validez y asiento (`companion_assignment.seat.seat_number`), persiste `companion_seat_reference=companion_seat` en la instancia y en la base de datos, y pasa este valor a `TicketData(companion_seat_number=companion_seat)`.
  - Si no existe acompañante o el acompañante no tiene butaca confirmada en ese tramo, emite registro en logger con advertencia y lanza `InvalidTicketError`.
  - Se conserva `ticket.seat_number = None`, `ticket.seat_assignment = None`, precio $0,00 y categoría `LAP_INFANT`.

### Pruebas Automatizadas
- `tickets/test_minors.py`:
  - `test_lap_infant_under_five_gets_ticket_without_seat_in_both_legs_and_boards`: verifica viaje ida y vuelta con butacas distintas en ida (Butaca 1) y regreso (Butaca 3), confirma persistencia en base de datos mediante `refresh_from_db()`, comprueba ausencia de `SeatAssignment` propio, extrae el texto del PDF generado y confirma la presencia en ambos talones de la leyenda exacta `Sin asiento propio. Butaca de referencia del acompañante: 1` e `...: 3`. Verifica que no existan asignaciones adicionales (`SeatAssignment.count() == 2`).
  - `test_lap_infant_idempotent_reissue_does_not_alter_historical_pdf`: verifica que reemisiones posteriores, recargas desde DB y reenvíos de correo (`send_booking_tickets`) no recalculen ni modifiquen los bytes de los archivos PDF generados.
  - `test_lap_infant_fails_and_leaves_evidence_when_companion_lacks_valid_seat`: verifica que si el acompañante carece de butaca en el tramo, se registre advertencia en log y falle la emisión.
  - `test_lap_infant_fails_and_leaves_evidence_when_accompanied_by_missing`: verifica que ante la falta del acompañante se registre evidencia y falle la emisión.
- `panel/test_permisos_navegacion_20261104.py`:
  - Verificación estricta por rol de que ni la barra lateral ni el dashboard contienen enlaces o accesos independientes a Operaciones ni Recorridos, manteniendo Viajes y Colectivos para admin y Viajes en consulta para vendedores.
- `tickets/test_migration_historical.py`:
  - Cobertura completa de migración backward/forward sobre registros históricos.

---

## 4. Matriz de Validación y Evidencia

| Verificación | Comando | Resultado |
|---|---|---|
| **Linter / Estilo** | `python -m ruff check .` | **All checks passed! (0 errores)** |
| **Django System Check** | `$env:DEBUG="True"; python manage.py check` | **System check identified no issues (0 silenced).** |
| **Migraciones en seco** | `$env:DEBUG="True"; python manage.py makemigrations --check --dry-run` | **No changes detected (sin discrepancias de modelos).** |
| **Formato Git Diff** | `git diff --check` | **Limpio (sin conflictos ni espacios en blanco erróneos).** |
| **Pruebas Afectadas** | `python manage.py test panel.test_routes panel.test_permisos_navegacion_20261104 tickets.test_minors sales.test_minors panel.test_sales_charts_and_commissions tickets.test_migration_historical` | **49 tests OK.** |
| **Suite Completa** | `$env:DEBUG="True"; python manage.py test -v 2` | **Found 711 test(s). Ran 706 tests in 558.877s. OK (skipped=6).** |

- **Log completo de la suite:** Almacenado fuera del árbol de trabajo del repositorio en:
  `C:\Users\JOEL\AppData\Local\Temp\scviajes_pendientes_20261107\full_test_suite_output.log`.
- **Muestras PDF sintéticas ida/vuelta renderizadas:** Almacenadas fuera del repositorio en:
  `C:\Users\JOEL\AppData\Local\Temp\scviajes_pendientes_20261107\muestras_pdf`
  (revisadas y aprobadas visualmente por el coordinador).
- **Las 6 omisiones (skips) de la suite:**
  1. `operations.test_maintenance_worker.PostgreSQLMaintenanceWorkerLockTests` (`skipped 'requiere PostgreSQL'`)
  2. `payments.tests.PostgreSQLConcurrencyTests.test_postgres_concurrent_cash_payments_serialization` (`skipped 'Prueba específica de PostgreSQL (se ejecuta en CI).'`)
  3. `payments.tests.PostgreSQLConcurrencyTests.test_postgres_unique_active_payment_constraint_reports_specific_error` (`skipped 'Prueba específica de PostgreSQL (se ejecuta en CI).'`)
  4. `sales.tests.SalesPostgresConcurrencyTests` (`skipped 'Requiere PostgreSQL para probar concurrencia real y select_for_update; base actual: sqlite'`)
  5. `tickets.tests.TicketsPostgresDeadlockIsolationTests.test_postgres_fulfillment_claim_deadlock_isolation` (`skipped 'Prueba específica de PostgreSQL; base actual no es PostgreSQL.'`)
  6. `tickets.tests.TicketsPostgresConcurrencyTests.test_concurrent_issuance_threads_produce_exact_tickets` (`skipped 'Prueba específica para PostgreSQL.'`)
