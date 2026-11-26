# Guion de demostración — reunión con Sandro (viernes 27/11, mañana)

Alcance: demostración funcional de la interfaz y los flujos implementados.
No es una habilitación de producción.

## Motor y límites declarados

- La demo corre sobre **SQLite** en un archivo nuevo fuera del repositorio
  (`%USERPROFILE%\scviajes-demo-sandro\demo.sqlite3`). Es una demostración
  de UI: la aceptación sobre **PostgreSQL 16** queda respaldada por CI
  (workflows `sales-postgres`, `tickets-postgres` y `browser-e2e` con
  servicio `postgres:16` efímero; el servidor que usa Playwright hereda
  el mismo `DATABASE_URL` y el workflow lo verifica imprimiendo
  `connection.vendor`).
- Correo: backend `locmem` (no se envía correo real). Payway y Mercado
  Pago: deshabilitados en esta demo (sin credenciales del comercio).
- Datos 100 % ficticios: dominio `demo.scviajes.invalid`, usuarios
  `demo-20261005-*`, comprobantes sintéticos.

## Arranque y apagado (reproducible)

Desde Git Bash en la raíz del repo, rama `feature/mercadopago-checkout-pro`:

```bash
bash scripts/demo_sandro.sh              # prepara y levanta en http://127.0.0.1:8000/
SCVIAJES_DEMO_RESET=1 bash scripts/demo_sandro.sh   # recrea la base desde cero
```

Apagado: `Ctrl+C`. No queda ningún proceso vivo; la próxima corrida
reutiliza la base (o la recrea con `SCVIAJES_DEMO_RESET=1`).

Verificación del motor: el propio script imprime
`Motor de la demo: sqlite - .../demo.sqlite3` antes de levantar.

## Usuarios

| Usuario | Grupo | Contraseña demo |
|---|---|---|
| `demo-20261005-admin` | Administrador | `demo-admin-2026` |
| `demo-20261005-vendedor` | Vendedor | `demo-vendedor-2026` |
| `demo-20261005-cash-vendedor` | Vendedor | `demo-caja-2026` |
| `cliente@demo.scviajes.invalid` | Cliente web | `demo-cliente-2026` |

## Datos sembrados

- Recorrido ida: Córdoba Demo → Jesús María Demo → Perico Demo →
  Palpalá Demo → Jujuy Demo (ida, subida en las dos primeras).
- Recorrido vuelta: el inverso.
- Viaje IDA (~+7 días del seed, siempre posterior a la reunión) y
  VUELTA (~+10 días), colectivo de 59 butacas
  (1–48 SEMI_CAMA $13.000 / 49–59 CAMA $19.500).
- Reservas listas: `held@` (retenida invitado), `roundtrip@`
  (ida/vuelta), `awaiting@` (esperando comprobante), `review@`
  (transferencia EN REVISIÓN para aprobar en vivo), `rejected@`
  (transferencia rechazada), `confirmed@` (confirmada con pasaje
  emitido, vinculada al cliente), `expired@` (vencida).

## Guion 15–20 minutos

1. **Búsqueda pública (2 min)** — `/` → elegir "Córdoba Demo" →
   "Jujuy Demo", fecha del viaje IDA → resultados con categorías y
   tarifas por tramo.
2. **Compra web invitado con transferencia (4 min)** — seleccionar
   butaca → completar pasajero → `/resumen/<id>/` → botón
   "Pagar con transferencia" → se muestran datos bancarios ficticios →
   "Subir comprobante" (cualquier PNG/PDF) → queda *en revisión*.
3. **Panel vendedor (3 min)** — `/panel/login/` con
   `demo-20261005-vendedor` → `/panel/reservas/` → "Nueva reserva" →
   venta interna: al cargar el comprobante **el titular confirma
   directamente**. Mostrar que el menú no ofrece revisión de
   transferencias ni usuarios.
4. **Panel administrador (4 min)** — `/panel/login/` con
   `demo-20261005-admin` → `/panel/pagos/transferencias-pendientes/` →
   abrir `review@` → "Aprobar" → reserva confirmada → fulfillment
   emite el pasaje → descargar PDF desde la reserva. Mostrar también
   `rejected@` (rechazo con motivo).
5. **Descarga del pasaje (2 min)** — `/cliente/login/` con el cliente
   demo → "Mis viajes" → descargar pasaje PDF (botón protegido).
6. **Ida y vuelta con menor (2 min)** — búsqueda ida/vuelta (IDA y
   VUELTA) con un pasajero adulto + menor sin asiento → resumen con
   dos tramos.
7. **Reportes y listados (2 min)** — `/panel/reportes/ventas/` +
   descargar CSV; `/panel/operaciones/viajes/` → viaje →
   "Listado de pasajeros (PDF provisional)" y manifiesto.
8. **Permisos (1 min)** — con el vendedor logueado intentar
   `/panel/pagos/transferencias-pendientes/` o `/panel/usuarios/` →
   acceso denegado/redirección.

Si se quiere mostrar vencimiento: `expired@` ya figura EXPIRED; el
mantenimiento corre vía `python manage.py run_operational_maintenance`.

## Evidencia fuera del repo

`%USERPROFILE%\scviajes-demo-sandro\evidencia\`: capturas PNG
(home, búsqueda, dashboard, reservas, transferencias, fulfillment,
reportes, viajes, vista vendedor, home móvil) y 3 PDF de pasajes
ficticios (`AAA-000000001/3/5.pdf`).
