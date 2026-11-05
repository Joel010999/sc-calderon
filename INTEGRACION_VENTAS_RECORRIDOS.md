# Informe de Integración: Ventas Internas por Transferencia y Recorridos Versionados

## 1. Resumen Ejecutivo
Se integraron secuencialmente y sin pérdida de commits en un worktree nuevo basado en `origin/main` (`131552b990b7dfccddf5b8507e9e0cadc826d6f4`) las ramas de gestión versionada de recorridos y de ventas internas por transferencia bancaria:
1. `e87e8cf7b49015c847e7c21135ec8b565282e364` (Checkpoint: gestión versionada de recorridos)
2. `05fac358248efc43fc31c847d1e8affcdec7918f` (Checkpoint NO APROBADO: navegación de recorridos; inspeccionado y conservado)
3. `c2dd16aac3a0d25e758775f885ca30d20375692e` (Ajustar permisos esperados del manifiesto)
4. `6d0b10935f87b8fa8f695b6b9fe7b760c448f3b4` (Implementar ventas internas por transferencia)

Ambas funcionalidades quedaron plenamente operativas y armonizadas, resolviendo las conexiones pendientes y respetando las restricciones de arquitectura sin Django Admin, sin CDN ni dependencias frontend remotas, y sin introducir infraestructura no autorizada.

---

## 2. Resoluciones de Integración y Compatibilidad

### 2.1 Conexión de `panel/internal_sales_urls.py`
- Se incluyó `path('internal-sales/', include('panel.internal_sales_urls'))` en `panel/urls.py`.
- No duplica rutas ni nombres: expone el namespace anidado `panel:internal_sales:` (`pending_transfers`, `transfer_review`, `voucher_download`) bajo `/panel/internal-sales/transferencias/...`.
- No oculta ni modifica los endpoints existentes bajo `/panel/pagos/` (`panel:pending_transfers`, `panel:transfer_review`, `panel:payment_voucher`).
- Se verificó mediante clientes HTTP que ambas rutas responden correctamente y aíslan el alcance de acceso.

### 2.2 Accesos por Rol: Reserva Manual vs Venta Interna
- **Administrador / Superusuario**:
  - Acceso completo a **Reserva manual rápida sin vencimiento** (`expires_at = None`).
  - Acceso completo a **Venta interna por transferencia** con retención temporal (`sale_type=transfer` / `create_internal_transfer_booking`) o confirmación inmediata con comprobante y pago verificado (`create_immediate_transfer_sale`).
  - Enlaces duales presentes en el menú lateral ("Nueva reserva" y "Venta interna") y en el listado de reservas.
- **Vendedor**:
  - Acceso exclusivo a **Venta interna**, sin posibilidad de crear reservas manuales sin vencimiento.
  - El menú lateral y el listado de reservas muestran únicamente "Venta interna" / "Nueva venta interna".
  - En `booking_create`, los vendedores siempre generan ventas por transferencia con retención temporal (`expires_at` calculado) o ventas confirmadas en última hora.
  - Si un vendedor intenta invocar `create_panel_manual_booking` con `expires_at = None`, es bloqueado con `PermissionDenied` mediante `require_can_create_manual_unexpired_reservation`.

### 2.3 Conservación de Navegación de Recorridos en Viajes
- Se conservó intacto el botón "Gestionar recorridos" en `templates/panel/operations/trips.html` (`panel:routes`).
- Se mantuvo el acceso a Recorridos en la navegación lateral de Operaciones (`panel:routes`).
- Se respetó la prohibición de limpieza general del lateral: se mantuvieron todas las secciones existentes (Principal, Reservas, Pagos, Caja, Embarque, Operaciones) sin gráficos ni eliminaciones.

### 2.4 Reglas de Vencimiento y Cortes
- **Cálculo de vencimiento**: `expires_at = min(medianoche AR al término del día de compra, 1h antes de salida inicial)`. Evaluado estrictamente con `departure_at` de la salida inicial y no de paradas intermedias.
- **Venta en última hora**: entre 60 y 0 minutos antes de la salida inicial, queda prohibido crear retenciones pendientes; exige obligatoriamente comprobante adjunto válido y declaración expresa de pago verificado para confirmación inmediata atómica.
- **Autorización previa a retornos idempotentes**: en `_review_transfer_payment_atomic`, `validate_payment_reviewer(reviewer, payment=payment)` se ejecuta antes de cualquier retorno temprano por idempotencia. Un vendedor ajeno recibe rechazo (`ValidationError` en dominio o `404 Not Found` en panel por aislamiento de queryset `_visible_payments`), impidiendo auditar o validar operaciones ajenas.
- **Canal ONLINE**: se comprobó que el canal online conserva su retención inmutable de 15 minutos (`SALES_ONLINE_HOLD_MINUTES`).

### 2.5 Versionado de Recorridos y Protección de Paradas
- La edición de un recorrido con viajes programados desactiva la versión previa y crea una nueva versión (`version = base.version + 1`, `parent_route = base`) con sus nuevas paradas.
- Los viajes históricos existentes conservan su relación inmutable con la versión original.
- Las paradas vinculadas a viajes históricos quedan congeladas en sus atributos críticos (`code`, `name`, `city`, `province`), impidiendo alteraciones indirectas sobre tramos pasados y reglas interprovinciales.

---

## 3. Archivos Modificados y Creados

### Archivos Modificados en la Integración:
- `panel/urls.py`: Inclusión de `path('internal-sales/', include('panel.internal_sales_urls'))`.
- `panel/context_processors.py`: Incorporación de `can_create_manual_unexpired` y `can_access_internal_sales` para control granular en plantillas.
- `templates/panel/base.html`: Segmentación de enlaces en el menú lateral (Admin: "Nueva reserva" + "Venta interna"; Vendedor: "Venta interna").
- `templates/panel/reservations/list.html`: Botones de acción contextuales según rol del usuario.
- `templates/panel/reservations/create.html`: Encabezados, textos explicativos, campo oculto `sale_type` y botón de submit adaptados dinámicamente al flujo de venta interna o reserva manual.
- `panel/reservation_views.py`: Soporte para parámetro `sale_type` en `booking_create`, permitiendo a los administradores operar indistintamente ambos flujos y asegurando que los vendedores nunca generen retenciones indefinidas.

### Archivos Nuevos:
- `panel/test_integration_ventas_recorridos.py`: Suite de 12 pruebas de integración completas vía cliente HTTP y servicios transaccionales.
- `INTEGRACION_VENTAS_RECORRIDOS.md`: Este informe de consolidación.

---

## 4. Evidencia de Pruebas y Control de Calidad

1. **Pruebas de Integración Nuevas (`panel.test_integration_ventas_recorridos`)**:
   - `python manage.py test panel.test_integration_ventas_recorridos`
   - **Resultado**: 12/12 pruebas exitosas en 2.878s (código de salida 0).
2. **Suite de Módulos Afectados**:
   - `python manage.py test panel.test_integration_ventas_recorridos panel.test_routes panel.test_reservations panel.test_payments sales.test_internal_transfer panel.test_manifest panel.test_trips panel.test_operations`
   - **Resultado**: 206/206 pruebas exitosas en 94.275s (código de salida 0).
3. **Suite Completa del Proyecto**:
   - `python manage.py test`
   - **Resultado**: 662 pruebas exitosas (8 skipped por aislamiento PostgreSQL) en 549.378s (código de salida 0).
4. **Linter Ruff**:
   - `python -m ruff check`
   - **Resultado**: `All checks passed!` (0 errores).
5. **Verificación de Django**:
   - `python manage.py check`
   - **Resultado**: `System check identified no issues (0 silenced).`
6. **Migraciones Pendientes**:
   - `python manage.py makemigrations --check --dry-run`
   - **Resultado**: `No changes detected` (modelos y migraciones perfectamente sincronizados).
7. **Aplicación de Migraciones en Base Temporal Vacía**:
   - `python manage.py migrate --no-input` sobre SQLite efímero nuevo.
   - **Resultado**: Las 46 migraciones del proyecto aplicaron limpiamente (código de salida 0).
8. **Git Diff Check**:
   - `git diff --check`
   - **Resultado**: 0 errores de espacios en blanco o conflictos.

---

## 5. Estado de Pruebas de Concurrencia PostgreSQL (Pendiente)

- **Comprobación**: Se verificó la disponibilidad de PostgreSQL en `localhost:5432` mediante `Test-NetConnection`. La conexión TCP falló (`TcpTestSucceeded: False`).
- **Declaración formal**: Conforme a la directiva ("Ejecutá concurrencia PostgreSQL con una instancia local desechable si ya está disponible; no inicies servicios automáticamente y si no está disponible declaralo pendiente"), se declara **PENDIENTE** la ejecución de pruebas nativas de concurrencia y bloqueos transaccionales `select_for_update` / advisory locks sobre PostgreSQL en el entorno local.
- **Garantías de código**: Toda la lógica crítica fue desarrollada e instrumentada con transacciones atómicas de Django (`@transaction.atomic`), orden determinístico de bloqueos por clave primaria y validaciones server-side.
