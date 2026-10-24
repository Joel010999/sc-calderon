# Estado — permisos, usuarios y balances (24/10/2026)

## Implementado

- Se agregó `panel.StaffProfile` para datos internos de vendedor: nombre y apellido en el usuario, más DNI y WhatsApp; no se reutiliza `Customer` ni se cambia `AUTH_USER_MODEL`.
- Se agregó una capacidad propietaria explícita (`is_owner`) que no se expone en el panel. Solo esa identidad provisionada puede invitar o promover administradores; un superusuario sin esa capacidad no puede autoasignársela mediante la interfaz.
- La desactivación conserva historial y operaciones, revoca sesiones existentes y mantiene la protección del último administrador activo.
- Los reportes aplican el alcance del actor antes de filtros, búsquedas, agregaciones, paginación, vendedores y exportación. Vendedores solo reciben sus ventas y los filtros manipulados no amplían el alcance.
- El dashboard separa reservas confirmadas de compras vendidas y limita ventas, ingresos, comisiones y pendientes al vendedor autenticado.
- El balance usa pagos `APPROVED` de reservas `CONFIRMED`; la reserva rápida, pendientes y rechazados no aportan ingresos.
- La comisión es `Decimal('5000.00')` por asignación confirmada con precio positivo, por pasajero y tramo. Una compra ida/vuelta sigue siendo una compra; no se duplica el pago.
- Se muestran bruto, unidades comisionables, comisión retenida y neto operativo (bruto menos comisión). No se generan transferencias ni se marcan rendiciones.
- Se agregaron filtros separados por fecha de confirmación/venta y por fecha de viaje, interpretados en `America/Argentina/Buenos_Aires`.
- La exportación continúa siendo CSV UTF-8 con BOM compatible con Excel y neutralización de fórmulas; no se presenta como XLSX ni agrega dependencias.

## Pendiente para integración

- La asignación real de la identidad propietaria de Sandro requiere conocer su cuenta y debe hacerse por provisioning explícito, fuera del panel.
- El flujo actual envía un enlace seguro por correo; Sandro compartirá el acceso manualmente por WhatsApp. No se integró WhatsApp ni se enviaron invitaciones reales durante las pruebas.
- Revisar en la rama de reservas/pagos cualquier endpoint adicional que exponga datos de terceros. Esta rama no modifica `sales/**`, endpoints de reservas/pagos, transferencias ni la navegación global.
- Si se requiere XLSX nativo, definir contrato y dependencia en una integración posterior; actualmente el contrato entregado es CSV compatible con Excel.
- Descuentos o diferencias que el modelo histórico no distribuye por asignación se informan como limitación; no se recalculan tarifas ni se prorratean con valores actuales.

## Validación

- Pruebas focalizadas: `python manage.py test panel.test_staff_management panel.test_reports` — 19 OK.
- No se ejecutaron migraciones persistentes. La migración `panel/migrations/0003_staffprofile.py` solo se incluye para revisión e integración.
