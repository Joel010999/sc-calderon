# Entrega: rutas y Güemes — 2026-10-24

## Identificación

- Rama: `feature/sandro-rutas-guemes-20261024`
- Worktree: `D:\\renderbyte\\scviajes-wt-rutas-20261024`
- Base: `2b47af4faf7e33eff5385c7bdd1b2f9739c3eaff` (`origin/main` al iniciar)
- No se modificaron los documentos canónicos de `docs`.

## Implementado

- `Stop.province` se usa explícitamente para validar que origen y destino estén en provincias diferentes; no se deduce la provincia por el nombre.
- `Route.allows_journey`, `TripFare`, reservas, disponibilidad, búsqueda pública y formulario del panel rechazan tramos dentro de una misma provincia.
- La configuración controlada de operaciones incorpora Güemes (`GUE`, provincia Salta):
  - Córdoba → Jujuy: Córdoba, Jesús María, Güemes, Perico, Palpalá, San Salvador de Jujuy.
  - Jujuy → Córdoba: San Salvador de Jujuy, Palpalá, Perico, Güemes, Jesús María, Córdoba.
- Se conservaron permisos de subida/bajada, orden, tarifas por tramo y ausencia de horarios o importes inventados.
- `setup_initial_operations --dry-run` informa cambios sin escribir. La ejecución es idempotente; puede completar una ruta heredada sin viajes, pero rechaza modificar una ruta parcial que ya tiene viajes para no reescribir históricos.
- El escenario demo ahora declara provincias sintéticas explícitas (Córdoba/Jujuy), manteniendo reservas válidas bajo la nueva regla.

## Migraciones y datos

No hubo cambios de modelos persistentes que requieran migración. `python manage.py makemigrations --check --dry-run` informó `No changes detected`. No se ejecutaron migraciones contra bases persistentes ni se modificaron viajes históricos.

## Validación

Con base temporal Django/SQLite, correo locmem, storage temporal y datos sintéticos:

- `python manage.py test operations.tests --noinput`: 53 OK.
- `python manage.py test sales.tests core.test_checkout --noinput`: 113 OK, 1 omitido.
- `python manage.py test panel.test_operations panel.test_trips --noinput`: 89 OK.
- `python manage.py test core.test_demo_scenario --noinput`: 4 OK.
- Suite completa: `Ran 567 tests in 479.206s`, `OK (skipped=7)`, código de salida 0.
- `python -m ruff check .`: `All checks passed!`
- `python manage.py check`: sin problemas.
- `python manage.py makemigrations --check --dry-run`: sin cambios.
- `git diff --check`: sin errores; Git solo informó advertencias de conversión LF/CRLF.

Las pruebas cubren ambos sentidos, pares permitidos, misma provincia, orden inverso, paradas ajenas al viaje, manipulación POST, dry-run, idempotencia y conservación de históricos.

## No ejecutado / integración

No se ejecutaron PostgreSQL ni pruebas de navegador/Playwright en esta entrega; la suite validada usa la base temporal local. Debe repetirse la matriz de rutas con PostgreSQL antes de integrar. Güemes requiere ejecutar el comando controlado sobre la configuración operativa del entorno correspondiente, con revisión de sus viajes existentes; el comando no modifica automáticamente rutas con viajes.

No se modificaron emisión, manifiestos, pasajeros, tarifas por salida ni proveedores externos. No hubo push, PR, merge a `main`, despliegue ni migración persistente.
