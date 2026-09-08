# Decisiones de SC Viajes

## Confirmado

La especificación es la referencia completa. Este resumen no convierte referencias iniciales ni puntos pendientes en requisitos definitivos.

| Tema | Decisión y referencia |
| --- | --- |
| Producto | Venta online de pasajes y gestión interna mediante panel personalizado. [Producto](PROJECT_SPEC.md#producto). |
| Recorridos | Dos sentidos con orden y permisos de subida y bajada definidos; validación de origen y destino; sin reventa de una butaca por tramos en el mismo viaje para el MVP. [Recorridos](PROJECT_SPEC.md#recorridos). |
| Compra | Ida o ida y vuelta, varios pasajeros, compra como invitado, correo y contraseña o Google; sin códigos de acceso por correo ni Apple/iCloud en la primera versión. Correo de compra obligatorio y consentimiento comercial explícito, separado y auditable. Los datos de pasajero son una referencia por validar y las reglas de menores siguen abiertas. [Compra](PROJECT_SPEC.md#compra). |
| Colectivos | Cama y semicama con precios diferentes, distribución flexible y sin cambio de colectivo una vez programado el viaje en la operación informada. Las 60 butacas y su distribución por planta son referencias que requieren validación productiva. [Butacas y colectivos](PROJECT_SPEC.md#butacas-y-colectivos). |
| Disponibilidad | Retención online de 15 minutos y reserva manual de 24 horas, configurables; comprobante enviado a revisión manual. Cierre online una hora antes de la subida y ventas manuales de administradores y vendedores hasta el inicio del viaje, con igual efecto en disponibilidad, caja, ventas y métricas. El vencimiento durante revisión no está definido. [Disponibilidad y vencimientos](PROJECT_SPEC.md#disponibilidad-y-vencimientos). |
| Pagos | Efectivo manual, transferencia a Mercado Pago con comprobante y aprobación manual, QR de Mercado Pago y tarjetas por Payway. Integraciones reales posteriores a la migración. [Pagos](PROJECT_SPEC.md#pagos). |
| Pasajes | PDF por correo y descargable en la web, sin envío automático por WhatsApp; diseño basado en material del cliente y QR único. Validación de QR y embarque posteriores. [Pasajes](PROJECT_SPEC.md#pasajes). |
| Cambios | Sin cancelación por el pasajero; solicitud de cambio hasta cinco horas antes de la subida, con penalización aún sin importe o fórmula. Políticas de empresa y cambio de nombre pendientes. [Cambios y cancelaciones](PROJECT_SPEC.md#cambios-y-cancelaciones). |
| Migración | Origen AppSheet/Google Sheets; etapa penúltima y anterior a activar pasarelas; prueba inicial, auditoría, validaciones, errores, conteos e importación repetible sin duplicados. Implementación diferida hasta conocer los datos reales. [Migración](PROJECT_SPEC.md#migración). |

La arquitectura acordada y los límites de los módulos están en [ARCHITECTURE.md](ARCHITECTURE.md): monolito modular Django, PostgreSQL productivo, panel propio, recursos frontend locales, Railway y WhiteNoise. La primera implementación funcional será `operations`. La tecnología de procesamiento en segundo plano queda diferida y requiere una decisión aprobada.

### Retiro del dominio del prototipo

- Los modelos `core.Ruta`, `core.Parada`, `core.Bus`, `core.Salida`, `core.Reserva` y `core.ReservaItem` pertenecían exclusivamente al prototipo.
- El propietario confirmó explícitamente que no existe información real que deba conservarse en esas tablas. No se requiere una migración de datos.
- Se retiran mediante una nueva migración de esquema antes de construir funcionalidad nueva, conservando la aplicación `core` y su infraestructura.
- `operations` queda como única fuente de verdad para paradas, recorridos, colectivos, butacas, viajes y tarifas.
- La futura migración desde AppSheet/Google Sheets es una etapa diferente y permanece pendiente según [PROJECT_SPEC.md](PROJECT_SPEC.md#migración).

## Pendiente de consultar con Sandro

- Datos obligatorios definitivos de cada pasajero.
- Límite máximo de pasajeros por compra.
- Tarifas y reglas para menores, niños y adolescentes.
- Importe o fórmula de penalización por cambios.
- Política ante cancelación del viaje por parte de la empresa.
- Saldo a favor o devolución.
- Cambio de nombre del pasajero.
- Tiempo máximo que una transferencia puede permanecer pendiente de revisión.
- Conducta exacta del vencimiento de la reserva durante la revisión de una transferencia.
- Distribución definitiva del colectivo y butacas inhabilitadas.
- Horarios habituales y duración entre paradas.
- Tarifas reales por origen, destino y categoría.
- Hojas, columnas, relaciones y calidad de datos de AppSheet/Google Sheets.
- Plantilla definitiva en blanco del pasaje PDF y pasaje real de referencia a entregar por el cliente.
- Proveedor definitivo para envío de correo.

No resolver estas decisiones por suposición. Registrar la respuesta aprobada antes de implementar la regla correspondiente.
