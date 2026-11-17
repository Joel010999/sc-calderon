# Informe de Implementación: Gestión de Recorridos en Viajes (SC Viajes)

## 1. Resumen Ejecutivo
Se implementó integralmente el módulo de gestión y versionado de recorridos dentro de Operaciones / Viajes, protegiendo rigurosamente la integridad de los datos históricos, viajes programados, reservas y pasajes emitidos, respetando las restricciones de arquitectura y monolito modular en Django sin recurrir a Django Admin.

---

## 2. Alcance Funcional Implementado

1. **Acceso "Gestionar recorridos" en Viajes**:
   - Se incorporó el acceso en `templates/panel/operations/trips.html` mediante un botón visible exclusivamente para administradores y superusuarios (`can_manage`).

2. **Creación y configuración de recorridos**:
   - Superusuarios y administradores pueden configurar nombre, código, paradas, orden secuencial y permisos de subida (`allows_boarding`) y bajada (`allows_alighting`).
   - Implementado en `operations/services.py`, `panel/route_forms.py` y `panel/route_services.py`.

3. **Configuración de paradas sin Django Admin**:
   - Se crearon formularios y vistas dedicadas (`StopForm`, `stop_create`, `stop_edit`, `templates/panel/operations/stop_form.html`) accesibles directamente desde el flujo de gestión operativa.

4. **Edición in-place de recorridos sin viajes asociados**:
   - Si `route.has_trips` es `False`, el recorrido y sus paradas pueden ser modificados directamente en su misma versión (actualización de código, nombre, paradas, orden y permisos).

5. **Versionado automático de recorridos en uso**:
   - Si un recorrido ya cuenta con viajes asociados (`route.trips.exists()`), cualquier modificación genera automáticamente una nueva versión (`version = route.version + 1`, `parent_route = route`, `is_active = True`).
   - El recorrido anterior es desactivado para nuevas programaciones pero conservado intacto con sus `RouteStop` originales.
   - Los viajes históricos, cronogramas por parada, reservas y pasajes ya emitidos preservan su relación inmutable con la versión original sin alteraciones.
   - `Route.clean()`, `RouteStop.clean()` y `RouteStop.delete()` bloquean cualquier mutación directa sobre registros vinculados a viajes.

6. **Estrategia explícita contra alteraciones indirectas en paradas compartidas**:
   - Se agregó `Stop.has_historical_trips` y validación en `Stop.clean()`: si una parada está asociada a salidas históricas (`self.trip_stops.exists()` o en recorridos con viajes), sus atributos críticos (`province`, `code`, `name`, `city`) quedan estrictamente congelados contra modificaciones in-place.
   - Si se requiere una configuración diferente de parada para un nuevo recorrido, el administrador debe crear una nueva entidad `Stop`, evitando efectos colaterales sobre salidas pasadas o reglas interprovinciales.

7. **Invariantes del recorrido y validación interprovincial**:
   - Función de dominio `validate_route_stops` en `operations/services.py`:
     - Mínimo dos paradas (origen y destino distintos).
     - Orden estrictamente positivo y correlativo (`1..N`).
     - No admite paradas duplicadas dentro del mismo recorrido.
     - La primera parada debe permitir subir (`allows_boarding=True`).
     - La última parada debe permitir bajar (`allows_alighting=True`).
     - Cada parada intermedia debe permitir al menos subir o bajar.
     - Restricción interprovincial obligatoria: debe existir al menos un tramo permitido entre provincias diferentes (`origin.province != destination.province`).

8. **Creación de salidas desde nueva versión del recorrido**:
   - `panel:trip_create` / `TripSelectionForm` expone los recorridos activos (incluyendo las nuevas versiones).
   - Se preservan cronogramas por parada en zona horaria de Argentina (`America/Argentina/Buenos_Aires`), tarifas por categoría (`CAMA` y `SEMI_CAMA`), y prevención transaccional de superposición de horarios del colectivo (`schedule_trip`).

9. **Bloqueo del cambio de colectivo en viajes creados**:
   - `Trip.clean()` valida que no se modifique `bus_id` en viajes ya creados.

10. **Alineación de permisos para Vendedores**:
    - Los vendedores sólo pueden consultar viajes próximos/pasados (`panel:trips`), horarios estimados y tarifas (`panel:trip_detail`) para comercialización.
    - Se bloqueó el acceso de vendedores a crear/modificar recorridos, paradas, salidas, horarios, tarifas e iniciar o finalizar viajes (`trip_start`, `trip_start_confirm`, `trip_complete`).

11. **Bloqueo estricto del Manifiesto y Listado Global de Pasajeros**:
    - Las vistas `trip_manifest`, `trip_manifest_print` y `trip_manifest_csv` se restringieron exclusivamente a Administradores y Superusuarios (`@operations_access(write=True)`).
    - El acceso a URLs directas devuelve `HTTP 403 Forbidden` a vendedores y personal no administrativo.
    - El enlace al manifiesto en `trip_detail.html` no se renderiza para vendedores.

12. **Distinción entre permisos de parada y validación de embarque**:
    - Los permisos `allows_boarding` y `allows_alighting` controlan la viabilidad de tramos y venta.
    - El embarque físico se valida independientemente en `tickets.BoardingRecord` (`/panel/embarques/`).

---

## 3. Documentación de Inclusiones e Impedimentos

### Inclusión pendiente en `panel/urls.py`
Conforme a las instrucciones estrictas de la tarea ("No editar panel/permissions.py, panel/urls.py, navegación lateral compartida ni documentos canónicos"):
- Se creó el módulo URL específico **`panel/route_urls.py`** con las rutas de gestión:
  - `operaciones/recorridos/` (`route_list`)
  - `operaciones/recorridos/nuevo/` (`route_create`)
  - `operaciones/recorridos/<int:pk>/` (`route_detail`)
  - `operaciones/recorridos/<int:pk>/editar/` (`route_edit`)
  - `operaciones/recorridos/<int:pk>/nueva-version/` (`route_new_version`)
  - `operaciones/recorridos/<int:pk>/activar/` (`route_activate`)
  - `operaciones/recorridos/<int:pk>/desactivar/` (`route_deactivate`)
  - `operaciones/paradas/nueva/` (`stop_create`)
  - `operaciones/paradas/<int:pk>/editar/` (`stop_edit`)
- **No se conectó en `panel/urls.py`**. Queda documentada aquí la inclusión pendiente para que el coordinador o integrador la conecte cuando corresponda.

### Impedimento de actualización en `docs/STATUS.md`
- Conforme a los límites estrictos ("No editar... documentos canónicos") y la regla ("Actualizar docs/STATUS.md sólo si el alcance del módulo realmente queda completo; si la restricción de no editar documentos canónicos impide esto, documentá el impedimento en el archivo nuevo"):
  - Se registra que **no se modificó `docs/STATUS.md`** para preservar la inmutabilidad de los documentos canónicos establecida en las restricciones del prompt.

---

## 4. Archivos Modificados y Creados

### Modificados:
- `operations/models.py`: Se agregaron campos `version` y `parent_route` a `Route`, restricciones de unicidad `(code, version)`, validación en `clean()` para rutas en uso, inmutabilidad de `RouteStop` con viajes, y protección de paradas compartidas en `Stop.clean()`.
- `operations/services.py`: Incorporación de `validate_route_stops` y `save_route_configuration`.
- `panel/manifest_views.py`: Restricción de manifiesto (web, impresión y CSV) exclusivamente a administradores (`@operations_access(write=True)`).
- `panel/operations_views.py`: Delegación de `routes` a `route_views.route_list`.
- `panel/services.py`: Incorporación de `snapshot` y `record_event` para `Route` y `Stop`.
- `panel/trip_services.py`: Restricción de `transition_trip` a `require_operations_manager`.
- `panel/trip_views.py`: Restricción de `trip_start`, `trip_start_confirm`, `trip_complete` a administradores y ajuste de visibilidad del manifiesto.
- `templates/panel/operations/trips.html`: Incorporación del botón "Gestionar recorridos".
- `templates/panel/operations/routes.html`: Visualización de versiones, estado de uso, paradas y paradas disponibles.
- `panel/test_manifest.py`: Pruebas de bloqueo de manifiesto y CSV a vendedores (HTTP 403) y autorización a administradores.
- `panel/test_trips.py`: Pruebas de bloqueo de inicio/finalización de viajes a vendedores y ejecución por administradores.

### Nuevos:
- `operations/migrations/0004_route_parent_route_route_version_alter_route_code_and_more.py`: Migración de Django para el versionado de `Route`.
- `panel/route_forms.py`: Formularios `StopForm`, `RouteForm`, `RouteConfigurationForm`.
- `panel/route_services.py`: Servicios transaccionales con bitácora `AuditEvent` para recorridos y paradas.
- `panel/route_views.py`: Vistas operativas de listado, creación, edición, versionado y activación/desactivación.
- `panel/route_urls.py`: Módulo URL específico para gestión de recorridos y paradas.
- `templates/panel/operations/route_form.html`: Plantilla de edición/creación/versionado de recorridos y paradas.
- `templates/panel/operations/stop_form.html`: Plantilla de edición y creación de paradas.
- `panel/test_routes.py`: 15 pruebas unitarias e integración sobre versionado, invariantes, protección de paradas compartidas y roles.
- `GESTION_RECORRIDOS.md`: Este informe de entrega.

---

## 5. Validación y Pruebas Ejecutadas

1. **Linter Ruff**:
   - Comando: `python -m ruff check`
   - Resultado: `All checks passed!` (0 errores).

2. **Django System Check**:
   - Comando: `python manage.py check`
   - Resultado: `System check identified no issues (0 silenced).`

3. **Django Migrations Check**:
   - Comando: `python manage.py makemigrations --check --dry-run`
   - Resultado: `No changes detected` (migraciones completamente al día).

4. **Git Diff Check**:
   - Comando: `git diff --check`
   - Resultado: Sin errores de conflicto ni espacios en blanco.

5. **Suite de Pruebas Afectadas**:
   - Comando: `python manage.py test operations panel.test_routes panel.test_trips panel.test_manifest panel.test_operations`
   - Resultado: `Ran 181 tests in 12.441s. OK (skipped=1)`. 0 fallos, 0 errores.
   - Entorno: SQLite en memoria temporal de testing, sin tocar bases de datos reales.

---

## 6. Evidencia de Configuración del Agente
- **launch.requested**: agent=antigravity, model=gemini-3.8-flash-medium, effort=medium
- **launch.effective**: agent=antigravity, model=gemini-3.8-flash-medium, effort=medium
- **Estado de coincidencia**: Verificado y coincidente exactamente.

---

## 7. Riesgos y Tareas Pendientes para el Coordinador
1. **Conexión de `panel/route_urls.py`**:
   - En `panel/urls.py`, incluir las rutas de `panel.route_urls` (por ejemplo mediante `include('panel.route_urls')` o agregando sus patrones) cuando finalice la fase de restricción de edición.
2. **Aplicación de Migraciones en Staging**:
   - Ejecutar `python manage.py migrate` en el entorno staging de PostgreSQL antes de desplegar.
