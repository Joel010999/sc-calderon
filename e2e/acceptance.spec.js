const fs = require('fs');
const { test, expect } = require('@playwright/test');

const adminUser = process.env.DEMO_ADMIN_USERNAME || 'demo-20261005-admin';
const sellerUser = process.env.DEMO_SELLER_USERNAME || 'demo-20261005-vendedor';
const adminPassword = process.env.DEMO_ADMIN_PASSWORD || 'e2e-only-admin-password-20261007';
const sellerPassword = process.env.DEMO_SELLER_PASSWORD || 'e2e-only-seller-password-20261007';
const cashSellerUser = process.env.DEMO_CASH_SELLER_USERNAME || 'demo-20261005-cash-vendedor';
const cashSellerPassword = process.env.DEMO_CASH_SELLER_PASSWORD || 'e2e-only-cash-seller-password-20261010';
const customerEmail = process.env.DEMO_CUSTOMER_EMAIL || 'cliente@demo.scviajes.invalid';
const customerPassword = process.env.DEMO_CUSTOMER_PASSWORD || 'e2e-only-customer-password-20261007';
const customerHome = '/cliente/mis-viajes/';

async function panelLogin(page, username = sellerUser) {
  const password = username === adminUser
    ? adminPassword
    : username === cashSellerUser ? cashSellerPassword : sellerPassword;
  await page.goto('/panel/login/');
  await page.locator('#id_username').fill(username);
  await page.locator('#id_password').fill(password);
  await Promise.all([
    page.waitForURL(/\/panel\/$/),
    page.getByRole('button', { name: 'Ingresar' }).click(),
  ]);
}

async function customerLogin(page, next) {
  await page.goto(`/login/?next=${encodeURIComponent(next)}`);
  await expect(page.locator('form[action="/login/"]')).toBeVisible();
  await page.locator('#email').fill(customerEmail);
  await page.locator('#password').fill(customerPassword);
  await page.locator('form[action="/login/"] button[type="submit"]').click();
}

async function createGuestBooking(page, email) {
  await page.goto('/');
  const originValue = await page.locator('#origin option').filter({ hasText: 'Cordoba Demo' }).first().getAttribute('value');
  const destinationValue = await page.locator('#destination option').filter({ hasText: 'Jujuy Demo' }).first().getAttribute('value');
  expect(originValue).toBeTruthy();
  expect(destinationValue).toBeTruthy();
  const travelDate = new Date();
  let checkoutForm = page.locator('form[action*="/checkout/"]');
  let foundTrip = false;
  for (let offset = 0; offset <= 14; offset += 1) {
    const candidate = new Date(travelDate);
    candidate.setDate(candidate.getDate() + offset);
    const dateValue = candidate.toISOString().slice(0, 10);
    await page.goto(`/buscar/?trip_type=oneway&origin=${originValue}&destination=${destinationValue}&date=${dateValue}&passengers=1`);
    checkoutForm = page.locator('form[action*="/checkout/"]');
    if (await checkoutForm.count()) {
      foundTrip = true;
      break;
    }
  }
  expect(foundTrip).toBe(true);
  await checkoutForm.first().locator('button').click();
  await page.locator('input[name="outbound_seats"]:not([disabled])').first().check({ force: true });
  await page.locator('#p_1_first_name').fill('E2E');
  await page.locator('#p_1_last_name').fill('Invitado');
  await page.locator('#p_1_document_number').fill(`99${Date.now().toString().slice(-6)}`);
  await page.locator('#p_1_birth_date').fill('1990-01-01');
  await page.locator('#id_email').fill(email);
  await page.locator('#id_phone').fill('3515550000');
  await page.locator('#submit-booking-btn').click();
  await expect(page).toHaveURL(/\/resumen\/[0-9a-f-]+\//);
  return page.url();
}

function readBrowserSecrets() {
  const secretFile = process.env.E2E_SECRET_FILE;
  if (!secretFile) throw new Error('E2E_SECRET_FILE no está configurado');
  return JSON.parse(fs.readFileSync(secretFile, 'utf8'));
}

async function createManualBookingFromPanel(page) {
  await page.goto('/panel/reservas/nueva/');
  const trip = page.locator('#id_outbound_trip option:not([value=""])').first();
  await Promise.all([
    page.waitForNavigation(),
    page.locator('#id_outbound_trip').selectOption(await trip.getAttribute('value')),
  ]);
  const origin = page.locator('#id_outbound_origin option:not([value=""])').first();
  await Promise.all([
    page.waitForNavigation(),
    page.locator('#id_outbound_origin').selectOption(await origin.getAttribute('value')),
  ]);
  const destination = page.locator('#id_outbound_destination option:not([value=""])').last();
  await Promise.all([
    page.waitForNavigation(),
    page.locator('#id_outbound_destination').selectOption(await destination.getAttribute('value')),
  ]);
  await page.locator('input[name="outbound_seats"]:not([disabled])').first().check();
  await page.locator('#id_contact_email').fill(`cash-${Date.now()}@demo.scviajes.invalid`);
  await page.locator('#id_contact_phone').fill('3515550000');
  await page.locator('#id_p_1_first_name').fill('Caja');
  await page.locator('#id_p_1_last_name').fill('Sintética');
  await page.locator('#id_p_1_document_number').fill(`9900${Date.now().toString().slice(-6)}`);
  await page.locator('#id_p_1_birth_date').fill('1990-01-01');
  await Promise.all([
    page.waitForURL(/\/panel\/reservas\/[0-9a-f-]+\//),
    page.getByRole('button', { name: /confirmar venta interna|confirmar y crear reserva manual|crear reserva manual/i }).click(),
  ]);
  return page.url();
}

const onePixelPng = {
  name: 'comprobante-e2e.png',
  mimeType: 'image/png',
  buffer: Buffer.from(
    'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk' +
      'YAAAAAYAAjCB0C8AAAAASUVORK5CYII=',
    'base64',
  ),
};

test.describe('aceptación pública y panel interno', () => {
  test('invitado puede buscar y retener una butaca', async ({ page }) => {
    await createGuestBooking(page, `e2e-${Date.now()}@demo.scviajes.invalid`);
    await expect(page.getByText(/Reserva online/)).toBeVisible();
    await expect(page.getByText(/butacas permanecerán retenidas|butacas permaneceran retenidas/)).toBeVisible();
  });

  test('transferencia pública muestra datos y permite adjuntar comprobante sintético', async ({ page }) => {
    await createGuestBooking(page, `transfer-${Date.now()}@demo.scviajes.invalid`);
    await page.locator('a[href*="/pago/"]').first().click();
    await page.locator('form[action*="/transferencia/iniciar/"] button').click();
    await expect(page).toHaveURL(/\/transferencia\//);
    await expect(page.getByText(/Pago por transferencia bancaria/)).toBeVisible();
    const voucher = page.locator('input[type="file"][name="voucher"]');
    await expect(voucher).toBeVisible();
    await voucher.setInputFiles(onePixelPng);
    await page.locator('#btn-upload-voucher').click();
    await expect(page.getByText(/Comprobante en revisi/)).toBeVisible();
  });

  test('admin aprueba y visualiza el rechazo de una transferencia', async ({ page }) => {
    await panelLogin(page, adminUser);
    await page.goto('/panel/pagos/transferencias-pendientes/');
    const pending = page.locator('section[aria-label^="Transferencia"]');
    await expect(pending.first()).toBeVisible();
    page.once('dialog', (dialog) => dialog.accept());
    await pending.first().getByRole('button', { name: /Aprobar transferencia/ }).click();
    await page.waitForURL(/\/panel\/pagos\/transferencias-pendientes\//);
    const remaining = page.locator('section[aria-label^="Transferencia"]');
    if (await remaining.count()) {
      await remaining.first().locator('input[name="rejection_reason"]').fill('Comprobante ilegible en E2E');
      page.once('dialog', (dialog) => dialog.accept());
      await remaining.first().getByRole('button', { name: /Rechazar/ }).click();
      await page.waitForURL(/\/panel\/pagos\/transferencias-pendientes\//);
    }
    await page.goto('/panel/pagos/?status=REJECTED');
    await page.getByRole('link', { name: 'Ver detalle' }).first().click();
    await expect(page.getByText(/Motivo de rechazo/)).toBeVisible();
    await expect(page.getByText(/Demo sintetico|Comprobante ilegible en E2E/).first()).toBeVisible();
  });

  test('vendedor accede al panel operativo y no al explorador de auditoría', async ({ page }) => {
    await panelLogin(page);
    await expect(page.getByText('Dashboard General')).toBeVisible();
    await expect(page.locator('#main-sidebar')).not.toContainText(/Caja/i);
    await expect(page.locator('#main-sidebar')).not.toContainText(/Embarque/i);
    const cajaResponse = await page.goto('/panel/caja/');
    expect(cajaResponse.status()).toBe(404);
    const embarquesResponse = await page.goto('/panel/embarques/');
    expect(embarquesResponse.status()).toBe(404);
    const response = await page.goto('/panel/auditoria/');
    expect(response.status()).toBe(403);
  });

  test('administrador puede revisar reservas, fulfillment y auditoría', async ({ page }) => {
    await panelLogin(page, adminUser);
    await page.goto('/panel/reservas/');
    await expect(page.getByRole('heading', { name: /Reservas/ })).toBeVisible();
    await page.goto('/panel/fulfillment/');
    await expect(page.getByText(/Trabajos recientes/)).toBeVisible();
    await page.goto('/panel/auditoria/');
    await expect(page.getByRole('heading', { name: /Auditoría central|Auditoria central/ })).toBeVisible();
  });

  test('login y registro de cliente son navegables con next interno', async ({ page }) => {
    await page.goto(`/registro/?next=${encodeURIComponent(customerHome)}`);
    await expect(page.locator('form[action="/registro/"]')).toBeVisible();
    await expect(page.locator('#first_name')).toBeVisible();

    await customerLogin(page, customerHome);
    await expect(page).toHaveURL(/\/cliente\/mis-viajes\/$/);
  });

  test('login rechaza next externo y variante con backslash', async ({ page }) => {
    await customerLogin(page, 'https://evil.example/');
    await expect(page).toHaveURL(/\/cliente\/mis-viajes\/$/);
    expect(page.url()).not.toContain('evil.example');

    await page.context().clearCookies();
    await customerLogin(page, '/\\\\evil.example/');
    await expect(page).toHaveURL(/\/cliente\/mis-viajes\/$/);
    expect(page.url()).not.toContain('evil.example');
  });

  test('contextos separados no comparten la sesiÃ³n ni permisos de rol', async ({ browser }) => {
    const sellerContext = await browser.newContext({ locale: 'es-AR' });
    const adminContext = await browser.newContext({ locale: 'es-AR' });
    try {
      const sellerPage = await sellerContext.newPage();
      const adminPage = await adminContext.newPage();
      await panelLogin(sellerPage);
      await adminPage.goto('/panel/auditoria/');
      await expect(adminPage).toHaveURL(/\/panel\/login\//);
      await panelLogin(adminPage, adminUser);
      await expect(adminPage).toHaveURL(/\/panel\/$/);
      await sellerPage.goto('/panel/auditoria/');
      await expect(sellerPage).toHaveURL(/\/panel\/auditoria\//);
      await expect(sellerPage.locator('body')).toContainText(/403|permiso|prohibido/i);
    } finally {
      await sellerContext.close();
      await adminContext.close();
    }
  });

  test('pantalla de embarque y control de escaneo están retirados', async ({ page }) => {
    await panelLogin(page);
    await expect(page.locator('#main-sidebar')).not.toContainText(/Embarque/i);
    const response = await page.goto('/panel/embarques/');
    expect(response.status()).toBe(404);
    await expect(page.locator('body')).not.toContainText('Validar embarque');
  });

  test('vendedor no visualiza caja y sus rutas operativas están retiradas', async ({ page }) => {
    await panelLogin(page);
    await expect(page.locator('#main-sidebar')).not.toContainText(/Caja/i);
    const cajaResp = await page.goto('/panel/caja/');
    expect(cajaResp.status()).toBe(404);
    const reportResp = await page.goto('/panel/caja/reporte-diario/');
    expect(reportResp.status()).toBe(404);
    await expect(page.locator('body')).not.toContainText('Caja diaria');
  });

  test('caja y cobro en efectivo retirados en reserva manual y reportes', async ({ browser }) => {
    const sellerContext = await browser.newContext({ locale: 'es-AR' });
    const adminContext = await browser.newContext({ locale: 'es-AR' });
    const otherContext = await browser.newContext({ locale: 'es-AR' });
    try {
      const sellerPage = await sellerContext.newPage();
      await panelLogin(sellerPage, cashSellerUser);
      // Rutas operativas de caja no están disponibles (404)
      const cajaResp = await sellerPage.goto('/panel/caja/');
      expect(cajaResp.status()).toBe(404);

      // Crear reserva manual desde panel operativo conservando permisos de vendedor
      await createManualBookingFromPanel(sellerPage);
      await expect(sellerPage.locator('body')).not.toContainText(/cobro en efectivo/i);
      await expect(sellerPage.getByRole('button', { name: /cobro en efectivo/i })).toHaveCount(0);

      // Flujo vigente: pago por transferencia bancaria con comprobante
      const voucherInput = sellerPage.locator('input[type="file"][name="voucher"]');
      await expect(voucherInput).toBeVisible();
      await voucherInput.setInputFiles(onePixelPng);
      await Promise.all([
        sellerPage.waitForURL(/\/panel\/reservas\/[0-9a-f-]+\//),
        sellerPage.getByRole('button', { name: /Registrar transferencia/i }).click(),
      ]);
      await expect(sellerPage.getByText(/Transferencia en revisión|registrada una transferencia/i)).toBeVisible();

      // Reportes de caja retirados (404) para administradores
      const adminPage = await adminContext.newPage();
      await panelLogin(adminPage, adminUser);
      const reportClosedResp = await adminPage.goto('/panel/reportes/caja/?estado=CLOSED');
      expect(reportClosedResp.status()).toBe(404);
      const reportRootResp = await adminPage.goto('/panel/reportes/caja/');
      expect(reportRootResp.status()).toBe(404);

      // Aislamiento: vendedor no accede a auditoría central ni reportes restringidos
      const otherPage = await otherContext.newPage();
      await panelLogin(otherPage);
      const forbidden = await otherPage.goto('/panel/auditoria/');
      expect(forbidden.status()).toBe(403);
    } finally {
      await sellerContext.close();
      await adminContext.close();
      await otherContext.close();
    }
  });

  test('transferencia completa confirma fulfillment y descarga protegida', async ({ browser }) => {
    const guestContext = await browser.newContext({ locale: 'es-AR' });
    const operatorContext = await browser.newContext({ locale: 'es-AR' });
    try {
      const guestPage = await guestContext.newPage();
      const operatorPage = await operatorContext.newPage();
      const summaryUrl = await createGuestBooking(guestPage, `complete-${Date.now()}@demo.scviajes.invalid`);
      const bookingPublicId = summaryUrl.match(/[0-9a-f-]{36}/i)[0];
      await guestPage.locator('a[href*="/pago/"]').first().click();
      await guestPage.locator('form[action*="/transferencia/iniciar/"] button').click();
      await guestPage.locator('input[type="file"][name="voucher"]').setInputFiles(onePixelPng);
      await guestPage.locator('#btn-upload-voucher').click();
      await expect(guestPage.getByText(/Comprobante en revisi/)).toBeVisible();

      await panelLogin(operatorPage, adminUser);
      await operatorPage.goto('/panel/pagos/transferencias-pendientes/');
      const transfer = operatorPage.locator('section[aria-label^="Transferencia"]').filter({
        has: operatorPage.locator(`a[href*="${bookingPublicId}"]`),
      });
      await expect(transfer).toHaveCount(1);
      operatorPage.once('dialog', (dialog) => dialog.accept());
      await transfer.getByRole('button', { name: /Aprobar transferencia/ }).click();
      await operatorPage.waitForURL(/\/panel\/pagos\/transferencias-pendientes\//);
      await operatorPage.goto(`/panel/reservas/${bookingPublicId}/`);
      await expect(operatorPage.getByText(/Confirmada/).first()).toBeVisible();
      await expect(operatorPage.getByRole('heading', { name: 'Entrega de pasajes' })).toBeVisible();

      await guestPage.goto(summaryUrl);
      await expect(guestPage.getByText(/Pasajes de tu reserva/)).toBeVisible();
      const downloadPromise = guestPage.waitForEvent('download');
      await guestPage.getByRole('link', { name: 'Descargar PDF' }).first().click();
      await expect((await downloadPromise).suggestedFilename()).toMatch(/\.pdf$/);
    } finally {
      await guestContext.close();
      await operatorContext.close();
    }
  });

  test('cliente consulta pasaje sintético y no puede usar el panel', async ({ browser }) => {
    const customerContext = await browser.newContext({ locale: 'es-AR' });
    try {
      const page = await customerContext.newPage();
      await customerLogin(page, customerHome);
      await expect(page.getByText(/Mis viajes y pasajes/)).toBeVisible();
      await expect(page.getByRole('link', { name: 'Descargar PDF' }).first()).toBeVisible();
      const response = await page.goto('/panel/');
      expect(response.status()).toBe(403);
    } finally {
      await customerContext.close();
    }
  });

  test('embarque retirado de rutas y manifiesto sin contadores de embarque', async ({ browser }) => {
    const customerContext = await browser.newContext({ locale: 'es-AR' });
    const operatorContext = await browser.newContext({ locale: 'es-AR' });
    const sellerContext = await browser.newContext({ locale: 'es-AR' });
    try {
      const customerPage = await customerContext.newPage();
      await customerLogin(customerPage, customerHome);
      await expect(customerPage.getByText(/Mis viajes y pasajes/)).toBeVisible();
      await expect(customerPage.getByRole('link', { name: 'Descargar PDF' }).first()).toBeVisible();

      const secrets = readBrowserSecrets();
      const tripId = String(secrets.boarding_trip_id);

      const operatorPage = await operatorContext.newPage();
      await panelLogin(operatorPage, adminUser);

      // Manifiesto accesible para admin sin contadores de embarque obsoletos
      await operatorPage.goto(`/panel/operaciones/viajes/${tripId}/manifiesto/`);
      await expect(operatorPage.getByRole('heading', { name: /Manifiesto de pasajeros/i, level: 2 })).toBeVisible();
      await expect(operatorPage.getByRole('heading', { name: /Resumen por parada/i, level: 3 })).toBeVisible();
      await expect(operatorPage.getByRole('heading', { name: /Pasajeros confirmados/i, level: 3 })).toBeVisible();
      await expect(operatorPage.locator('body')).not.toContainText(/Embarcados:/i);
      await expect(operatorPage.locator('body')).not.toContainText(/Pendientes de embarque/i);

      // Ruta de embarques retirada (404)
      const embarqueResp = await operatorPage.goto('/panel/embarques/');
      expect(embarqueResp.status()).toBe(404);

      // Detalle del viaje: sin acciones de embarque, pero permite descarga de listado y gestión del viaje
      await operatorPage.goto(`/panel/operaciones/viajes/${tripId}/`);
      await expect(operatorPage.locator('body')).not.toContainText('Validar embarque');
      await expect(operatorPage.getByRole('link', { name: 'Descargar listado de pasajeros' })).toBeVisible();

      // Ciclo operativo de viaje ejecutable por administrador
      await operatorPage.getByRole('link', { name: 'Iniciar viaje' }).click();
      await operatorPage.getByRole('button', { name: 'Confirmar e iniciar viaje' }).click();
      await operatorPage.waitForURL(new RegExp(`/panel/operaciones/viajes/${tripId}/$`));
      await operatorPage.getByRole('button', { name: 'Finalizar viaje' }).click();
      await operatorPage.waitForURL(new RegExp(`/panel/operaciones/viajes/${tripId}/$`));

      // Permisos: vendedor no puede acceder al manifiesto ni al listado de pasajeros (403)
      const sellerPage = await sellerContext.newPage();
      await panelLogin(sellerPage);
      const manifestResp = await sellerPage.goto(`/panel/operaciones/viajes/${tripId}/manifiesto/`);
      expect(manifestResp.status()).toBe(403);
      const pdfResp = await sellerPage.goto(`/panel/operaciones/viajes/${tripId}/listado-pasajeros.pdf`);
      expect(pdfResp.status()).toBe(403);
    } finally {
      await customerContext.close();
      await operatorContext.close();
      await sellerContext.close();
    }
  });

  test.describe('QR público sintético', () => {
    test('QR válido es de solo consulta y el token manipulado es rechazado', async ({ page }) => {
      const secrets = readBrowserSecrets();
      await page.goto(secrets.qr_verification_url);
      await expect(page.getByText(/Pasaje V/)).toBeVisible();
      await expect(page.locator('body')).not.toContainText(/@demo\.scviajes\.invalid|99000101|3515550000/);
      await expect(page.locator('body')).not.toContainText(secrets.qr_verification_url.split('token=')[1]);
      await page.goto(`${secrets.qr_verification_url}x`);
      await expect(page.getByText(/Pasaje No Encontrado/)).toBeVisible();

      await panelLogin(page, adminUser);
      await page.goto(`/panel/operaciones/viajes/${secrets.qr_trip_id}/manifiesto/`);
      await expect(page.getByRole('heading', { name: /Manifiesto de pasajeros/i, level: 2 })).toBeVisible();
      await expect(page.getByRole('heading', { name: /Pasajeros confirmados/i, level: 3 })).toBeVisible();
      await expect(page.locator('body')).not.toContainText(/Embarcados:/i);
      await expect(page.locator('body')).not.toContainText(/Pendientes de embarque/i);
    });
  });
});
