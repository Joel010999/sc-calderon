# Backup y restore seguro

La producción usa exclusivamente PostgreSQL. `create_database_backup` ejecuta
`pg_dump` en formato custom mediante una lista de argumentos (`shell=False`),
crea un archivo temporal en el mismo directorio, aplica permisos privados y lo
mueve atómicamente sin sobrescribir un archivo existente. La ruta indicada debe
ser absoluta y estar fuera del repositorio.

```text
python manage.py backup_preflight --json
python manage.py create_database_backup --output /var/lib/scviajes/backups/scviajes-2026-10-06.dump --dry-run --json
python manage.py create_database_backup --output /var/lib/scviajes/backups/scviajes-2026-10-06.dump --json
python manage.py verify_database_backup --backup /var/lib/scviajes/backups/scviajes-2026-10-06.dump --manifest /var/lib/scviajes/backups/scviajes-2026-10-06.dump.manifest.json --json
```

La verificación sólo ejecuta `pg_restore --list`; no restaura, modifica ni
elimina bases o archivos. SQLite rechaza backups de producción. Los resultados
son JSON estable, incluyen SHA-256 cuando corresponde y reemplazan errores de
subprocesos por mensajes sanitizados; nunca imprimen contraseñas, URLs con
credenciales, contenido del backup o stderr de las herramientas.

## Archivos privados y retención

`backup_preflight` inventaría sólo cantidad y tamaño de archivos en `MEDIA_ROOT`,
`PROTECTED_MEDIA_ROOT` y `TICKETS_STORAGE_ROOT`. No lee ni lista nombres de
archivos en la salida. `BACKUP_OUTPUT_DIR` debe ser externo al proyecto y la
retención se configura con `BACKUP_RETENTION_COUNT` y `BACKUP_RETENTION_DAYS`.
La aplicación no borra backups ni archivos privados automáticamente.

## Drill de restore

El restore es un procedimiento documentado y manual: primero verificar el dump,
crear una base temporal aislada, restringir sus credenciales y red, restaurar
allí con `pg_restore`, ejecutar checks y pruebas, y registrar resultados. Sólo
después de una autorización explícita se evalúa un cambio productivo con backup,
ventana de mantenimiento y rollback. Nunca ejecutar estos pasos sobre la base
productiva, nunca usar una base existente como destino y nunca automatizar un
restore destructivo desde Django.

Cada dump exitoso crea junto al archivo un manifest separado con versión de
esquema, versión de PostgreSQL, timestamp UTC, nombre, tamaño y SHA-256.
`verify_database_backup` compara esos valores antes de ejecutar únicamente
`pg_restore --list`. La retención se configura con `BACKUP_MINIMUM_COPIES` y
`BACKUP_RETENTION_DAYS`; esta entrega informa candidatos, pero nunca elimina.
