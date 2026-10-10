# Calidad de código

Ruff es obligatorio para Python. La versión de CI está fijada (`ruff==0.13.0`)
y la configuración vive en `pyproject.toml`; el comando local reproducible es:

```text
uvx ruff==0.13.0 check .
```

La puerta usa las reglas `E4` (errores sintácticos de estilo que suelen ocultar
errores) y `F` (errores reales, imports y variables muertos), excluye migraciones
históricas y no aplica formato automático. Los imports F401 de tests se excluyen
porque algunos módulos agregan clases de test para el descubrimiento de Django.

Exclusiones puntuales justificadas:

- `panel/inbox_services.py`, `panel/staff_services.py` y `panel/staff_views.py`:
  contienen código compacto histórico ya revisado; expandir cada sentencia sería
  una refactorización amplia sin valor de comportamiento para esta tarea.
- `setup_groups.py:E402`: `django.setup()` debe ejecutarse antes de importar
  `Group`.
- `**/migrations/**`: no se reformatean migraciones históricas.

Los hallazgos F de producción y los F841 demostrables se corrigen en origen; no
se desactiva la regla completa para ocultarlos.
