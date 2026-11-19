# Contexto de coordinación — SC Viajes

**Leer este documento junto con `AGENTS.md` al iniciar toda sesión coordinada
(por ChatGPT u otro orquestador).** Describe el acuerdo vigente, el estado
verificado del repositorio y el protocolo de entrega. Ante contradicción
entre una afirmación y el código, la contradicción se registra y se consulta;
no se resuelve por suposición.

## 1. Objetivo y entorno

- Proyecto **SC Viajes** — cliente **Sandro**, responsable técnico **Joel Santos**.
- Repositorio remoto: `Joel010999/sc-calderon`.
- Desarrollo con Devin Local sobre Windows + `localhost` con **SQLite de prueba**
  y datos ficticios. Producción vive en **Railway y no debe tocarse**.
- Codex/Orca/Antigravity quedaron **pausados** para este flujo. ChatGPT
  coordinará tareas y revisiones cuando exista la conexión MCP (puente aún
  no construido — investigado, no implementado).
- Reglas de trabajo del repo en `AGENTS.md` (nunca `main`, sin Django Admin,
  monolito modular Django, PostgreSQL en producción, `Decimal` para dinero,
  datetimes conscientes de zona horaria, migración por cada cambio de modelo).

## 2. Reglas y decisiones vigentes

### Funcionalidad acordada (verificada en código)

- Administradores/superusuarios gestionan recorridos y viajes; vendedores
  consultan y operan sus propias ventas, sin recorridos/viajes ni reservas
  manuales sin vencimiento (`panel/reservation_services.py`,
  `require_can_create_manual_unexpired_reservation`; coacción `sale_type`
  en `panel/reservation_views.py`).
- Venta interna por transferencia con comprobante y aprobación según
  permisos; aislamiento por vendedor (otro vendedor recibe 404).
- Vencimiento interno: `min(medianoche AR del día de compra, salida
  inicial - 1h)` — `sales/internal_transfer.py:88-100`. En la última hora
  rige el flujo inmediato ya implementado (comprobante + pago verificado).
- Compra pública: retención de butaca de 15 minutos
  (`SALES_ONLINE_HOLD_MINUTES`).
- Idempotencia, disponibilidad por tramo e históricos conservados.
- Recorridos dentro de Viajes; versionado de recorridos utilizados.
- Caja, efectivo nuevo y validación de embarque: **retirados**
  operativamente; modelos y registros históricos conservados.
- Menores gratuitos sin asiento propio: pasaje con referencia persistente
  de la butaca del acompañante por tramo, en ambos talones
  (`Sin asiento propio. Butaca de referencia del acompañante: N`).
- Manifiesto de pasajeros: PDF provisional administrativo con confirmados
  y menores sin asiento; formato definitivo CNRT queda para después.
- Panel rojo, gráficos y comisiones implementados. Regla exacta de
  comisión (verificada): `COMMISSION_PER_TICKET = Decimal("5000.00")` por
  asignación confirmada con precio positivo, por pasajero y tramo
  (`panel/report_services.py:26,613`); menores sin butaca y ventas web
  sin vendedor no comisionan; ingresos = pagos `APPROVED` de reservas
  `CONFIRMED`.

### Decisiones recientes que prevalecen

- **Migración AppSheet/Google Sheets: CANCELADA.** Sandro no tendrá acceso
  al sistema anterior; SC Viajes usa su propia base de datos
  (`docs/DECISIONS.md` §4).
- **Mercado Pago Checkout Pro autorizado** para implementación y sandbox
  (ya no depende de migración). Checkout Pro ≠ QR presencial; este último
  no se implementa. Transferencia manual intacta.
- **Butacas prioritarias 51 y 54 (discapacidad)**: el acuerdo conversado
  es *consultas o solicitud por correo hasta 48 horas antes del día del
  viaje* y **no se acordó liberación automática**. Interpretación del
  horario límite y demás condiciones: **pendientes de Sandro**.
- **Diferidos sin inventar reglas**: fecha de referencia de edad del
  menor, seguro y documentación de menores (RENAPER), políticas de
  cambios/cancelaciones/devoluciones, datos bancarios/fiscales reales.

### Contradicciones registradas (consultar, no resolver por suposición)

| Punto | Acuerdo conversado | Lo que el código hace hoy | Acción |
|---|---|---|---|
| Butacas 51/54 | Solicitud por correo hasta 48 h antes; **sin liberación automática acordada** | `sales/priority_seats.py` **sí libera automáticamente** a las 00:00 AR dos días calendario antes de la salida | Reconciliar con Sandro: ¿se retira la liberación automática, se reemplaza por solicitud manual, o conviven? |
| Revisión de transferencias internas | `DECISIONS.md`/`PROJECT_SPEC.md`: exclusiva de administradores | `panel/internal_sales_permissions.py` (regla 5) y la UI permiten al vendedor aprobar sus propias ventas (verificado en aceptación local C4) | Confirmar con Sandro cuál rige |
| Reservas manuales sin vencimiento | `DECISIONS.md` §1 / `STATUS.md` §1.1: los vendedores las crean sin vencimiento | Sólo administradores; el vendedor obtiene venta interna con vencimiento `min(medianoche AR, salida − 1 h)` | Actualizar documentación canónica al confirmar |

## 3. Estado técnico y evidencia

Verificado por `git` en esta máquina (árbol limpio al momento de escribir):

| Item | Estado | Origen |
|---|---|---|
| Rama de trabajo | `Joel010999/mercadopago-checkout-pro` | `git branch` |
| HEAD | `b393348` Mercado Pago Checkout Pro (local, sin push) | `git log` |
| `4d9d6ad` | Actualización documental (verificaciones E2E + fix `5642156`) | `git log` |
| `5642156` | Fix mantenimiento: reservas HELD sin `Payment` ya no se excluyen por `latest_payment_status NULL` | `git log`, pusheado a PR #50 |
| `1bc0d4d` | Botones Descargar PDF por pasaje en detalle interno + tests de alcance | `git log`, pusheado a PR #50 |
| PR #50 → `main` | Open, **Draft**, head remoto `5642156` (verificado por API de GitHub al momento del push; no re-verificado después) | API GitHub (evidencia consultada) |
| Workflows CI sobre `5642156` | 5 en verde: Quality Gate, Browser E2E, Sales PostgreSQL, Tickets PostgreSQL, Restore Drill | API GitHub (evidencia consultada). **CI ≠ validación de despliegue real en staging.** |
| `b393348` (Checkout Pro) | Implementación completa con `payments.0003`; **solo validado con mocks** (26 tests); **sandbox NO ejecutado** — faltan credenciales de prueba y recepción HTTPS de webhooks | `git log` + `payments/test_mercadopago.py` |
| Aprobación post-vencimiento | Conflicto registrado (`conflict_reason` + `AuditEvent`); sin reasignación de butaca, sin pasaje, sin devolución automática | `payments/mercadopago.py` |

### Pruebas locales ya realizadas (no repetir sin cambio relacionado)

- Venta interna por transferencia y aprobación → PDF.
- Ida/vuelta con menor: referencia de butaca distinta por tramo.
- Descarga por botón real como admin y vendedor titular; otro vendedor 404.
- Compra pública invitada por transferencia → PDF; butaca bloqueada en
  segunda sesión.
- Vencimiento por `run_operational_maintenance` real (sin otra compra).
- Vendedor no crea reserva sin vencimiento (POST forjado coaccionado a
  transferencia con vencimiento + protección de servicio).

## 4. Pendientes y dudas por resolver

1. **Contradicción butacas 51/54** (tabla §2): definir con Sandro si la
   liberación automática implementada se mantiene, se retira o se
   reemplaza por solicitud manual por correo.
2. **Sandbox Checkout Pro**: credenciales de prueba MP (app de prueba +
   usuarios vendedor/comprador) y túnel HTTPS para webhooks
   (`/webhooks/mercadopago/`). Guía en `docs/MERCADO_PAGO.md`.
3. Reglas de menores, cambios/devoluciones, datos fiscales/bancarios
   reales, Payway — pendientes de Sandro (`docs/DECISIONS.md` §3-§4).
4. Validación de despliegue real en staging PostgreSQL (la CI no la
   sustituye), storage privado S3, SMTP, drill de backup.
5. Puente MCP ChatGPT↔Devin: investigado (CLI `devin.exe` v3000.14.30819
   presente, sesiones resumibles por ID, locks en `session_locks/`);
   **no construido ni expuesto**.

## 5. Próxima tarea

**Revisar la implementación de Checkout Pro (`b393348`) y completar la
prueba sandbox** UI→PDF cuando existan credenciales de prueba y túnel
HTTPS. Mientras tanto, el trabajo queda en la rama
`Joel010999/mercadopago-checkout-pro` sin push ni merge.

## 6. Protocolo de coordinación y entrega

- Consultar `git` antes de modificar; preservar trabajo ajeno; sin reset.
- **Una sola sesión con capacidad de escritura por directorio** (los
  locks de sesión en `%APPDATA%\devin\cli\session_locks` indican sesiones
  activas: no resumir una sesión en uso).
- Resolver errores técnicos dentro del alcance; verificar resultados
  reales — ningún éxito se declara por exit code 0 ni por informe previo.
- Cada entrega incluye: diff, pruebas ejecutadas con resultado, SHA,
  pendientes reales y limitaciones.
- Prohibido sin autorización explícita de la tarea: push, merge,
  despliegue, Railway, proveedores reales, credenciales reales.
- Datos ficticios siempre; nunca secretos en código, logs ni documentos.
- No cerrar procesos globales de Chrome: solo instancias de prueba.
- Actualizar la documentación canónica cuando cambie el alcance.
