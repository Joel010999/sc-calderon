# Toolkit local para descubrir exports heredados

Este comando inventaría únicamente archivos locales sintéticos o entregados para la sesión:

```text
python manage.py inventory_legacy_exports --input RUTA_EXPORTS --output inventario.json --format json
python manage.py inventory_legacy_exports --input RUTA_EXPORTS --output inventario.md --format markdown --markdown-output inventario-resumen.md
python manage.py inventory_legacy_exports --input RUTA_EXPORTS --dry-run --format json
```

Acepta CSV, TSV y XLSX con varias hojas. No acepta XLSM, macros, enlaces simbólicos ni rutas fuera del directorio indicado. No usa Internet, Google, AppSheet, una base de datos ni un motor de fórmulas. Los valores de celdas nunca se copian al reporte; sólo se informan estructura, conteos, tipos generales, hashes SHA-256 y nombres de columnas sanitizados.

La salida se escribe atómicamente, no sobrescribe por defecto y puede permitirlo sólo con `--allow-overwrite`. Los límites de archivos, bytes, filas, columnas y hojas son argumentos explícitos. Los errores son códigos estables y no incluyen rutas completas ni datos de celdas.

## Checklist para la sesión con Sandro

1. Solicitar una copia local y verificar que no contenga credenciales, archivos reales innecesarios o accesos configurados.
2. Crear un directorio temporal fuera del repositorio y copiar allí sólo los exports autorizados; no seguir symlinks.
3. Ejecutar primero `--dry-run`; conservar únicamente el resumen estructural sanitizado.
4. Revisar nombres de archivos, hojas, columnas sensibles, errores, duplicados y relaciones candidatas como hipótesis.
5. Confirmar con Sandro el significado de cada hoja y columna antes de diseñar un importador.
6. No mapear ni importar datos durante esta sesión; no asumir equivalencias con modelos Django.
7. Guardar el JSON/Markdown fuera del repositorio, con acceso restringido y sin valores de celdas.
8. Destruir el directorio temporal y el reporte cuando termine la sesión, según la política acordada.

Este toolkit no implementa importación, migración, conexión a cuentas ni sincronización.
