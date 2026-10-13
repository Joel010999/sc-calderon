# Auditoría integral del release candidate

Fecha: 2026-10-12  
Base auditada: `origin/main` esperado `34389a7` (worktree `feature/final-release-candidate-audit-20261012`).

## Criterio

La matriz usa exclusivamente estos estados: `PASS`, `FAIL`, `BLOCKED_BY_BUSINESS_DECISION`, `REQUIRES_REAL_PROVIDER`, `REQUIRES_REAL_DATA` y `REQUIRES_STAGING_VALIDATION`. Los comandos se ejecutaron sólo con SQLite temporal, credenciales sintéticas y proveedores simulados; no se usaron bases, datos ni servicios reales.

| Área / control | Estado | Evidencia verificable / pendiente |
|---|---|---|
| Documentación de producto, arquitectura, decisiones y estado | PASS | Leídos `docs/PROJECT_SPEC.md`, `ARCHITECTURE.md`, `DECISIONS.md`, `STATUS.md` y documentación operativa asociada. |
| Django check en entorno de test | PASS | `python manage.py check` sin errores con SQLite temporal y variables sintéticas. |
| Migraciones consistentes | PASS | `makemigrations --check --dry-run`: sin cambios; `migrate` aplicado sólo a SQLite temporal. |
| Tests Django SQLite | PASS | Suite completa con configuración sintética coherente: 550 tests, OK, 7 skipped; los 7 corresponden a pruebas que requieren PostgreSQL. La primera corrida falló por overrides de entorno incompatibles (`PUBLIC_BASE_URL` HTTP y datos bancarios distintos), no por código. |
| Ruff | PASS | `uvx --from ruff==0.13.0 ruff check .`: todos los checks pasaron. |
| Dependencias Python | PASS | `pip check` sin dependencias rotas y `pip-audit -r requirements.txt`: no se conocen vulnerabilidades en las dependencias declaradas. El entorno global no se usa como criterio del release. |
| Dependencias npm | PASS | `npm audit --audit-level=high`: 0 vulnerabilidades. |
| `check --deploy` sintético | PASS | Con S3/SMTP, URL HTTPS, proxies/hosts, seguridad HTTP y datos bancarios sintéticos configurados: exit code 0; sólo quedaron advertencias HSTS de subdominios/preload para decisión de despliegue. |
| collectstatic | PASS | `python manage.py collectstatic --noinput`: 0 archivos nuevos, 48 sin cambios, sin errores. |
| PostgreSQL, concurrencia y workflows CI | REQUIRES_STAGING_VALIDATION | No se accedió a PostgreSQL real ni GitHub Actions; el coordinador debe verificar el hash final en CI. |
| Proveedores SMTP, S3, Google y Apple | REQUIRES_REAL_PROVIDER | Implementaciones simulables y checks presentes; faltan credenciales/endpoint/certificados productivos y pruebas de integración autorizadas. |
| Datos bancarios, tarifas, teléfono, políticas comerciales | BLOCKED_BY_BUSINESS_DECISION | No inventar valores: CVU/CUIT/alias, tarifas, menores, cancelaciones, cambios, saldos, contacto y servicios siguen pendientes de definición/validación. |
| AppSheet/Google Sheets/Flecha Bus | BLOCKED_BY_BUSINESS_DECISION | Migración y acuerdos externos fuera del alcance sin datos reales aprobados. |
| Secretos, credenciales y datos utilizables | PASS | No se encontraron `.env` ni secretos productivos; contraseñas/CBU de E2E están marcados como sintéticos y confinados al workflow de demo. |
| Branding y textos públicos | REQUIRES_STAGING_VALIDATION | La salida de algunas herramientas PowerShell mostró mojibake por decodificación de consola; los archivos del repositorio deben validarse como UTF-8 en el quality gate antes del release. No se aplicó una conversión masiva sin evidencia binaria. |
| TODO/FIXME/placeholders/código muerto | PASS | No se identificó placeholder productivo accionable; referencias sintéticas están documentadas como demo/test. |
| Pureza GET / mutaciones | PASS | Tests cubren rechazos de GET en acciones mutables; servicios de mantenimiento y vencimiento durable son POST/comando. |
| Auth, permisos, IDOR, CSRF, open redirects | PASS | Cobertura existente para roles, aislamiento, CSRF y `next`; no se reprodujo bypass en tests revisados. |
| Uploads y descargas privadas | PASS | Comprobantes/pasajes usan storage privado, tokens diferenciados y permisos; validado en tests SQLite. Persistencia real queda pendiente. |
| PII en logs, CSV, errores y auditoría | PASS | Auditoría/CSV y métricas sanitizadas; tests verifican exclusión de PII y protección contra fórmulas. |
| Capturas silenciosas y errores | PASS | Errores se registran de forma sanitizada y los tests cubren fallos de fulfillment/notificaciones. |
| Estados, transiciones y reglas de dominio | PASS | Servicios transaccionales e idempotentes cubiertos por tests; PostgreSQL debe validar restricciones concurrentes. |
| N+1 y consultas de reportes/manifiestos | REQUIRES_STAGING_VALIDATION | Revisión estática no sustituye profiling con dataset representativo y PostgreSQL. |
| Panel personalizado / Django Admin | PASS | Admin no está instalado y `/admin/` responde 404 en tests; panel bajo `/panel/`. |
| Operaciones, ventas y disponibilidad | PASS | Flujos y restricciones cubiertos por tests; configuración productiva de bus/seat layout pendiente de negocio. |
| Pagos, transferencias y caja | PASS | Efectivo/transferencia manual, caja y auditoría cubiertos con datos sintéticos; QR Mercado Pago/Payway quedan fuera. |
| Tickets, QR, email y fulfillment | PASS | Emisión, tokens, privacidad, reintentos y storage cubiertos en tests; SMTP/storage productivos pendientes. |
| Clientes Google/Apple, invitado y consentimiento | PASS | Tests de identidad, no enumeración, reclamo y consentimiento presentes; proveedores reales pendientes. |
| Personal y permisos | PASS | Grupos exactos, último administrador, invitaciones y CSRF cubiertos. |
| Manifiestos y embarque | PASS | Validación server-side, historial de reversión y lectura sin PII cubiertos. |
| Notificaciones y mantenimiento | PASS | Outbox, reintentos idempotentes y comandos dry-run cubiertos; scheduler/worker real requiere staging. |
| Abuso / rate limiting | PASS | Contadores hash-only y límites cubiertos en tests; comportamiento distribuido requiere PostgreSQL/staging. |
| Backup / restore | REQUIRES_STAGING_VALIDATION | Preflight/drill sintético disponible; no se ejecutó restore sobre PostgreSQL autorizado. |
| Checkout, accesibilidad y E2E | REQUIRES_STAGING_VALIDATION | Suite Playwright/axe está definida, pero no se ejecutó en esta auditoría por dependencia de Chromium/servidor; workflow debe confirmar el hash final. |

## Correcciones realizadas

No se modificó código de aplicación durante esta auditoría: los hallazgos observados son de validación/entorno o requieren decisiones confirmadas. Se corrigió la clasificación del informe para separar la corrida con entorno inválido de la suite válida. Este informe es el único artefacto agregado.

## Impedimentos de entrega

El release no debe declararse listo para datos reales hasta ejecutar Ruff y todos los workflows sobre el hash final, validar PostgreSQL/restore/staging, configurar storage/SMTP/proveedores autorizados y confirmar datos bancarios y decisiones comerciales sin inventar valores. La suite SQLite válida y los tests afectados están en verde.

## Actualización del toolkit de inventario heredado

- **PASS:** la herramienta local `inventory_legacy_exports` fue revisada con datos sintéticos y mantiene sus protecciones de lectura, sanitización y límites para CSV, TSV y XLSX multihoja.
- **No realizada:** no se efectuó ninguna conexión a AppSheet ni Google Sheets.
- **No utilizados:** no se utilizaron datos reales.
- **Bloqueado:** la configuración real y las decisiones de Sandro continúan pendientes de autorización y definición.
- **Pendiente:** el staging autorizado continúa sin ejecutarse.

Esta actualización no declara el sistema productivo ni aprobado para datos reales.
