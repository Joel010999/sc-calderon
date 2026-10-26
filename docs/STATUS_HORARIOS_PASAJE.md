# Estado de Implementación: Horarios Estimados por Parada y Borrador Reutilizable del Pasaje PDF

## 1. Base, Rama y Aislamiento

- **Repositorio principal**: `D:\renderbyte\scviajes`
- **Worktree aislado**: `C:\Users\JOEL\orca\workspaces\scviajes\scviajes-horarios-pasaje-20261026`
- **Rama de trabajo**: `Joel010999/scviajes-horarios-pasaje-20261026`
- **Commit base verificado (`origin/main`)**: `1c3e6f168d498f83f473ef67fd385525b3d4e857` (`feat(sales): add lap infant companion and minor rules`)
- **Verificación de `origin/main`**: `git fetch origin` ejecutado sin errores; `origin/main` coincide con `1c3e6f168d498f83f473ef67fd385525b3d4e857`.
- **Migraciones**: No se requirieron nuevas migraciones de base de datos, ya que `operations.TripStop.scheduled_at` y `tickets.Ticket` expresan íntegramente los horarios programados/estimados por parada y las instantáneas históricas inmutables del pasaje.

## 2. Recursos Locales Encontrados y Política de Inclusión

Se verificó el directorio externo `D:\renderbyte\referencias-scviajes\`:

1. **`D:\renderbyte\referencias-scviajes\pasaje editado.docx`** (`39.258` bytes, extensión `.docx` comprobada):
   - Leído íntegramente (párrafos y tablas 1, 2 y 3).
   - Define la estructura objetivo de dos talones (`Talón superior` y `Talón para el pasajero`), el listado de campos automáticos vs. fijos pendientes de Sandro, el borrador de condiciones generales y el cuestionario de 10 puntos para Sandro.
   - **No incluido en el repositorio ni en el commit** (conforme a las reglas de alcance).
2. **`D:\renderbyte\referencias-scviajes\pasaje referencia.pdf`** (`100.810` bytes, extensión `.pdf` comprobada):
   - Inspeccionado visualmente y por texto como referencia visual de diagramación en dos talones.
   - **Ningún dato fiscal, razón social, CUIT, IIBB, aseguradora, número de boleto, código, teléfono, dirección ni recargo/descuento (`GRAL URQUIZA`, `SUV-212798751-0`, `SCX001`, `30-54622766-5`, `908-30716745445`, `Protección Mutual`, `+5491144945021`, `Calle 4 y 41`, `Bv. Juan Domingo Peron 380`, `Tasa Emb.`, `Cortesia 10%`) fue utilizado ni copiado al código o plantillas.**
   - **No incluido en el repositorio ni en el commit.**
3. **`D:\renderbyte\referencias-scviajes\scviajes logo.png`** (`810.423` bytes, extensión `.png` comprobada):
   - Incorporado al proyecto en la ruta exacta: `static/assets/scviajes-logo.png` (referenciado en `tickets/rendering/template.py` mediante `LOGO_ASSET_REL_PATH = Path("static/assets/scviajes-logo.png")`).
4. **Recursos faltantes / pendientes de entrega por el cliente**:
   - Versión final aprobada por Sandro del documento Word con los textos definitivos de condiciones generales y datos fiscales/aseguradora cerrados.

## 3. Archivos Modificados y Nuevos

- `operations/services.py`: Servicio transaccional `update_trip_schedules(*, trip, schedules)` que reutiliza `TripStop.scheduled_at` con bloqueo `select_for_update()` sobre `Trip`, `Bus` y `TripStop`, validando estado operativo (`SCHEDULED` / `BOARDING`), pertenencia de cada parada al recorrido del viaje, secuencia consecutiva, fechas conscientes de zona horaria (`America/Argentina/Buenos_Aires`), orden estrictamente creciente (incluyendo cruces de medianoche) y ausencia de superposición de horarios del mismo colectivo.
- `panel/trip_forms.py`: Endurecimiento de `LocalDateTimeField` con `DATETIME_LOCAL_INPUT_FORMATS` explícitos que exigen fecha y hora (`HH:MM`) y rechazan cadenas de solo fecha para nunca autocompletar faltantes como `00:00`; incorporación de `TripStopScheduleForm`.
- `panel/trip_services.py`: Funciones `can_modify_trip_schedules` y `save_trip_schedules` protegidas con `require_operations_manager(actor)` (solo Sandro / grupo `Administrador` o superusuario), validación de salida futura y registro atómico de auditoría sanitizada (`AuditEvent.Action.UPDATE` sobre `operations.Trip` con instantáneas `before` y `after` de paradas y horarios).
- `panel/trip_views.py` y `panel/urls.py`: Vista personalizada `trip_stops_schedule` en `/panel/operaciones/viajes/<int:trip_pk>/horarios/` (`panel:trip_stops_schedule`) con soporte `GET` y `POST+CSRF`.
- `templates/panel/operations/trip_detail.html` y `templates/panel/operations/trip_schedule_form.html`: Interfaz en español argentino para visualizar y cargar/editar los horarios estimados de llegada a cada parada en zona `America/Argentina/Buenos_Aires`.
- `static/assets/scviajes-logo.png`: Logo institucional PNG incorporado desde `D:\renderbyte\referencias-scviajes\scviajes logo.png`.
- `tickets/rendering/data.py`: Ampliación retrocompatible de `TicketData` con los campos del borrador reutilizable de dos talones y valores por defecto explícitos (`Pendiente de confirmación con Sandro`).
- `tickets/rendering/template.py`: Renderizado del borrador reutilizable en ReportLab con ambos talones (`TALÓN SUPERIOR` y `TALÓN PARA EL PASAJERO`), separación entre `Agencia: SC VIAJES` y `Empresa transportista`, vendedor visible únicamente cuando corresponde (omitido en ventas web), fechas y horarios de salida en parada de embarque y arribo estimado en parada de destino, ubicación conocida de paradas, medio de pago e importes reales en `Decimal`, preservación de menores sin butaca (`Sin butaca`) y único código QR de verificación embebido.
- `tickets/services.py`: Enriquecimiento de `issue_tickets_for_booking` convirtiendo `leg.departure_at`, `leg.arrival_at` y `effective_now` con `timezone.localtime(...)` (`America/Argentina/Buenos_Aires`), resolviendo vendedor solo para canal `MANUAL`, medio de pago real desde `booking.payments` e inmutabilidad de pasajes/PDFs ya emitidos.
- `operations/tests.py`, `panel/test_trips.py` y `tickets/tests.py`: Nuevas suites de pruebas cubriendo permisos, `POST+CSRF`, auditoría sanitizada, cruces de medianoche, rechazo de faltantes sin `00:00`, estructura PDF de dos talones, ausencia de vendedor en ventas web, medio de pago, menores sin asiento y preservación de PDFs emitidos.

## 4. Contratos y Definiciones Pendientes de Sandro

Los siguientes datos fijos se mantienen desacoplados como configuración (`getattr(settings, ...)` con fallback explícito `"Pendiente de confirmación con Sandro"`), sin inventar valores ni pedirlos en cada venta:

1. **Empresa transportista (`TICKETS_CARRIER_NAME`)**: Nombre comercial de la empresa transportista que presta efectivamente el servicio y su relación legal con `SC VIAJES` (agencia).
2. **Datos fiscales de la transportista (`TICKETS_CARRIER_LEGAL_NAME`, `TICKETS_CARRIER_CUIT`, `TICKETS_CARRIER_VAT_CONDITION`, `TICKETS_CARRIER_ACTIVITY_START`, `TICKETS_CARRIER_IIBB`)**: Razón social, CUIT, condición frente al IVA, fecha de inicio de actividades e Ingresos Brutos.
3. **Compañía aseguradora (`TICKETS_INSURER_NAME`, `TICKETS_INSURANCE_POLICY_REF`)**: Nombre oficial de la aseguradora y póliza o referencia vigente.
4. **Direcciones físicas exactas de paradas (`TICKETS_STOP_ADDRESSES`)**: Dirección exacta o boletería/plataforma de cada parada (`CBA`, `JMA`, `GUE`, `PER`, `PAL`, `SSJ`). Actualmente se informa la ciudad y provincia real registrada en `operations.Stop` indicando dirección exacta pendiente.
5. **Calidad / servicio y anuncio de plataforma (`TICKETS_SERVICE_QUALITY_DISPLAY`, `TICKETS_PLATFORM_ANNOUNCEMENT`)**: Texto fijo aplicable o confirmación de ocultamiento definitivo.
6. **WhatsApp oficial de contacto (`TICKETS_CONTACT_WHATSAPP`)**: Número oficial de atención de SC Viajes para el talón del pasajero.
7. **Condiciones generales del pasaje**: Validación legal de Sandro sobre equipaje, tiempo de presentación previo al embarque y política comercial de devoluciones/cambios (no implementada en el sistema).

## 5. Ambigüedades Detectadas y Criterio Aplicado

- **Privacidad del documento en el PDF**: El borrador Word menciona `Documento: [Tipo y número]`, mientras que la especificación y las pruebas existentes de `tickets` exigen enmascarar el documento (`DNI ***3456`) en el PDF para no exponer el número completo. Se conservó el documento enmascarado (`mask_document`) cumpliendo las garantías de privacidad vigentes.
- **Política de edad de menores sin butaca**: De acuerdo con las instrucciones de la tarea, la regla de edad de menores de 5 años corresponde a otra tarea paralela; en `tickets` se respetó el snapshot `passenger.is_lap_infant` y `accompanied_by` existente, mostrando `Sin butaca` y `ARS 0,00` sin inventar asientos falsos.

## 6. Validaciones Ejecutadas

- `python -m ruff check`: **No disponible en el entorno** (`No module named ruff`, ni binario `ruff` en el `venv` o `PATH`). Registrado como impedimento de herramienta local.
- `python manage.py check`: **OK** (`System check identified no issues (0 silenced)`).
- `python manage.py makemigrations --check --dry-run`: **OK** (`No changes detected`).
- `git diff --check`: **OK** (sin errores de espacios ni conflictos de formato).
- **Pruebas de los módulos afectados (`python manage.py test operations panel.test_trips tickets`)**: **OK** (`172` pruebas ejecutadas, `169` pasadas, `3` salteadas por requerir motor PostgreSQL; `0` fallos).
- **Suite completa del proyecto (`python manage.py test`)**: Ejecutadas `597` pruebas sobre base temporal SQLite en memoria (`589` pasadas, `7` salteadas por requerir PostgreSQL, `1` error preexistente ajeno en `core.test_private_storage_email.PrivateStorageAndEmailConfigurationTests.test_s3_storage_is_private_and_has_no_public_url` por ausencia del paquete opcional `django-storages` en `D:\renderbyte\scviajes\venv`).
- **PostgreSQL**: No disponible en el entorno local sin configuración de base externa; las pruebas generales se ejecutaron contra SQLite temporal en memoria sin ejecutar migraciones contra bases persistentes.
