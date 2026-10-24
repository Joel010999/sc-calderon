# Estado de la tanda Sandro — reservas y pagos — 24/10/2026

## Alcance implementado

- Reserva manual rápida atribuida al actor autenticado, con DNI y nombre como datos mínimos, comentario interno opcional y `expires_at` nulo.
- Reservas online conservan la retención de 15 minutos y la exigencia de vencimiento explícito.
- Liberación manual limitada al creador de la reserva, con alcance por vendedor aplicado antes de filtros, detalles, descargas y acciones.
- Pago manual exige completar los datos de venta antes de cobrar.
- Revisión de transferencias limitada a administradores activos; los vendedores registran comprobantes y consultan sus propias operaciones.
- Rechazos preservan el intento, el motivo y la butaca retenida; el siguiente comprobante crea un nuevo intento.
- Aprobación terminal y fulfillment durable; no se genera un aviso independiente de transferencia aprobada.
- Corte manual server-side: permitido exactamente a 30 minutos de la subida y rechazado a 29 minutos.
- Mantenimiento no vence reservas manuales sin vencimiento ni reservas cuyo último intento fue rechazado.

## Base y aislamiento

- SHA base auditado: `3398bb5ff9d9e5d1b87aead90e71f39297d94cc5`.
- Rama: `feature/sandro-reservas-pagos-20261024`.
- Worktree: `D:\renderbyte\scviajes-wt-reservas-20261024`.
- No se modificó el repositorio principal ni se integró `production-maintenance-worker`.

## Validación

- `python -m compileall -q sales payments panel core operations sslviajes`: OK.
- `python manage.py check` con SQLite efímero: OK, con warning preexistente de `tickets.W002`.
- `python manage.py makemigrations --check --dry-run` con SQLite efímero: OK.
- Suite afectada inicial: encontró expectativas antiguas de 24 horas y revisión por vendedores; se actualizaron esas pruebas para los contratos nuevos.
- La suite completa y Ruff quedan como validación final de entrega.

## Decisiones y pendientes de integración

- El corte manual sigue midiendo provisionalmente la subida inicial, pendiente de confirmación del negocio.
- No se definió aún el tratamiento de aprobaciones tardías de transferencias iniciadas antes del corte; se preservan los estados existentes sin borrar pagos ni crear excepciones.
- El plazo anterior de carga/revisión online se conserva como política heredada pendiente de validación; no se presenta como decisión nueva.
- El recibo interno y su adjunto no se implementan en esta tanda.
- Payway permanece fuera de alcance y sin llamadas externas.
- `.env.example` no se modificó porque contiene una secuencia de codificación inválida para la edición segura; la configuración del corte se agregó mediante `SALES_MANUAL_CUTOFF_MINUTES` en settings.
