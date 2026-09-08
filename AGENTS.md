# Reglas obligatorias de desarrollo de SC Viajes

Antes de modificar el proyecto, leer:

- [Especificación del producto](docs/PROJECT_SPEC.md).
- [Arquitectura](docs/ARCHITECTURE.md).
- [Decisiones confirmadas y pendientes](docs/DECISIONS.md).
- [Estado del proyecto](docs/STATUS.md).

## Arquitectura e interfaces

- Nunca trabajar directamente sobre `main`.
- No usar ni reactivar Django Admin. Todo el panel interno debe ser 100 % personalizado.
- Mantener una arquitectura de monolito modular en Django.
- PostgreSQL es la base de datos de producción.
- Las interfaces deben estar en español argentino.
- Usar la zona horaria `America/Argentina/Buenos_Aires`.
- No usar CDN de Tailwind ni dependencias frontend remotas.
- No introducir Celery, Redis u otra infraestructura sin una decisión aprobada.

## Datos, seguridad y reglas de negocio

- No almacenar secretos ni archivos `.env`.
- No ejecutar migraciones contra bases reales.
- Toda regla de negocio debe tener pruebas.
- No modificar modelos sin generar su migración correspondiente. Generar una migración no autoriza ejecutarla contra una base real.
- Usar `Decimal`, nunca `float`, para dinero.
- Las fechas y horas deben ser conscientes de zona horaria.
- No borrar ni sobrescribir cambios locales ajenos.
- Las decisiones pendientes no deben resolverse por suposición: deben registrarse y consultarse.

## Validación y entrega

- Antes de entregar, ejecutar `git diff --check`, `python manage.py check` y los tests.
- Las validaciones deben respetar las restricciones del prompt y no utilizar bases reales. Registrar cualquier impedimento sin omitirlo ni resolverlo por suposición.
- No hacer commit, push, merge ni despliegue salvo que el prompt lo autorice expresamente.
- Actualizar `docs/STATUS.md` cuando finalice cada módulo.
