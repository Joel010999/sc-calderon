# Auditoría integral del release candidate y consolidación posterior al PR #48

---

## Parte 1. Auditoría histórica del release candidate (2026-10-12)

> [!NOTE]
> **ESTADO: AUDITORÍA HISTÓRICA.**
> Esta sección conserva el resultado de la auditoría original realizada el 12 de octubre de 2026 sobre la base `34389a7` (PR #42 / PR #43). Los cambios e integraciones posteriores a dicha fecha se documentan en la Parte 2 de este informe.

Fecha histórica: 12 de octubre de 2026
Base auditada: `origin/main` esperado `34389a7` (worktree `feature/final-release-candidate-audit-20261012`).

### Criterio histórico

La matriz original utilizó los estados: `PASS`, `FAIL`, `BLOCKED_BY_BUSINESS_DECISION`, `REQUIRES_REAL_PROVIDER`, `REQUIRES_REAL_DATA` y `REQUIRES_STAGING_VALIDATION`. Los comandos se ejecutaron exclusivamente con SQLite temporal, credenciales sintéticas y proveedores simulados; no se utilizaron bases, datos ni servicios reales.

| Área / control | Estado histórico | Evidencia verificable / pendiente |
|---|---|---|
| Documentación de producto, arquitectura, decisiones y estado | PASS | Leídos `docs/PROJECT_SPEC.md`, `ARCHITECTURE.md`, `DECISIONS.md`, `STATUS.md` y documentación operativa asociada. |
| Django check en entorno de test | PASS | `python manage.py check` sin errores con SQLite temporal y variables sintéticas. |
| Migraciones consistentes | PASS | `makemigrations --check --dry-run`: sin cambios; `migrate` aplicado sólo a SQLite temporal. |
| Tests Django SQLite | PASS | Suite completa con configuración sintética coherente: 550 tests, OK, 7 skipped; los 7 corresponden a pruebas que requieren PostgreSQL. |
| Ruff | PASS | `uvx --from ruff==0.13.0 ruff check .`: todos los checks pasaron. |
| Dependencias Python | PASS | `pip check` sin dependencias rotas y `pip-audit -r requirements.txt`: no se conocen vulnerabilidades en las dependencias declaradas. |
| Dependencias npm | PASS | `npm audit --audit-level=high`: 0 vulnerabilidades. |
| `check --deploy` sintético | PASS | Con S3/SMTP, URL HTTPS, proxies/hosts, seguridad HTTP y datos bancarios sintéticos configurados: exit code 0. |
| collectstatic | PASS | `python manage.py collectstatic --noinput`: 0 archivos nuevos, 48 sin cambios, sin errores. |
| PostgreSQL, concurrencia y workflows CI | REQUIRES_STAGING_VALIDATION | No se accedió a PostgreSQL real ni GitHub Actions en la auditoría local; el coordinador verificó el hash en CI. |
| Proveedores SMTP, S3, Google y Apple | REQUIRES_REAL_PROVIDER | Faltan credenciales/endpoint/certificados productivos y pruebas de integración autorizadas. |
| Datos bancarios, tarifas, teléfono, políticas comerciales | BLOCKED_BY_BUSINESS_DECISION | Datos bancarios, tarifas, menores, cancelaciones, cambios, saldos y contacto pendientes de definición con Sandro. |
| AppSheet/Google Sheets | BLOCKED_BY_BUSINESS_DECISION | Migración y acuerdos externos fuera del alcance sin datos reales aprobados. |
| Secretos, credenciales y datos utilizables | PASS | No se encontraron `.env` ni secretos productivos; contraseñas/CBU están marcados como sintéticos y confinados a demo. |
| Branding y textos públicos | REQUIRES_STAGING_VALIDATION | Validación UTF-8 requerida en el quality gate antes del release. |
| TODO/FIXME/placeholders/código muerto | PASS | No se identificó placeholder productivo accionable; referencias sintéticas están documentadas como demo/test. |
| Pureza GET / mutaciones | PASS | Tests cubren rechazos de GET en acciones mutables; servicios de mantenimiento y vencimiento durable son POST/comando. |
| Auth, permisos, IDOR, CSRF, open redirects | PASS | Cobertura para roles, aislamiento, CSRF y `next`; no se reprodujo bypass en tests revisados. |
| Uploads y descargas privadas | PASS | Comprobantes y pasajes usan storage privado, tokens diferenciados y permisos. Persistencia real pendiente. |
| PII en logs, CSV, errores y auditoría | PASS | Auditoría/CSV y métricas sanitizadas; tests verifican exclusión de PII y neutralización de fórmulas. |
| Capturas silenciosas y errores | PASS | Errores se registran de forma sanitizada y los tests cubren fallos de fulfillment/notificaciones. |
| Estados, transiciones y reglas de dominio | PASS | Servicios transaccionales e idempotentes cubiertos por tests. |
| N+1 y consultas de reportes/manifiestos | REQUIRES_STAGING_VALIDATION | Revisión estática no sustituye profiling con dataset representativo y PostgreSQL. |
| Panel personalizado / Django Admin | PASS | Admin deshabilitado; panel 100 % propio bajo `/panel/`. |
| Operaciones, ventas y disponibilidad | PASS | Flujos y restricciones cubiertos por tests; configuración productiva de bus/seat layout pendiente de negocio. |
| Pagos, transferencias y caja | PASS | Efectivo/transferencia manual, caja y auditoría cubiertos con datos sintéticos; pasarelas automáticas diferidas. |
| Tickets, QR, email y fulfillment | PASS | Emisión, tokens, privacidad, reintentos y storage cubiertos en tests; SMTP/storage productivos pendientes. |
| Clientes Google/Apple, invitado y consentimiento | PASS | Tests de identidad, no enumeración, reclamo y consentimiento presentes. |
| Personal y permisos | PASS | Grupos exactos, último administrador, invitaciones y CSRF cubiertos. |
| Manifiestos y embarque | PASS | Validación server-side, historial de reversión y lectura sin PII cubiertos. |
| Notificaciones y mantenimiento | PASS | Outbox, reintentos idempotentes y comandos dry-run cubiertos; scheduler/worker real requiere staging. |
| Abuso / rate limiting | PASS | Contadores hash-only y límites cubiertos en tests. |
| Backup / restore | REQUIRES_STAGING_VALIDATION | Preflight/drill sintético disponible; no se ejecutó restore sobre PostgreSQL autorizado. |
| Checkout, accesibilidad y E2E | REQUIRES_STAGING_VALIDATION | Suite Playwright/axe definida; requiere confirmación en CI. |

---

## Parte 2. Consolidación del estado posterior al PR #48 (2026-10-27)

> [!IMPORTANT]
> **NO SE DECLARA PRODUCCIÓN APROBADA.**
> Las entregas integradas desde el release candidate completaron el alcance acordado con Sandro en código y pruebas automatizadas, pero el pase a producción permanece formalmente condicionado a la ejecución del staging real con base PostgreSQL dedicada, configuración de almacenamiento S3 y servidor SMTP productivos, y la resolución de las definiciones comerciales abiertas.

### 1. Historial de integraciones posteriores al Release Candidate

Desde el corte histórico de la auditoría original (`34389a7`), se integraron a la rama principal los siguientes cinco Pull Requests:

1. **PR #44 (commit `3398bb5`) — Inventario seguro de datos heredados**:
   - Incorporación de `inventory_legacy_exports` para inspección pasiva y sanitizada de archivos heredados de AppSheet y Google Sheets (CSV, TSV, XLSX multihoja) sin dependencias de red ni conexión a servicios remotos.
2. **PR #45 (commit `2b47af4`) — Adaptación a reglas Sandro de reservas, pagos y balances**:
   - Reservas manuales rápidas sin vencimiento automático (`expires_at = None`).
   - Alcance de vendedores acotado a operaciones propias en QuerySet base.
   - Revisión de comprobantes de transferencia reservada con exclusividad a Administradores activos.
   - Balance de vendedores con comisión fija de `Decimal("5000.00")` por asignación confirmada con precio positivo.
3. **PR #46 (commit `16a6808`) — Rutas con Güemes y gratuidad de menores sin butaca**:
   - Inclusión de la parada Güemes (`GUE`, Salta) en ambos sentidos de recorrido.
   - Prohibición estricta de tramos intraprovinciales (`origin.province != destination.province`).
   - Gratuidad sin butaca para menores con acompañante obligatorio.
4. **PR #47 (commit `1c3e6f1`) — Tarifas por salida y butacas prioritarias 51/54**:
   - Modelo de tarifas `operations.TripFare` por salida y categoría (`CAMA` y `SEMI_CAMA`), uniforme en todos los tramos permitidos.
   - Bloqueo de butacas prioritarias 51 y 54 para venta general hasta las 00:00 de Argentina de dos días calendario antes de la salida.
5. **PR #48 (commit `69297a5`) — Menores de cinco años, horarios por parada y pasaje oficial AAA**:
   - Ampliación del umbral de gratuidad a menores de cinco años (0 a 4 años cumplidos).
   - Horarios programados/estimados por parada en `TripStop.scheduled_at`, editables por Administradores.
   - Plantilla PDF oficial aprobada de 1 página con dos talones, sin códigos QR ni de barras visuales, con numeración correlativa persistente serie `AAA-000000001` (`TicketSequence`) y tokens criptográficos internos.

### 2. Matriz de evidencia técnica por SHA integrado

| PR / Módulo | SHA | Pruebas sintéticas (SQLite efímero) | Workflows CI (GitHub Actions) | Validación Staging Real |
|---|---|---|---|---|
| **PR #44** (Legacy exports) | `3398bb5` | 100 % tests de parsing e inventario OK sobre fixtures sintéticos. | Quality Gate en verde. | Pendiente con archivos reales de AppSheet. |
| **PR #45** (Sandro reservas/pagos) | `2b47af4` | 560 tests OK, 7 skipped (PostgreSQL). Migraciones `sales.0003` y `panel.0003`. | `sales-postgres.yml` validó concurrencia y locks en PostgreSQL 16. | Pendiente provisioning real de cuenta Sandro (`is_owner`). |
| **PR #46** (Güemes y menores) | `16a6808` | 567 tests OK, 7 skipped. Migraciones `sales.0004` y `tickets.0006`. | Workflows Quality Gate, Sales Postgres y Tickets Postgres en verde. | Pendiente ejecución de comando en staging. |
| **PR #47** (Tarifas y prioritarias) | `1c3e6f1` | 586 tests OK, 7 skipped. Migración `operations.0003` con detección de conflictos. | Concurrencia transaccional validada en CI PostgreSQL. | Pendiente carga de tarifas comerciales reales. |
| **PR #48** (Menores 5a, horarios, pasaje AAA) | `69297a5` | 172 tests afectados (169 OK, 3 PostgreSQL skipped). 71 tests de `tickets` al 100 % OK. Migración `tickets.0007`. | Integrado sobre base de workflows en verde. | Pendiente validación visual externa y storage S3 productivo. |

### 3. Contradicciones y afirmaciones históricas resueltas

1. **Expiración de reservas manuales**:
   - *Afirmación histórica superada*: vencimiento automático a las 24 horas (`SALES_MANUAL_HOLD_HOURS = 24`).
   - *Estado comprobado vigente*: reserva manual sin expiración automática (`expires_at = None`), atribuida al vendedor autenticado y con liberación manual restringida exclusivamente al creador de la reserva.
2. **Edad límite de gratuidad para menores**:
   - *Afirmación histórica superada*: gratuidad para menores de 2 años.
   - *Estado comprobado vigente*: gratuidad sin butaca para menores estrictamente menores a 5 años (`0` a `4` años cumplidos a la fecha de compra). Al cumplir 5 años abonan tarifa completa y ocupan butaca propia.
3. **Códigos visuales en el pasaje impreso**:
   - *Afirmación histórica superada*: pasaje con código QR impreso visible para control de embarque.
   - *Estado comprobado vigente*: la plantilla oficial aprobada por Sandro suprime totalmente los códigos QR y de barras visuales en ambos talones del documento PDF, preservando internamente los tokens de verificación y descarga en la base de datos para la validación de embarque interna en `/panel/embarques/` y emitiendo la numeración secuencial persistente `AAA-000000001` mediante el modelo `TicketSequence`.

### 4. Decisiones comerciales y validaciones pendientes

Permanecen abiertas y requieren resolución formal antes de cualquier puesta en producción comercial:

1. **Definiciones comerciales con Sandro**:
   - Fecha de referencia para la edad del menor: resolver si se adopta definitivamente la fecha de creación de la reserva (`Booking.created_at`) o la de confirmación del pago (`Booking.confirmed_at`).
   - Seguro obligatorio para menores en brazos: definir si se cobra prima de seguro, su monto e impacto en el pasaje.
   - Acreditación de menores: definir si se implementará integración con RENAPER o verificación presencial física.
   - Asignación previa de butacas 51 y 54: definir rol autorizante, procedimiento y tarifa previa a la liberación automática de 2 días.
   - Políticas de cambios y devoluciones: porcentajes de penalización, plazos de solicitud y condiciones de reintegro.
   - Datos fiscales y legales de la empresa transportista: razón social, CUIT, aseguradora, póliza y direcciones físicas exactas de paradas.
2. **Validaciones en Staging Real**:
   - Despliegue en Railway Staging con base PostgreSQL 16 y verificación de migraciones `0001` a `0007`.
   - Ejecución de la suite completa de concurrencia sobre PostgreSQL (resolviendo los 7 tests específicos omitidos en SQLite).
   - Configuración de almacenamiento privado S3-compatible (`PRIVATE_STORAGE_BACKEND=s3`) y verificación de descargas autenticadas.
   - Configuración de servidor SMTP transaccional y validación de entrega de pasajes adjuntos.
   - Verificación visual independiente de pasajes emitidos en visor PDF externo.
