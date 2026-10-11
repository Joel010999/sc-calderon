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
## Restore drill reproducible (PostgreSQL temporal)

El comando `python manage.py restore_drill` ejecuta una prueba aislada con dos
bases PostgreSQL sintéticas locales. Migra y ejecuta `seed_demo_scenario` en el
origen, crea el backup custom mediante la infraestructura existente, valida el
manifest SHA-256 y `pg_restore --list`, rechaza destinos no vacíos y restaura
únicamente tras recibir `CONFIRMAR_RESTORE_DRILL_TEMPORAL`. La salida sólo incluye
etiquetas sintéticas, conteos agregados, coincidencia de esquema y checksums; nunca
imprime URLs, credenciales, PII, nombres de archivos privados ni tokens.

Ejemplo CI/local (las bases deben ser temporales y tener el prefijo requerido):

```text
python manage.py restore_drill --source-url postgresql://usuario:clave@127.0.0.1:5432/scviajes_restore_drill_source --destination-url postgresql://usuario:clave@127.0.0.1:5432/scviajes_restore_drill_destination --backup C:\\Temp\\scviajes_restore_drill_run.dump --confirm CONFIRMAR_RESTORE_DRILL_TEMPORAL --json
```

Además se crean dos PDFs sintéticos en un filesystem temporal: inventario,
copias, SHA-256, restauración y limpieza automática. Un fallo limpia archivos
temporales y no sobrescribe un backup o manifest existente. El workflow manual y
por cambios en backups/migraciones vive en `.github/workflows/restore-drill.yml`.

## RPO/RTO propuestos y responsabilidades

RPO propuesto: 24 horas para la base y 1 hora para archivos privados, sujeto a
aprobación. RTO propuesto: 4 horas para la base y 8 horas para archivos privados.
Operaciones ejecuta el drill y conserva la evidencia sanitizada; Desarrollo
mantiene comandos y tests; el responsable de datos autoriza el punto de
restauración; Seguridad verifica permisos, secretos y ausencia de PII.

El rollback de un ejercicio consiste en descartar las bases temporales y los
artefactos del ejercicio. En producción se debe detener la aplicación, preservar
la base afectada, restaurar en un destino nuevo verificado y cambiar tráfico sólo
después de los checks; este comando no restaura producción ni ejecuta migraciones
contra ella. No cubre proveedor S3, credenciales, DNS, correo, secretos ni
consistencia de integraciones externas.
