# Integración Visual del Panel Rojo en SC Viajes

**Fecha:** 6 de noviembre de 2026
**Rama:** `Joel010999/panel-rojo-integracion`
**Commit base:** `5f1b06e54e7202ad1e63f3d5fe45e9c9a859a464`
**Referencia visual:** `design/panel-rojo` (commit `425a4036436d9270c13c57ed06ef816faacb236f`)

---

## 1. Alcance y Objetivos

Se implementó la integración de la propuesta visual "Panel Rojo" aprobada dentro del panel interno real de SC Viajes sobre el worktree aislado `panel-rojo-integracion`.

### Principios y Restricciones Cumplidas:
1. **Identidad visual roja y componentes locales:**
   - Paleta de diseño basada en tokens CSS de la propuesta: `--color-primary-base: #dc2626` (rojo principal), `--color-primary-dark: #b91c1c`, `--color-primary-subtle: #fef2f2`, neutros Slate de alto contraste (`#0f172a`, `#334155`, `#64748b`), acentos ámbar (`#d97706`), verde esmeralda (`#059669`) y azul cielo (`#0284c7`).
   - Sistema tipográfico `system-ui` con jerarquía clara, espaciado uniforme de 4px/8px/12px/16px/24px y bordes redondeados consistentes (`border-radius: 8px / 12px / 16px`).
   - Recursos 100 % locales sin CDN ni librerías frontend externas (`static/panel/css/panel.css`, `static/panel/css/operations.css`, logo local `static/assets/scviajes-logo.png`).
2. **Plantillas del panel real actualizadas:**
   - Base y layout unificado (`templates/panel/base.html` con estructura `app-shell`, drawer colapsable accesible para móvil con tecla Escape y foco gestionado, breadcrumbs, perfil de usuario, insignias de rol y logout). Enlace de 'Nueva reserva' retirado del menú lateral conforme a la propuesta de diseño aprobada, manteniéndose exclusivamente accesible de forma contextual dentro de Reservas y Venta interna.
   - Acceso a login (`templates/panel/login.html`).
   - Dashboard principal (`templates/panel/dashboard.html`).
   - Bandeja operativa / Inbox de reservas (`templates/panel/operational_inbox.html`).
   - Ventas e informes (`templates/panel/reports/sales.html`).
   - Fulfillment y reconciliación de pasajes (`templates/panel/fulfillment/list.html`).
   - Revisión de transferencias pendientes (`templates/panel/payments/pending_transfers.html`).
   - Clientes (`templates/panel/customers/list.html`, `templates/panel/customers/detail.html`).
   - Auditoría central (`templates/panel/audit/list.html`, `templates/panel/audit/detail.html`).
   - Gestión de equipo / Staff (`templates/panel/staff/list.html`, `templates/panel/staff/invite.html`, `templates/panel/staff/accept.html`).
   - Operaciones de viajes, recorridos y butacas (`static/panel/css/operations.css`).
3. **Respeto estricto del backend, permisos y reglas de negocio:**
   - No se alteraron modelos, migraciones ni reglas de negocio.
   - Formularios reales Django con protección CSRF, errores inline/resumen y filtros preservados intactos.
   - Paginación nativa de Django preservada y estilizada con estados de página activos y deshabilitados.
   - Enlace oficial de descarga del Listado Provisional de Pasajeros en PDF accesible desde el detalle del viaje para administradores.
   - No se reintrodujo caja ni efectivo ni validación de embarque.
   - No se modificaron la web pública ni las plantillas de pasajes PDF.
   - Todos los demás worktrees permanecieron intactos.

---

## 2. Matriz de Validación Técnica

| Verificación | Comando / Herramienta | Resultado | Detalle |
|---|---|---|---|
| **Linter de código** | `python -m ruff check .` | **PASS (0 errores)** | Código Python limpio y conforme a los estándares. |
| **Integridad Django** | `python manage.py check` | **PASS (0 problemas)** | Configuración, URLs y componentes sin errores. |
| **Migraciones** | `python manage.py makemigrations --check --dry-run` | **PASS (Sin cambios)** | No se alteraron modelos ni esquemas de datos. |
| **Diff de espacios/formato** | `git diff --check` | **PASS (0 alertas)** | Sin espacios en blanco sobrantes ni saltos de línea inválidos. |
| **Pruebas de navegador (CDP)** | `scratch/validate_browser_ui.py` con Chrome headless | **PASS (23/23 casos)** | Base SQLite efímera, aislamiento de vendedor y admin, desktop 1440px y móvil 390px, foco accesible, contraste, cierre de menú con Escape, 0px de desborde horizontal. |
| **Capturas de pantalla** | Guardadas fuera del repositorio | **18 imágenes PNG** | Almacenadas en `C:\Users\JOEL\AppData\Local\Temp\scviajes_panel_rojo_screenshots\`. |
| **Suite completa Django** | `python manage.py test -v 2` | **PASS (692 ejecutados, 0 fallos)** | 697 descubiertos, 692 ejecutados en 528.58s, 6 skipped (pruebas específicas de concurrencia PostgreSQL). |

---

## 3. Resumen Literal de Ejecución de Pruebas

```text
Found 697 test(s).
...
Ran 692 tests in 528.578s

OK (skipped=6)
```

Las 6 pruebas omitidas corresponden de manera esperada a las pruebas de concurrencia y bloqueos transaccionales específicas de PostgreSQL (`tickets.tests.TicketsPostgresConcurrencyTests`, `sales.tests.SalesPostgresConcurrencyTests`, etc.), las cuales requieren un servidor PostgreSQL 16 activo.

---

## 4. Archivos Modificados

- `static/panel/css/panel.css`
- `static/panel/css/operations.css`
- `templates/panel/base.html`
- `templates/panel/login.html`
- `templates/panel/dashboard.html`
- `templates/panel/operational_inbox.html`
- `templates/panel/reports/sales.html`
- `templates/panel/fulfillment/list.html`
- `templates/panel/payments/pending_transfers.html`
- `templates/panel/customers/list.html`
- `templates/panel/customers/detail.html`
- `templates/panel/audit/list.html`
- `templates/panel/audit/detail.html`
- `templates/panel/staff/list.html`
- `templates/panel/staff/invite.html`
- `templates/panel/staff/accept.html`
- `.gitignore`
- `panel/test_integration_ventas_recorridos.py`
- `docs/STATUS.md`
- `docs/STATUS_INTEGRACION_PANEL_ROJO_20261106.md` (este documento)
