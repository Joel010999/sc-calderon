# Estado y Especificación de la Etapa: Permisos, Navegación, Aislamiento y Pasajes Esperados

Fecha de implementación: 5 de noviembre de 2026
Rama de trabajo: `Joel010999/scviajes-permisos-navegacion-20261104`
Commit base: `8378d5b250c99d20ce17bf952b55bcf53576fe01`

---

## 1. Alcance Funcional Implementado

### 1.1 Navegación y estructura de interfaces
- **Operaciones:** Se eliminó la pantalla/acceso de resumen general de Operaciones (`panel:operations`), redirigiendo mediante HTTP 302 hacia el listado de Viajes (`panel:trips`).
- **Recorridos:** Se retiró el enlace de Recorridos del menú lateral y de la cabecera operativa; el acceso queda concentrado dentro de Viajes a través del botón **"Gestionar recorridos"**.
- **Colectivos:** Visibilidad y acceso restringidos estrictamente a Administrador y Superusuario. El menú lateral y la cabecera operativa condicionan su visibilidad a `can_manage_operations`. El acceso directo por URL a `/panel/operaciones/colectivos/` y a la edición/alta de unidades y butacas responde con **HTTP 403 Forbidden** para vendedores.
- **Reservas:**
  - Se eliminó "Nueva reserva" del menú lateral, conservando "Reservas" y "Venta interna".
  - Se reubicó **"Entrega de pasajes"** desde el bloque Principal hacia el interior de Reservas, integrándolo como botón de acción en la cabecera junto a "Nueva reserva manual" y "Venta interna".
  - **"Nueva reserva manual"** quedó restringida exclusivamente a Administradores y Superusuarios; los vendedores acceden únicamente al flujo de "Venta interna" (`sale_type=transfer`), impidiendo la creación de reservas manuales indefinidas sin vencimiento por parte de vendedores.
- **Pagos:**
  - En el menú lateral se dejó únicamente el enlace directo a "Pagos", suprimiendo del lateral los accesos a transferencias pendientes y reportes.
  - Se incorporaron en la cabecera de Pagos los accesos a **"Ver transferencias pendientes"** y **"Reporte de ventas"**.
- **Auditoría central y Clientes:** Conservados exclusivamente para Administrador y Superusuario; bloqueados para vendedores con HTTP 403.

### 1.2 Aislamiento server-side de vendedores
- **Dashboard y métricas:** Los contadores y sumatorias de ingresos (efectivo, transferencias, reservas confirmadas y retenidas) se filtran server-side en el QuerySet base por `seller=request.user` antes de realizar agregaciones o renderizar.
- **Reservas y pagos:**
  - Los listados solo muestran registros creados o gestionados por el vendedor autenticado.
  - Los desplegables de filtro por vendedor se ocultan para vendedores no administradores.
  - Si un vendedor introduce manualmente un parámetro `?seller=<id_ajeno>` en la URL, el filtro es ignorado y no expone ventas ni identidades de otros vendedores.
  - Vistas de detalle (`booking_detail`, `payment_detail`), acciones de liberación (`booking_release`) y reintentos de fulfillment (`booking_fulfillment_retry`) devuelven **HTTP 404 Not Found** si se solicita un identificador que no pertenece al vendedor.
- **Revisión de transferencias (`transfer_review`):**
  - La aprobación y rechazo de pagos por transferencia bancaria es potestad exclusiva de usuarios del grupo `Administrador` y superusuarios. Si un vendedor intenta enviar un POST de revisión, recibe **HTTP 403 Forbidden**.
- **Descargas seguras:**
  - Descarga de pasajes PDF (`tickets:download`): Si el usuario es un vendedor autenticado, el pasaje debe corresponder a una reserva propia (`booking__seller=request.user`), de lo contrario se responde **HTTP 404 Not Found**.
  - Descarga de comprobantes de pago (`panel:payment_voucher` y `payments:download_voucher`): Si el usuario es un vendedor autenticado, el pago debe pertenecer a su reserva, respondiendo **HTTP 404 Not Found** ante pagos ajenos.
- **Entrega de pasajes (Fulfillment):**
  - El listado en `panel:fulfillment_list` y la lista de reservas confirmadas sin trabajo se filtran estrictamente por `booking__seller=request.user` para vendedores.
  - La reconciliación manual en lote (`panel:fulfillment_reconcile`) valida en el servidor que cada ID solicitado pertenezca a ventas del vendedor autenticado, descartando y contabilizando como fallo cualquier intento sobre IDs ajenos.
- **Bandeja operativa:**
  - Todos los ítems (reservas retenidas, transferencias en revisión, pagos rechazados, entregas fallidas, tickets incompletos y notificaciones) se restringen a reservas propias.
  - La categoría de viajes próximos con pendientes (`trip`) se omite para vendedores, evitando la exposición de manifiestos y métricas globales de salidas.
- **Estructura operativa:**
  - Vendedores no pueden crear ni editar colectivos, butacas, recorridos, paradas, viajes, horarios ni tarifas, ni tampoco iniciar o finalizar viajes (todas las vistas de mutación responden HTTP 403).

### 1.3 Gestión de usuarios internos y autoridad canónica
- **Superusuarios:** Capacidad global para invitar, editar y listar Superusuarios, Administradores y Vendedores desde el panel interno personalizado (sin Django Admin).
- **Administradores:** Pueden listar e invitar únicamente Vendedores. Tienen bloqueado invitar o promover usuarios a roles de Administrador o Superusuario, con validación tanto a nivel de formulario como en POST directo anti-escalamiento.
- **Supresión de hardcodeos de autoridad:** Se eliminaron las referencias y condicionales de autorización dependientes de nombres fijos (Joel/Sandro). La autoridad propietaria previa (`is_owner`) fue reemplazada por la autoridad canónica de Django `is_superuser`; los textos comerciales preexistentes no forman parte de esa autorización.
- **Protección del sistema:**
  - Se impide que un usuario se auto-desactive o se degrade a sí mismo.
  - Se protege la existencia de al menos un superusuario activo y al menos un administrador activo en el sistema.

### 1.4 Pasajes esperados con menores gratuitos
- **Cálculo corregido:** Se corrigió el cálculo de tickets esperados para la detección de entregas incompletas en la bandeja operativa. En lugar de contabilizar asignaciones de butaca (`legs__seat_assignments`), ahora se calcula:
  $$\text{Esperados} = \text{Count}(\text{legs}, \text{distinct}=\text{True}) \times \text{Count}(\text{passengers}, \text{distinct}=\text{True})$$
  y se compara contra `Count(tickets, filter=ISSUED)`.
- **Compatibilidad total:**
  - Cubre de forma exacta tanto salidas de solo ida (1 tramo) como de ida y vuelta (2 tramos), reconociendo el pasaje correspondiente a cada menor de 5 años gratuito que viaja en brazos sin butaca (`is_lap_infant = True`).
  - No genera butacas adicionales ni altera el importe de $0,00, las comisiones o los snapshots históricos.
  - Mantiene el diseño del pasaje PDF oficial de dos talones sin alteraciones.

---

## 2. Archivos Modificados y Creados

### Modelos y migraciones
- `panel/models.py`: Incorporación del rol `"Superusuario"` en `StaffInvitation.ROLE_CHOICES`.
- `panel/migrations/0004_alter_staffinvitation_role.py`: Migración generada (`makemigrations panel`).

### Permisos y servicios
- `panel/permissions.py`:
  - `is_staff_owner` y `can_create_administrator` reemplazados por verificación canónica `user.is_superuser`.
  - `can_manage_customers` restringido exclusivamente a Administrador y Superusuario.
- `panel/context_processors.py`:
  - `can_manage_customers` actualizado a `user.is_superuser or "Administrador" in group_names`.
- `panel/staff_services.py`:
  - Ampliación de roles a `("Superusuario", "Administrador", "Vendedor")`.
  - Control de invitaciones y promociones: administradores solo invitan vendedores; superusuarios pueden invitar cualquier rol.
  - `update_staff` con protección anti-escalamiento, protección de auto-bloqueo y resguardo del último superusuario y último administrador activo.
- `panel/inbox_services.py`:
  - Soporte de parámetro `user` para aislamiento server-side de todos los ítems.
  - Corrección de cálculo de tickets incompletos considerando menores gratuitos: `Count("legs") * Count("passengers") > issued`.
  - Supresión de ítems `trip` para vendedores no administradores.

### Vistas
- `panel/operations_views.py`: `overview` redirige a `panel:trips`; `buses` y `bus_detail` protegidos con `@operations_access(write=True)`.
- `panel/trip_views.py`: `trip_detail` oculta resumen de pendientes y desactiva permisos de manifiesto para vendedores.
- `panel/staff_views.py`: Listado y formularios con alcance diferenciado por rol y protección anti-escalamiento.
- `panel/reservation_views.py`:
  - Aislamiento de filtros `seller` para vendedores.
  - Forzado de `sale_type = "transfer"` en creación para vendedores (bloqueo de manuales indefinidas).
- `panel/payment_views.py`:
  - Aislamiento de pagos y transferencias pendientes.
  - `transfer_review` exclusivo para Administrador y Superusuario (HTTP 403 a vendedores).
  - `download_voucher` protegido para permitir descarga únicamente de comprobantes propios a vendedores.
- `panel/fulfillment_views.py`:
  - Listado de trabajos y reservas confirmadas filtradas por vendedor.
  - Reconciliación en lote con validación server-side de propiedad del trabajo o reserva.
- `panel/inbox_views.py`: Pasa `user=request.user` a `build_inbox`.
- `payments/views.py`: `download_voucher` protegido server-side contra descargas de comprobantes ajenos por vendedores.
- `tickets/views.py`: `download_ticket_view` protegido server-side contra descargas de pasajes ajenos por vendedores.

### Plantillas
- `templates/panel/base.html`: Limpieza de enlaces en menú lateral (sin "Nueva reserva", sin "Operaciones", sin "Recorridos", solo "Pagos", "Colectivos" condicionado a admin).
- `templates/panel/reservations/list.html`: Inclusión de "Entrega de pasajes"; "Nueva reserva manual" solo para admin; dropdown de vendedor aislado.
- `templates/panel/payments/list.html`: Inclusión de "Ver transferencias pendientes" y "Reporte de ventas"; dropdown de vendedor aislado.
- `templates/panel/operations/base.html`: Retiro de Operaciones y Recorridos de la barra de navegación; Colectivos condicionado a admin.
- `templates/panel/operations/trip_detail.html`: Restricción de aviso de pendientes y acciones de manifiesto solo a admin.
- `templates/panel/staff/list.html`: Visualización y asignación del rol Superusuario.
- `templates/panel/dashboard.html`: Enlace de operaciones apunta a `panel:trips`.

### Pruebas
- `panel/test_staff_management.py`: Pruebas actualizadas con autoridad de superusuario y protecciones de último administrador.
- `panel/test_permisos_navegacion_20261104.py`: Nueva suite integral con 16 casos de prueba reales sobre aislamiento A/B, descargas ajenas, navegación, escalamiento y cálculo de menores gratuitos.

---

## 3. Estado de Pruebas y Validación Técnica

- **Ruff linter:** `python -m ruff check .` → Todos los archivos superados sin observaciones.
- **Django system check:** `python manage.py check` → 0 problemas identificados.
- **Migraciones:** `python manage.py makemigrations --check --dry-run` → Sin cambios pendientes.
- **Git diff whitespace:** `git diff --check` → Código limpio sin conflictos.
- **Pruebas automatizadas ejecutadas:**
  - `panel.test_permisos_navegacion_20261104`: 16 tests, 100% OK.
  - `panel.test_staff_management`: 11 tests, 100% OK.
  - `panel.test_inbox`: 3 tests, 100% OK.
  - Entorno de prueba: SQLite efímero en memoria, locmem email, storage temporal en directorio temporal.
