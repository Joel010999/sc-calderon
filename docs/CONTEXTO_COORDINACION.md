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
  coordinará tareas y revisiones cuando exista la conexión MCP. Un puente
  local de solo lectura se construyó fuera del repositorio
  (`C:\renderbyte\devin-bridge`) y quedó **pausado**; ChatGPT no se conectó.
- Reglas de trabajo del repo en `AGENTS.md` (nunca `main`, sin Django Admin,
  monolito modular Django, PostgreSQL en producción, `Decimal` para dinero,
  datetimes conscientes de zona horaria, migración por cada cambio de modelo).

## 2. Reglas y decisiones vigentes

### Funcionalidad acordada (verificada en código)

- Administradores/superusuarios gestionan recorridos y viajes; vendedores
  consultan y operan sus propias ventas, sin recorridos/viajes ni reservas
  manuales sin vencimiento (restricción por rol confirmada y reconciliada en
  la documentación canónica) (`panel/reservation_services.py`,
  `require_can_create_manual_unexpired_reservation`; coacción `sale_type`
  en `panel/reservation_views.py`).
- Venta interna (decisión confirmada por Joel): cada vendedor cobra en su
  cuenta personal; comprobante obligatorio; su carga por el vendedor titular
  confirma el cobro, aprueba el pago y dispara la emisión, sin segunda
  aprobación ni cuenta/Mercado Pago del negocio (declaración del vendedor,
  no verificación bancaria). Aislamiento por vendedor (otro vendedor: 404).
- Transferencias de compras públicas (web): sólo administradores y
  superusuarios las aprueban o rechazan (decisión confirmada; bloqueo por
  UI, URL, POST y servicio).
- `/tickets/verify/`: consulta de solo lectura conservada con sus permisos
  actuales; el retiro del embarque no la alcanza.
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
- **Butacas prioritarias 51 y 54 (discapacidad)**: decisión confirmada
  por Joel — reservadas por solicitud por correo hasta 48 horas antes de
  la salida; a las 48 horas exactas previas se liberan automáticamente
  para venta general si están disponibles, sin cancelar ocupaciones ni
  circuito de autorización en la aplicación.
- **Diferidos sin inventar reglas**: fecha de referencia de edad del
  menor, seguro y documentación de menores (RENAPER), políticas de
  cambios/cancelaciones/devoluciones, datos bancarios/fiscales reales.

### Contradicciones registradas (consultar, no resolver por suposición)

| Punto | Acuerdo conversado | Lo que el código hace hoy | Acción |
|---|---|---|---|
| ~~Butacas 51/54~~ | Solicitud por correo hasta 48 h antes; liberación automática a las 48 h exactas previas a la salida | `sales/priority_seats.py` libera a `departure_at` − 48 h; la solicitud por correo queda fuera de la aplicación | **Resuelto**: decisión confirmada e implementada |
| ~~Revisión de transferencias de ventas internas~~ | Cobro en cuenta personal del vendedor; la carga del comprobante por el titular confirma | La carga del titular confirma en la misma transacción, sin casilla ni segunda aprobación | **Resuelto**: decisión confirmada e implementada |
| ~~Revisión de transferencias de compras públicas~~ | Sólo administradores y superusuarios | Regla explícita por canal en servicio, permisos y vistas | **Resuelto**: decisión confirmada e implementada |
| ~~Reservas manuales sin vencimiento~~ | `DECISIONS.md` §1 / `STATUS.md` §1.1 decían que los vendedores las crean sin vencimiento | Sólo administradores; el vendedor obtiene venta interna con vencimiento `min(medianoche AR, salida − 1 h)` | **Reconciliado**: decisión confirmada, documentos actualizados |

## 3. Estado técnico y evidencia

Verificado por `git` en esta máquina (árbol limpio al momento de escribir):

| Item | Estado | Origen |
|---|---|---|
| Rama de trabajo | `Joel010999/mercadopago-checkout-pro` | `git branch` |
| HEAD | Ver `git log`; al momento de la última actualización, la rama contiene `b393348` (Checkout Pro), `7107a52` (este documento), `88df44f`/`4aee059` (revisión Checkout Pro), `2847344`/`527ff29` (aceptación local) y la reconciliación documental posterior, todos locales sin push | `git log` |
| `4d9d6ad` | Actualización documental (verificaciones E2E + fix `5642156`) | `git log` |
| `5642156` | Fix mantenimiento: reservas HELD sin `Payment` ya no se excluyen por `latest_payment_status NULL` | `git log`, pusheado a PR #50 |
| `1bc0d4d` | Botones Descargar PDF por pasaje en detalle interno + tests de alcance | `git log`, pusheado a PR #50 |
| PR #50 → `main` | Open, **Draft**, head remoto `5642156` (re-verificado por API de GitHub el 19/11/2026) | API GitHub (evidencia consultada) |
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

1. **Sandbox Checkout Pro**: credenciales de prueba MP (app de prueba +
   usuarios vendedor/comprador) y túnel HTTPS para webhooks
   (`/webhooks/mercadopago/`). Guía en `docs/MERCADO_PAGO.md`.
2. Reglas de menores, cambios/devoluciones, datos fiscales/bancarios
   reales — pendientes de Sandro (`docs/DECISIONS.md` §3-§4).
   **Payway**: agregado al alcance como pendiente de implementación;
   propuesta técnica en `docs/PAYWAY.md`. Requiere de Sandro: credenciales
   sandbox, producto (tokenización vs. formulario hospedado), cuotas,
   devoluciones, vertical de fraude y coexistencia con Mercado Pago.
3. Validación de despliegue real en staging PostgreSQL (la CI no la
   sustituye), storage privado S3, SMTP, drill de backup.
4. Puente MCP ChatGPT↔Devin: puente local de solo lectura construido y
   **pausado**; sin túnel ni conexión desde ChatGPT. Antecedente: investigado (CLI `devin.exe` v3000.14.30819
   presente, sesiones resumibles por ID, locks en `session_locks/`).
   No expuesto a internet.

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
