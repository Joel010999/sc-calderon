# Dirección visual — Web pública SC Viajes

Rediseño integral del frontend público (rama `feature/mercadopago-checkout-pro`).
Referencia de estructura: balut.com.ar (solo jerarquía y organización; sin copiar
marca, textos, fotos ni código).

## Sistema

Unifica la web pública con la identidad aprobada del panel interno
(`static/panel/css/panel.css` como referencia, sin importar sus resets):

- **Color institucional:** rojo `#dc2626` con escala Tailwind-red
  (50–950) ya definida en el panel. El dorado `#E8B930` y el navy del
  diseño anterior quedan eliminados; los neutrales son slate.
- **Superficies:** fondos blancos `#ffffff` y grises claros
  `#f8fafc`/`#f1f5f9`; bordes `#e2e8f0`/`#cbd5e1`.
- **Tipografía:** `system-ui` stack (la misma del panel), pesos 400/600/700/800.
- **Componentes:** cards con radio 12px y sombra suave; botones sólidos
  rojos con hover `#b91c1c`; foco visible `0 0 0 3px rgba(220,38,38,.35)`;
  estados semánticos success/warning/danger/info del panel.
- **Marca:** logo real `static/assets/scviajes-logo.png` (proporciones y
  transparencia preservadas; versión 256px `scviajes-logo-sm.png` para
  encabezado y pie). El símbolo CSS genérico (`brand-mark__flag`) se elimina.
- **Movimiento:** sin marquesinas ni animaciones permanentes; reveal-on-scroll
  discreto desactivable con `prefers-reduced-motion`.

## Estructura de portada

1. Encabezado limpio: logo + nombre, navegación breve, Mis viajes/Ingresar, CTA Reservar.
2. Hero con fotografía real de Jujuy + título "Conectamos Córdoba y Jujuy"
   + buscador protagonista visible sin scroll en escritorio.
3. Próximas salidas: alimentadas por viajes `SCHEDULED`/`BOARDING` reales
   (origen→destino, fecha/hora, menor tarifa, enlace al buscador).
4. Recorrido: paradas operativas reales de los recorridos activos
   (subida/bajada según `RouteStop`).
5. Categorías: Semicama y Cama (solo nombres reales de `SeatCategory`,
   sin inventar prestaciones).
6. Cómo comprar: buscar → elegir butaca → pagar → recibir el pasaje.
7. FAQ: solo políticas confirmadas (retención con tiempo límite, transferencia
   con comprobante, pasaje PDF tras confirmación, menores de 5 años sin cargo
   acompañados — política provisional `provisional-created-at-until-sandro`,
   declarada como sujeta a vigencia).
8. Pie: logo, enlaces útiles, contactos configurados
   (`SITE_SUPPORT_EMAIL`, `SITE_WHATSAPP_URL`) y créditos fotográficos.

Los paisajes turísticos se presentan como imágenes de Jujuy (destino),
nunca como paradas vendidas: Purmamarca/Humahuaca no son destinos del
servicio y no aparecen como tales.

## Fotografías (fuentes y licencias)

Guardadas como recursos locales optimizados en `static/assets/`:

| Archivo | Imagen | Autor | Licencia | Fuente |
|---|---|---|---|---|
| `hero-jujuy.jpg` | Cerro de los Siete Colores y viñedos, Purmamarca (Jujuy) | P. Hughes (Indies1) | CC BY 4.0 | https://commons.wikimedia.org/wiki/File:Purmamarca_-_Cerro_de_los_Siete_Colores_(Hill_of_Seven_Colors).jpg |
| `jujuy-quebrada.jpg` | Cerro de los Siete Colores, Quebrada de Purmamarca | Bernard Gagnon (Bgag) | CC BY-SA 4.0 | https://commons.wikimedia.org/wiki/File:Cerro_de_los_Siete_Colores_04.jpg |
| `jujuy-monumento.jpg` | Monumento "Jujuy, energía viva", San Salvador de Jujuy | Preyes | CC BY-SA 4.0 | https://commons.wikimedia.org/wiki/File:Jujuy,_energ%C3%ADa_viva.jpg |

Las imágenes `hero-bg.jpg` y `hero-interior.jpg` del diseño anterior se
conservan en el repo pero ya no se referencian.
