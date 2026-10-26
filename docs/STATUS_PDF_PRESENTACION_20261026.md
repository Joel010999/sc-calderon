# Corrección de presentación de pasajes — 2026-10-26

## Implementación y delegación

- Rama: `feature/sandro-menores-horarios-pasaje-integrados`.
- Base revisada: `09674c7b99e79c9593a9b87c0fe04bd8cdc75d0a`.
- Antigravity fue iniciado como worker nativo de Orca en un worktree aislado. Solo inspeccionó la documentación, las referencias y las muestras existentes; detectó texto ilegible y no modificó archivos.
- La ejecución quedó bloqueada por la cuota individual de Antigravity (`Individual quota reached...`, error `2eadbb5e-2633-4f9d-8700-e80a42e26269-70`). Codex capturó la evidencia, detuvo y liberó únicamente ese worker, verificó `writable=false` y conservó su worktree limpio.
- Codex completó la corrección como sustituto explícitamente autorizado. La implementación del worker no se marca como exitosa.

## Cambios

- Se usa el logo real `static/assets/scviajes-logo.png` con transparencia, en lugar del círculo de reemplazo.
- Se registran fuentes TrueType embebidas de ReportLab y se usan `Paragraph`/alturas dinámicas para saltos de línea en nombres, referencias, datos fiscales y campos largos.
- Se conservan los dos talones, QR propio, horarios por parada y descargas privadas.
- Se muestra el acompañante del menor en ambos talones.
- El menor gratuito muestra `Pasaje gratuito — sin butaca`; no se cambian estados de pago ni se inventa seguro.
- Los importes ARS se muestran con formato argentino (`$ 55.000,00`).
- Se elimina la nota técnica de plantilla provisional.
- Se incorporan en una página adicional las condiciones y devoluciones derivadas del Word editado, separadas de las notas de revisión y con advertencia de validación comercial/legal pendiente. No se presentan referencias normativas como verificadas.

## Validación

- `tickets`: 60 pruebas, OK, 2 omitidas; base de pruebas efímera.
- Prueba afectada de pasaje: 4/4, OK.
- `python manage.py check`: OK.
- `python manage.py makemigrations --check --dry-run`: OK, sin cambios.
- `git diff --check`: OK.
- Ruff: no disponible en el entorno local (`RUFF_NOT_INSTALLED`).
- Se generaron muestras nuevas fuera de Git en `D:\renderbyte\scviajes-pdf-samples\menores-horarios-20261026`, sin sobrescribir las anteriores.
- Cada muestra generada tiene dos páginas y fue inspeccionada estructuralmente con `pypdf`; la prueba existente compara la matriz QR extraída con la matriz esperada.
- El renderizador PDF local disponible no produjo una imagen útil: Poppler, Ghostscript, MuPDF, PyMuPDF y OpenCV no están instalados; la captura headless de Chrome resultó negra. Por eso la revisión visual completa de todas las páginas y la decodificación QR con un lector independiente quedan pendientes y no se declaran aprobadas.

## Pendientes preservados

Quedan pendientes la validación visual externa de las muestras, la confirmación comercial/legal de las condiciones, la configuración productiva de transportista/seguro/datos fiscales, el importe y condiciones del seguro, y la verificación documental oficial de edad. No se envían correos reales ni se ejecutan migraciones persistentes.

## Corrección posterior

- Se reemplaza el separador Unicode de recorrido por ` - ` al renderizar, evitando glifos cuadrados.
- La inconsistencia de `datos-largos-sintetico-nuevo.pdf` provenía del fixture: al sobrescribir origen y destino no se recalculaban `route_name`, `origin_address` ni `destination_address`. El generador ahora deriva esos campos cuando no se proporcionan explícitamente.
- Se agregó una regresión para ambos sentidos que contrasta recorrido, paradas, direcciones y horarios.
- Nuevas muestras: `D:\renderbyte\scviajes-pdf-samples\menores-horarios-20261026-v2`.
