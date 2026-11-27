# Instrucciones rápidas — demo SC Viajes (reunión Sandro)

## Arranque (Windows / Git Bash)

```bash
cd /c/renderbyte/scviajes
git checkout feature/mercadopago-checkout-pro
bash scripts/demo_sandro.sh
```

Solo eso. El script migra, siembra y levanta el servidor. La primera
corrida tarda ~1 minuto; las siguientes reutilizan la base.

- **URL:** http://127.0.0.1:8000/ (escucha solo en loopback)
- **Apagado:** `Ctrl+C` en esa terminal. No quedan procesos.
- **Reset de la base:** `SCVIAJES_DEMO_RESET=1 bash scripts/demo_sandro.sh`
- **Borrado total del escenario:** eliminar la carpeta
  `%USERPROFILE%\scviajes-demo-sandro\` manualmente (el script la recrea).
  Las evidencias NO están ahí: están en
  `%USERPROFILE%\scviajes-demo-evidencia\` y no se borran nunca.

## Usuarios demo

| Usuario | Rol | Contraseña |
|---|---|---|
| `demo-20261005-admin` | Administrador | `demo-admin-2026` |
| `demo-20261005-vendedor` | Vendedor | `demo-vendedor-2026` |
| `demo-20261005-cash-vendedor` | Vendedor | `demo-caja-2026` |
| `cliente@demo.scviajes.invalid` | Cliente web | `demo-cliente-2026` |

Panel interno: http://127.0.0.1:8000/panel/login/
Cliente web: http://127.0.0.1:8000/cliente/login/

## Qué está endurecido

- Mercado Pago y Payway **deshabilitados**; los únicos medios visibles
  son transferencia bancaria (datos ficticios de demo) y venta interna.
- Datos bancarios ficticios exclusivos de demo (validados por
  `validate_bank_transfer_configuration`, sin datos del negocio).
- Correo en memoria (no se envía nada real). Storage local, sin S3.
- Sin credenciales ni variables productivas heredables: el script las
  limpia antes de arrancar.
- Base SQLite nueva fuera del repo — demostración de UI. La aceptación
  sobre PostgreSQL 16 la respalda CI (workflows sales/tickets/browser-e2e).

## Material offline

`%USERPROFILE%\scviajes-demo-evidencia\` contiene copia del guion
(`GUION_DEMO_SANDRO.md`), esta guía (`LEEME.txt`), capturas PNG,
PDFs de pasajes ficticios y comprobante de prueba. Funciona sin
internet: el guion está en el propio directorio.

## Guion de 15–20 minutos

Ver `docs/GUION_DEMO_SANDRO.md` (o la copia en la carpeta de evidencia).
