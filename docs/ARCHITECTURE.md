# Arquitectura de SC Viajes

## Base técnica

- Monolito modular en Django.
- PostgreSQL en producción.
- Renderizado con Django Templates, CSS local y HTMX cuando aporte valor.
- Panel interno 100 % personalizado bajo `/panel/`.
- Django Admin permanece deshabilitado.
- Railway como plataforma de despliegue.
- WhiteNoise para archivos estáticos.
- Sin CDN de Tailwind ni dependencias frontend remotas.

Se prevé procesamiento en segundo plano para vencimientos, correos, PDFs y reintentos. La tecnología queda diferida: no instalar Celery ni Redis ahora, ni introducir infraestructura sin una decisión aprobada.

## Módulos conceptuales

| Módulo | Responsabilidad |
| --- | --- |
| `operations` | Paradas, recorridos, colectivos, butacas, viajes y tarifas. |
| `sales` | Reservas, ventas, pasajeros y asignación de butacas. |
| `payments` | Efectivo, transferencias, Mercado Pago y Payway. |
| `tickets` | Generación de PDF, QR y embarque. |
| `customers` | Cuentas, compra como invitado, Google y consentimiento comercial. |
| `notifications` | Correos transaccionales y comunicaciones autorizadas. |
| `migration` | Importación auditable desde Google Sheets/AppSheet. |
| `panel` | Interfaz interna personalizada. |
| `core` | Infraestructura compartida y sitio existente. |

Esta separación es conceptual. No crear todos estos módulos todavía: se implementarán progresivamente. La primera implementación funcional será solamente el módulo `operations`.

## Reglas de diseño

- Las integraciones de pago deben utilizar adaptadores para no mezclar APIs externas con el dominio.
- Los webhooks deberán ser idempotentes.
- El precio pagado deberá guardarse como una instantánea histórica en la venta.
- Usar `Decimal`, nunca `float`, para dinero.
- La disponibilidad de una butaca no puede depender solamente de lo mostrado en pantalla: debe validarse transaccionalmente en el servidor.
- Las operaciones sensibles deben registrar auditoría: usuario, fecha, acción y valores relevantes.
- Las reglas de recorridos y horarios deben vivir en el dominio, no en templates o JavaScript.
- El mapa visual debe construirse a partir de datos de posición de las butacas.
- Las interfaces deben estar en español argentino. Las fechas y horas deben ser conscientes de zona horaria, usando `America/Argentina/Buenos_Aires`.
- Toda regla de negocio debe tener pruebas. Los cambios de modelos deben acompañarse de sus migraciones, sin ejecutarlas contra bases reales.

Las reglas del producto se encuentran en [PROJECT_SPEC.md](PROJECT_SPEC.md). Las definiciones abiertas se registran en [DECISIONS.md](DECISIONS.md); no deben resolverse por suposición.
