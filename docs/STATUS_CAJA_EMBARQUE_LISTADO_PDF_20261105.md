# Estado de implementación: Retiro de Caja, Control de Embarque y Listado Provisional en PDF

**Fecha:** 5 de noviembre de 2026
**Rama:** `Joel010999/scviajes-caja-embarque-pdf-20261105`
**Entorno de ejecución:** Worktree aislado sobre `ce379797b2ca1ac6f00e2afe96bffda11c8c9bf6`
**Destinatario / Coordinador:** Codex (`orca_session_id:codex_ab3eccb2_b2d2_4c1c_b6db_fd0379978917`)
**ID de Tarea:** `task_f9618d3d9238`

---

## 1. Resumen Ejecutivo

En cumplimiento de las directivas operativas para SC Viajes, se completó la desconexión operativa de Caja y Efectivo, la deshabilitación del Control de Embarque y la implementación del Listado Provisional de Pasajeros en formato PDF descargable para administración:

1. **Retiro operativo de Caja y Efectivo**:
   - Se removieron los accesos de menú, pantallas y endpoints `/panel/caja/` y `/panel/caja/reporte-diario/` (retornan 404).
   - Se inhabilitaron las operaciones de apertura (`open_cash_session`), cierre (`close_cash_session`), arqueo/ajuste (`create_manual_movement`) y registro de cobros en efectivo (`register_cash_payment`), respondiendo con `ValidationError` ante intentos vía POST o llamadas programáticas.
   - Se retiraron las opciones de efectivo en la interfaz de venta interna y confirmación de pagos.
   - Se preservaron íntegramente los modelos `CashSession` y `CashMovement`, sus migraciones aplicadas, los registros contables existentes y la trazabilidad de auditoría (`AuditEvent`). Los pagos históricos en efectivo permanecen legibles para balances globales y auditoría.
   - El circuito de transferencias bancarias, revisión administrativa, comprobantes, vencimientos y fulfillment automático de pasajes continúa plenamente operativo.

2. **Retiro de Control de Embarque**:
   - Se retiró la acción "Validar embarque", enlaces de navegación y endpoints `/panel/embarques/` (retornan 404).
   - Las operaciones de validación y reversión (`tickets.boarding.validate_boarding`, `reverse_boarding`) levantan `ValidationError` informando que el servicio se encuentra deshabilitado.
   - Se removieron de todas las vistas operativas activas (tablas de viaje, manifiestos de pasajeros) los contadores de "Embarcados / Pendientes de embarque" y las insignias de estado de embarque.
   - Se preservó la base histórica de `tickets.BoardingRecord` y `TicketAuditEvent`, los permisos `can_board` / `can_alight` en paradas intermedias, y el flujo de inicio/finalización de viajes para administradores y superusuarios sin requerir control de embarque.
   - Se preservó el diseño de los pasajes individuales (PDF de 2 talones, numeración serie `AAA`, sin códigos visuales).

3. **Listado Provisional de Pasajeros en PDF**:
   - Implementado en `/panel/operaciones/viajes/<trip_pk>/listado-pasajeros.pdf` y accesible mediante el botón "Descargar listado de pasajeros" en el detalle operativo del viaje.
   - **Autorización estricta**: Exclusivo para usuarios del grupo `Administrador` y superusuarios. Los vendedores no tienen botón en la interfaz ni acceso por URL directa (403 Forbidden).
   - **Formato y paginación**: Generado mediante ReportLab en hoja A4 apaisada (landscape), con `NumberedCanvas` en dos pasadas que calcula el total de páginas ("Página X de Y"), encabezado de tabla repetido en cada salto de página (`repeatRows=1`) y encabezado superior a partir de la página 2.
   - **Filtros de reservas y deduplicación**: Incluye únicamente reservas confirmadas (`BookingStatus.CONFIRMED`). Excluye reservas retenidas (`HELD`), expiradas (`EXPIRED`) y liberadas (`RELEASED`). Deduplica asignaciones a nivel `(passenger_id, leg_id)` para prevenir filas redundantes por joins con pagos o eventos.
   - **Adultos y menores**: Lista nombre completo, tipo/número de documento, parada de subida, parada de bajada, código de reserva y condición de butaca. Los menores gratuitos de 5 años en brazos (`is_lap_infant=True`) se listan con la leyenda "Menor sin asiento (en brazos, butaca X)" referenciando la butaca de la madre/acompañante confirmada en la misma compra.
   - **Rotulado y disclaimer**: Claramente identificado como listado interno provisional:
     - *"SC VIAJES — LISTADO PROVISIONAL DE PASAJEROS"*
     - *"DOCUMENTO DE CONTROL INTERNO — LISTADO PROVISIONAL NO HOMOLOGADO — NO CNRT (NO VÁLIDO COMO MANIFIESTO OFICIAL)"*
     - Pie de página legal en todas las páginas.
     - Sin códigos QR, códigos de barras visuales ni referencias a estados de embarque.

---

## 2. Evidencia de Validación Técnica

Todas las verificaciones se ejecutaron en el entorno aislado cumpliendo las directivas de no tocar bases de datos reales ni dependencias de infraestructura no aprobadas:

- **Suite de Pruebas Automatizadas (Django test runner / SQLite en memoria)**:
  - Módulo `panel.test_passenger_list_pdf`: 9/9 pruebas aprobadas (permisos de rol, adultos/menores en falda, exclusión de estados no confirmados, múltiples paradas de subida, deduplicación de joins, nombres largos con acentos y caracteres especiales, múltiples páginas y encabezados repetidos).
  - Módulos afectados de caja y pagos: `panel.test_cash_reports`, `panel.test_payments`, `panel.test_reports`, `tickets.test_boarding`, `tickets.test_fulfillment_integration`, `tickets.test_minors`: 100 % aprobados.
  - Suite completa: el runner informó `Found 697 test(s)`, ejecutó `Ran 692 tests` y terminó `OK (skipped=6)`: 686 aprobadas, 6 omitidas por requerir PostgreSQL, sin fallos ni errores. El log íntegro está fuera del repositorio en `C:\Users\JOEL\AppData\Local\Temp\scviajes-review-final-20261105\full-suite-final.log`.
- **Ruff linter**:
  - `python -m ruff check .` ejecutado con resultado 0 errores (código formateado y limpio de imports no utilizados).
- **Django system check**:
  - `python manage.py check` ejecutado con 0 errores (solo warning benigno preexistente `tickets.W002` sobre almacenamiento local en desarrollo).
- **Chequeo de migraciones**:
  - `python manage.py makemigrations --check --dry-run` confirma 0 modelos desincronizados y sin migraciones pendientes.
- **Git diff check**:
  - `git diff --check` ejecutado sin advertencias de espacios en blanco o conflictos.
- **Muestra PDF generada**:
  - Se generó fuera del repositorio una muestra sintética de dos páginas con 45 pasajeros, nombres largos y acentos; se extrajo su texto y se inspeccionaron visualmente ambas páginas renderizadas. Los artefactos están en `C:\Users\JOEL\AppData\Local\Temp\scviajes-review-final-20261105\pdf\`.

---

## 3. Decisiones y Asuntos Pendientes

1. **Referencia de butaca para menores sin acompañante en el mismo tramo**:
   - En el caso habitual, el menor de 5 años viaja en el mismo tramo con un adulto que tiene butaca asignada en la misma reserva; el listado vincula automáticamente dicha butaca.
   - Si por alguna anomalía histórica no se localizara una butaca asignada para el adulto en dicho tramo, el sistema utiliza un fallback seguro ("Menor sin asiento (en brazos, con acompañante)") evitando caídas en la generación del PDF. Queda pendiente definir con Sandro la política para compras complejas multidestino.
2. **Entorno de producción / Staging PostgreSQL**:
   - Las pruebas y comprobaciones se realizaron sobre SQLite efímero respetando las reglas del repositorio. La ejecución de migraciones en PostgreSQL requerirá validación en el ambiente de staging dedicado una vez disponible.
