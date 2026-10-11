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
  await page.locator('#id_outbound_trip').selectOption(await trip.getAttribute('value'));
  await page.waitForLoadState('networkidle');
  const origin = page.locator('#id_outbound_origin option:not([value=""])').first();
  await page.locator('#id_outbound_origin').selectOption(await origin.getAttribute('value'));
  await page.waitForLoadState('networkidle');
  const destination = page.locator('#id_outbound_destination option:not([value=""])').last();
  await page.locator('#id_outbound_destination').selectOption(await destination.getAttribute('value'));
  await page.waitForLoadState('networkidle');
  await page.locator('input[name="outbound_seats"]:not([disabled])').first().check();
  await page.locator('#id_contact_email').fill(`cash-${Date.now()}@demo.scviajes.invalid`);
  await page.locator('#id_contact_phone').fill('3515550000');
  await page.locator('#id_p_1_first_name').fill('Caja');
  await page.locator('#id_p_1_last_name').fill('Sintética');
  await page.locator('#id_p_1_document_number').fill(`9900${Date.now().toString().slice(-6)}`);
  await page.locator('#id_p_1_birth_date').fill('1990-01-01');
  await page.getByRole('button', { name: /crear reserva manual/i }).click();
  await page.waitForURL(/\/panel\/reservas\/[0-9a-f-]+\//);
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
    await page.goto('/panel/caja/');
    await expect(page.getByText(/Mi caja abierta|No tenés una caja abierta/)).toBeVisible();
    await page.goto('/panel/embarques/');
    await expect(page.getByRole('heading', { name: 'Validar embarque' })).toBeVisible();
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

  test('pantalla de embarque expone selector y control de escaneo', async ({ page }) => {
    await panelLogin(page);
    await page.goto('/panel/embarques/');
    await expect(page.getByRole('heading', { name: 'Validar embarque' })).toBeVisible();
    await expect(page.locator('select[name="trip"]')).toBeVisible();
    await expect(page.locator('input[name="scan_value"]')).toHaveAttribute('autocomplete', 'off');
  });

  test('vendedor consulta su caja sin ver ajustes administrativos', async ({ page }) => {
    await panelLogin(page);
    await page.goto('/panel/caja/');
    await expect(page.getByRole('heading', { name: 'Caja diaria' })).toBeVisible();
    await expect(page.getByText('Ajuste excepcional')).toHaveCount(0);
    await expect(page.getByText(/Mi caja abierta/)).toBeVisible();
  });

  test('caja completa con reserva manual, idempotencia, cierre y conciliación', async ({ browser }) => {
    const sellerContext = await browser.newContext({ locale: 'es-AR' });
    const adminContext = await browser.newContext({ locale: 'es-AR' });
    const otherContext = await browser.newContext({ locale: 'es-AR' });
    try {
      const sellerPage = await sellerContext.newPage();
      await panelLogin(sellerPage, cashSellerUser);
      await sellerPage.goto('/panel/caja/');
      if (await sellerPage.getByRole('button', { name: 'Abrir caja' }).count()) {
        await sellerPage.locator('input[name="opening_amount"]').fill('0');
        await sellerPage.getByRole('button', { name: 'Abrir caja' }).click();
        await sellerPage.waitForURL(/\/panel\/caja\/$/);
      }
      const bookingUrl = await createManualBookingFromPanel(sellerPage);
      await expect(sellerPage.getByRole('button', { name: /Confirmar cobro en efectivo/i })).toBeVisible();
      sellerPage.once('dialog', (dialog) => dialog.accept());
      await sellerPage.getByRole('button', { name: /Confirmar cobro en efectivo/i }).click();
      await sellerPage.waitForURL(/\/panel\/reservas\/[0-9a-f-]+\//);
      await expect(sellerPage.locator('body')).toContainText(/confirmada|confirmado/i);
      await expect(sellerPage.getByRole('button', { name: /Confirmar cobro en efectivo/i })).toHaveCount(0);

      await sellerPage.goto('/panel/caja/');
      await sellerPage.locator('input[name="closing_amount"]').fill('0');
      await sellerPage.getByRole('button', { name: 'Cerrar caja' }).click();
      await sellerPage.waitForURL(/\/panel\/caja\/$/);

      const adminPage = await adminContext.newPage();
      await panelLogin(adminPage, adminUser);
      await adminPage.goto('/panel/reportes/caja/?estado=CLOSED');
      const detailLink = adminPage.getByRole('link', { name: 'Ver detalle' }).first();
      await expect(detailLink).toBeVisible();
      const detailUrl = await detailLink.getAttribute('href');
      await detailLink.click();
      await expect(adminPage.locator('body')).toContainText(/Total esperado:/);
      await expect(adminPage.locator('body')).toContainText(/Importe contado:/);
      await expect(adminPage.locator('body')).toContainText(/Diferencia:/);
      await expect(adminPage.getByRole('cell', { name: 'Cobro en efectivo' })).toHaveCount(1);
      await adminPage.locator('textarea[name="note"]').fill('Conciliación sintética E2E');
      await adminPage.getByRole('button', { name: 'Revisar cierre' }).click();
      await adminPage.waitForURL(/\/panel\/reportes\/caja\/[0-9]+\//);
      await expect(adminPage.locator('p[role="status"]')).toContainText(/revisado/i);
      await expect(adminPage.locator('body')).not.toContainText('cash-');

      const otherPage = await otherContext.newPage();
      await panelLogin(otherPage);
      const forbidden = await otherPage.goto(new URL(detailUrl, 'http://127.0.0.1').pathname);
      expect(forbidden.status()).toBe(403);
      await sellerPage.goto(bookingUrl);
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

  test('embarque válido, repetición idempotente y manifiesto', async ({ browser }) => {
    const customerContext = await browser.newContext({ locale: 'es-AR' });
    const operatorContext = await browser.newContext({ locale: 'es-AR' });
    try {
      const customerPage = await customerContext.newPage();
      await customerLogin(customerPage, customerHome);
      const secrets = readBrowserSecrets();
      const ticketCode = secrets.boarding_ticket_code;
      const tripId = String(secrets.boarding_trip_id);

      const operatorPage = await operatorContext.newPage();
      await panelLogin(operatorPage);
      await operatorPage.goto(`/panel/operaciones/viajes/${tripId}/manifiesto/`);
      await expect(operatorPage.locator('p').filter({ hasText: 'Embarcados:' })).toContainText('Embarcados: 0');
      await expect(operatorPage.locator('p').filter({ hasText: 'Pendientes:' })).toHaveText(/Pendientes: [2-9]/);
      await operatorPage.goto('/panel/embarques/');
      await operatorPage.locator('select[name="trip"]').selectOption(tripId);
      await operatorPage.locator('input[name="scan_value"]').fill(ticketCode.trim());
      await operatorPage.getByRole('button', { name: 'Validar pasaje' }).click();
      await expect(operatorPage.locator('[aria-live="polite"]')).toContainText(/embarque registrado|ya fue embarcado|ya fue utilizado/i);

      if (await operatorPage.getByRole('button', { name: 'Revertir embarque' }).count()) {
        await operatorPage.locator('textarea[name="reason"]').fill('Corrección de prueba sintética');
        await operatorPage.getByRole('button', { name: 'Revertir embarque' }).click();
        await operatorPage.waitForURL(/\/panel\/embarques\/$/);
        await operatorPage.locator('select[name="trip"]').selectOption(tripId);
        await operatorPage.locator('input[name="scan_value"]').fill(ticketCode.trim());
        await operatorPage.getByRole('button', { name: 'Validar pasaje' }).click();
        await expect(operatorPage.locator('[aria-live="polite"]')).toContainText(/embarque registrado/i);
        await operatorPage.goto(`/panel/operaciones/viajes/${tripId}/manifiesto/`);
        await expect(operatorPage.locator('p').filter({ hasText: 'Embarcados:' })).toContainText('Embarcados: 0');
        await expect(operatorPage.locator('p').filter({ hasText: 'Pendientes:' })).toHaveText(/Pendientes: [2-9]/);
      }

      await operatorPage.locator('input[name="scan_value"]').fill(ticketCode.trim());
      await operatorPage.getByRole('button', { name: 'Validar pasaje' }).click();
      await expect(operatorPage.locator('[aria-live="polite"]')).toContainText(/ya fue embarcado|ya fue utilizado/i);
      await operatorPage.goto(`/panel/operaciones/viajes/${tripId}/manifiesto/`);
      await expect(operatorPage.getByText('Embarcado', { exact: true })).toBeVisible();

      await operatorPage.goto('/panel/embarques/');
      await operatorPage.locator('select[name="trip"]').selectOption(tripId);
      await operatorPage.locator('input[name="scan_value"]').fill(ticketCode.trim());
      await operatorPage.getByRole('button', { name: 'Validar pasaje' }).click();
      await expect(operatorPage.getByRole('button', { name: 'Revertir embarque' })).toBeVisible();
      await operatorPage.locator('textarea[name="reason"]').fill('Reversión sintética de prueba');
      await operatorPage.getByRole('button', { name: 'Revertir embarque' }).click();
      await operatorPage.waitForURL(/\/panel\/embarques\/$/);
      await operatorPage.goto(`/panel/operaciones/viajes/${tripId}/manifiesto/`);
      await expect(operatorPage.locator('p').filter({ hasText: 'Embarcados:' })).toContainText('Embarcados: 0');
      await expect(operatorPage.locator('p').filter({ hasText: 'Pendientes:' })).toHaveText(/Pendientes: [2-9]/);

      await operatorPage.goto('/panel/embarques/');
      await operatorPage.locator('select[name="trip"]').selectOption(tripId);
      await operatorPage.locator('input[name="scan_value"]').fill(ticketCode.trim());
      await operatorPage.getByRole('button', { name: 'Validar pasaje' }).click();
      await expect(operatorPage.locator('[aria-live="polite"]')).toContainText(/embarque registrado/i);
      await operatorPage.locator('input[name="scan_value"]').fill(ticketCode.trim());
      await operatorPage.getByRole('button', { name: 'Validar pasaje' }).click();
      await expect(operatorPage.locator('[aria-live="polite"]')).toContainText(/ya fue embarcado|ya fue utilizado/i);

      await operatorPage.goto(`/panel/operaciones/viajes/${tripId}/`);
      await operatorPage.getByRole('link', { name: 'Iniciar viaje' }).click();
      await operatorPage.getByRole('button', { name: 'Confirmar e iniciar viaje' }).click();
      await operatorPage.waitForURL(new RegExp(`/panel/operaciones/viajes/${tripId}/$`));
      await operatorPage.getByRole('button', { name: 'Finalizar viaje' }).click();
      await operatorPage.waitForURL(new RegExp(`/panel/operaciones/viajes/${tripId}/$`));
      await operatorPage.goto('/panel/embarques/');
      await operatorPage.locator('select[name="trip"]').selectOption(tripId);
      await operatorPage.locator('input[name="scan_value"]').fill(ticketCode.trim());
      await operatorPage.getByRole('button', { name: 'Validar pasaje' }).click();
      await expect(operatorPage.locator('[aria-live="polite"]')).toContainText(/no estÃ¡ emitido|no es vÃ¡lido|no estÃ¡ vigente/i);
    } finally {
      await customerContext.close();
      await operatorContext.close();
    }
  });

  test.describe('QR pÃºblico sintÃ©tico', () => {
    test('QR vÃ¡lido es de solo consulta y el token manipulado es rechazado', async ({ page }) => {
      const secrets = readBrowserSecrets();
      await page.goto(secrets.qr_verification_url);
      await expect(page.getByText(/Pasaje V/)).toBeVisible();
      await expect(page.locator('body')).not.toContainText(/@demo\.scviajes\.invalid|99000101|3515550000/);
      await expect(page.locator('body')).not.toContainText(secrets.qr_verification_url.split('token=')[1]);
      await page.goto(`${secrets.qr_verification_url}x`);
      await expect(page.getByText(/Pasaje No Encontrado/)).toBeVisible();

      await panelLogin(page, adminUser);
      await page.goto(`/panel/operaciones/viajes/${secrets.qr_trip_id}/manifiesto/`);
      await expect(page.locator('p').filter({ hasText: 'Embarcados:' })).toContainText('Embarcados: 1');
      await expect(page.locator('p').filter({ hasText: 'Pendientes:' })).toHaveText(/Pendientes: [1-9]/);
    });
  });
});
