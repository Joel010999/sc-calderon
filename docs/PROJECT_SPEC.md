# Especificación del proyecto SC Viajes

Este documento registra las reglas confirmadas y distingue las referencias iniciales de las definiciones pendientes. No implica que las funcionalidades ya estén implementadas. Ver [estado](STATUS.md) y [decisiones](DECISIONS.md).

## Producto

SC Viajes venderá pasajes online y permitirá gestionar operaciones y ventas desde un panel interno personalizado.

## Recorridos

### Córdoba → Jujuy

1. Córdoba Capital: suben pasajeros.
2. Jesús María: solamente suben; nadie baja.
3. Perico: solamente bajan; nadie sube.
4. Palpalá: solamente bajan; nadie sube.
5. San Salvador de Jujuy: terminal final; bajan todos los pasajeros restantes.

### Jujuy → Córdoba

1. San Salvador de Jujuy: suben pasajeros.
2. Palpalá: solamente suben; nadie baja.
3. Perico: solamente suben; nadie baja.
4. Jesús María: solamente bajan; nadie sube.
5. Córdoba Capital: terminal final; bajan todos los pasajeros restantes.

La solución debe validar origen y destino según el orden y los permisos de subida y bajada de cada recorrido. Para el MVP no se revende una misma butaca por tramos dentro del mismo viaje.

## Compra

- Se permiten pasajes únicamente de ida o de ida y vuelta.
- Una compra puede incluir varios pasajeros.
- Los datos definitivos por pasajero se terminarán de validar con Sandro. La referencia actual es: nombre, apellido, DNI, fecha de nacimiento, nacionalidad, género, correo y teléfono.
- Las reglas y tarifas para menores quedan pendientes.
- Se puede comprar como invitado.
- El cliente puede registrarse con correo y contraseña o iniciar sesión con Google.
- No se utilizarán códigos enviados por correo para iniciar sesión.
- Apple/iCloud queda fuera de la primera versión.
- El correo de compra es obligatorio.
- El consentimiento para comunicaciones comerciales debe ser explícito, separado y auditable.

## Butacas y colectivos

- Existen categorías cama y semicama con precios diferentes.
- La referencia inicial del colectivo es de 60 butacas.
- Referencia visual: butacas 1 a 48 en planta alta, semicama; butacas 49 a 60 en planta baja, cama.
- La distribución debe modelarse de manera flexible y no quedar rígidamente dibujada en el código.
- Esta distribución debe validarse con Sandro antes de utilizarla como configuración productiva.
- En la operación informada no se cambia el colectivo una vez programado el viaje.

## Disponibilidad y vencimientos

- Una selección online mantiene la butaca durante 15 minutos.
- Una reserva manual del panel vence inicialmente a las 24 horas.
- Ambos plazos deben ser configurables.
- Al subir un comprobante de transferencia, la operación pasa a revisión manual.
- La conducta exacta del vencimiento durante la revisión queda pendiente de definición.
- La venta online cierra una hora antes del horario de subida del pasajero.
- Administradores y vendedores pueden registrar ventas manuales hasta que se inicie el viaje.
- Las ventas manuales deben afectar disponibilidad, caja, ventas y métricas exactamente igual que las ventas online.

## Pagos

Métodos previstos:

- Efectivo, registrado manualmente.
- Transferencia a cuenta de Mercado Pago, con comprobante y aprobación manual.
- QR de Mercado Pago.
- Tarjetas de crédito y débito mediante Payway.

Las integraciones reales de Mercado Pago y Payway se harán después de la migración desde Google Sheets.

## Pasajes

- El pasaje final será un PDF.
- Se enviará por correo electrónico y también quedará descargable desde la web.
- No se enviará automáticamente por WhatsApp.
- El diseño exacto utilizará como referencia un pasaje real y una plantilla en blanco que entregará el cliente.
- Cada pasaje tendrá un código QR único para control de embarque.
- La validación del QR y el estado de embarque se implementarán posteriormente.

## Cambios y cancelaciones

- El pasajero no puede cancelar el pasaje.
- Puede solicitar un cambio hasta cinco horas antes de su horario de subida.
- El cambio tiene penalización; el importe o fórmula está pendiente.
- La política de saldo a favor o devolución en situaciones atribuibles a la empresa está pendiente.
- El cambio de nombre del pasajero debe consultarse con Sandro.

## Migración

- SC Viajes utiliza actualmente AppSheet conectado con Google Sheets.
- La migración será una etapa controlada y auditable.
- Se realizará en la etapa penúltima, antes de activar las pasarelas de pago.
- Primero se importará a un entorno de prueba.
- Deben existir validaciones, reporte de errores, conteos y posibilidad de repetir la importación sin duplicar datos.
- No implementar todavía la migración: aún no se conocen las hojas y columnas reales.
