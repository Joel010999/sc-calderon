# Gráficos de Ventas y Corrección de Comisiones en SC Viajes

**Fecha:** 6 de noviembre de 2026
**Rama:** `Joel010999/panel-graficos-comisiones-20261106`
**Commit base:** `b9aa9ac49b47fe212063ca8e612cf94bde92f51e`
**Objetivo:** Implementar los 6 gráficos de ventas con porcentajes y corregir el cálculo compartido de comisiones de vendedores para ventas internas.

---

## 1. Alcance y Reglas de Negocio Implementadas

### A. Cálculo Unificado y Compartido de Comisiones (`panel/report_services.py`)
Se implementó la función unificada y compartida `get_commissionable_assignments(booking_ids_or_qs, *, seller_id=None)` que alimenta tanto al reporte de ventas (`panel:sales_report`) como al dashboard principal (`panel:dashboard`), garantizando paridad matemática absoluta:
1. **Tarifa y asignación:** ARS 5.000 (`Decimal("5000.00")`) por pasajero con tarifa positiva (`price > 0`) y por tramo vendido.
2. **Canal exclusivo:** Únicamente venta interna atribuida a usuario vendedor (`channel == BookingChannel.MANUAL` y `seller__isnull=False`).
3. **Canal ONLINE:** Siempre comisión cero ($0,00), aun en caso de tener vendedor asociado.
4. **Menores gratuitos en brazos:** Los menores de 5 años (`is_lap_infant=True`) viajan sin butaca asignada y con tarifa $0, por lo que generan comisión cero ($0,00).
5. **Estados válidos y exclusiones:** Solo reservas confirmadas con pago aprobado (`BookingStatus.CONFIRMED` y `PaymentStatus.APPROVED`). Se excluyen reservas en estados `HELD`, `REJECTED`, `EXPIRED` o `CANCELLED`.
6. **Idempotencia y deduplicación:** Se aplica `.distinct()` sobre las asignaciones de butaca para evitar duplicaciones por joins con pagos, reintentos o entregas múltiples.
7. **Neto operativo:** Definido estrictamente como ingreso bruto confirmado menos comisiones retenidas, sin confundir con liquidación contable o pago a vendedores.
8. **Aislamiento por rol:** Los vendedores solo acceden a sus propias unidades y comisiones; administradores acceden al consolidado global.

### B. Seis Gráficos Analíticos con Porcentajes (`templates/panel/reports/sales.html`, `panel/report_services.py`)
Se reemplazó la presentación visual anterior por 6 gráficos analíticos con barras apiladas, barras de avance, insignias de porcentajes e indicación explícita de unidad y denominador:
1. **Canal Web vs Venta Interna:** Unidad: Compras confirmadas; Denominador: Total de compras confirmadas. Detalla además el conteo de personas (pasajeros con butaca vs menores en brazos).
2. **Participación por Vendedor (Exclusivo Administración):** Unidad: Compras confirmadas; Denominador: Total de compras internas con vendedor. Totalmente omitido y protegido contra acceso no autorizado por vendedores.
3. **Categoría Cama vs Semicama:** Unidad: Pasajeros/tramos con asiento; Denominador: Total de asignaciones de butaca. Aclara explícitamente que los menores en brazos no consumen asiento.
4. **Sentido del Recorrido (Córdoba → Jujuy vs Jujuy → Córdoba):** Unidad: Tramos vendidos; Denominador: Total de tramos de las reservas confirmadas.
5. **Tipo Solo Ida vs Ida/Vuelta:** Unidad: Compras confirmadas; Denominador: Total de compras confirmadas.
6. **Salida Concreta:** Identificada inequívocamente por `Trip.pk`, fecha/hora de salida y código de colectivo (`Bus.code`). Los servicios de refuerzo con idéntico horario y recorrido no se agrupan, mostrando cada salida independiente con su ocupación real respecto a la capacidad física del colectivo (sin inventar datos) y pasajeros en brazos.

### C. Retiro de Tablas e Importes Monetarios en la Pantalla de Reportes
- Se suprimieron de la pantalla de Reportes de Ventas las tablas monetarias y tarjetas con signo de moneda (`$`), conforme al requerimiento.
- Se mantuvieron los filtros operativos de fechas (`desde`, `hasta`, `viaje_desde`, `viaje_hasta`), medio de pago, canal y vendedor.
- Se conservó la tabla de auditoría operativa de transacciones confirmadas (con datos no monetarios: referencia, fecha, canal, medio, pasajeros, tramos, vendedor).
- El Dashboard principal y la vista de Pagos conservan íntegramente sus importes monetarios ($) y métricas de neto operativo.

---

## 2. Matriz de Validación Técnica

| Verificación | Comando / Herramienta | Resultado | Detalle |
|---|---|---|---|
| **Linter de código** | `python -m ruff check .` | **PASS (0 errores)** | Código Python limpio sin advertencias ni imports residuales. |
| **Integridad Django** | `python manage.py check` | **PASS (0 problemas)** | Configuración del proyecto y aplicaciones sin observaciones. |
| **Migraciones de esquema** | `python manage.py makemigrations --check --dry-run` | **PASS (Sin cambios)** | No se modificaron modelos ni esquemas de base de datos. |
| **Diff y espacios en blanco** | `git diff --check` | **PASS (0 alertas)** | Formato limpio, sin espacios finales ni saltos erróneos. |
| **Pruebas de navegador (CDP)** | `SalesChartsBrowserValidationTests` (Chrome headless) | **PASS (8/8 tests OK)** | Desktop (1440px) y móvil (390px): 0 errores JS, 0px desborde horizontal (`scrollWidth <= clientWidth`), porcentajes reales renderizados. |
| **Suite completa Django** | `python manage.py test -v 2` | **PASS (700 tests OK, 6 skipped)** | Ejecutados 700 tests en 548.835s sin fallos ni errores. Las 6 omisiones corresponden a pruebas de concurrencia PostgreSQL. |
| **Logs íntegros fuera del repositorio** | `C:\Users\JOEL\AppData\Local\Temp\scviajes_charts_20261106\full_test_suite_output.log` | **Preservado íntegro** | Stdout/stderr completo guardado fuera del árbol de trabajo. |

---

## 3. Evidencia Visual (Capturas de Pantalla)

Las capturas fueron generadas mediante Chrome headless interactuando contra un servidor en vivo con datos sintéticos persistidos y almacenadas fuera del repositorio en `C:\Users\JOEL\AppData\Local\Temp\scviajes_charts_20261106\`:
1. `sales_report_admin_desktop.png` (92.9 KB): Vista de Administrador en escritorio con los 6 gráficos analíticos y métricas operativas sin importes monetarios.
2. `sales_report_admin_mobile.png` (111.7 KB): Vista responsiva de Administrador en resolución móvil (390x844) sin desborde horizontal.
3. `sales_report_seller_desktop.png` (83.2 KB): Vista de Vendedor con 5 gráficos (exclusión estricta del gráfico de vendedores) y aislamiento de datos.
4. `sales_report_seller_mobile.png` (111.5 KB): Vista responsiva de Vendedor en resolución móvil (390x844).
5. `dashboard_admin_desktop.png` (111.0 KB): Dashboard confirmando la preservación de tarjetas monetarias ($), ingresos confirmados y neto operativo sincronizado con el servicio de cálculo.

---

## 4. Archivos Modificados

- `panel/report_services.py`: Función compartida `get_commissionable_assignments` y servicio de armado de datos `build_sales_charts`.
- `panel/views.py`: Integración de la función compartida de comisiones en el `dashboard`.
- `templates/panel/reports/sales.html`: Reemplazo de presentación por los 6 gráficos con porcentajes, retiro de importes monetarios y marcado semántico accesible.
- `static/panel/css/panel.css`: Clases CSS dedicadas para gráficos (`.chart-card`, `.chart-bar-stacked`, `.chart-badge-pct`, etc.) en diseño rojo accesible.
- `panel/test_sales_charts_and_commissions.py`: Fixtures sintéticos persistidos, pruebas unitarias de comisiones/exclusiones/consistencia y validación E2E en Chrome headless via CDP.

---

## 5. Validaciones Pendientes
- **Validación en Staging con PostgreSQL**: Ejecución de la suite completa sobre una base de datos PostgreSQL 16 para validar las 6 pruebas de concurrencia y bloqueos transaccionales omitidas en SQLite.
- **Despliegue en ambiente de pruebas**: Verificación visual por parte de los coordinadores en el entorno de staging.
