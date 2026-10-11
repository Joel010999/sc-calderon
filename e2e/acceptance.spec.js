const { test, expect } = require('@playwright/test');

const adminUser = process.env.DEMO_ADMIN_USERNAME || 'demo-20261005-admin';
const sellerUser = process.env.DEMO_SELLER_USERNAME || 'demo-20261005-vendedor';
const adminPassword = process.env.DEMO_ADMIN_PASSWORD || 'e2e-only-admin-password-20261007';
const sellerPassword = process.env.DEMO_SELLER_PASSWORD || 'e2e-only-seller-password-20261007';
const customerEmail = process.env.DEMO_CUSTOMER_EMAIL || 'cliente@demo.scviajes.invalid';
const customerPassword = process.env.DEMO_CUSTOMER_PASSWORD || 'e2e-only-customer-password-20261007';
const customerHome = '/cliente/mis-viajes/';

async function panelLogin(page, username = sellerUser) {
  const password = username === adminUser ? adminPassword : sellerPassword;
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
      const ticketCode = await customerPage.locator('strong').filter({ hasText: /^TK-/ }).first().textContent();
      expect(ticketCode).toMatch(/^TK-/);

      const operatorPage = await operatorContext.newPage();
      await panelLogin(operatorPage);
      await operatorPage.goto('/panel/embarques/');
      const trip = operatorPage.locator('select[name="trip"] option').nth(2);
      const tripId = await trip.getAttribute('value');
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
      }

      await operatorPage.locator('input[name="scan_value"]').fill(ticketCode.trim());
      await operatorPage.getByRole('button', { name: 'Validar pasaje' }).click();
      await expect(operatorPage.locator('[aria-live="polite"]')).toContainText(/ya fue embarcado|ya fue utilizado/i);
      await operatorPage.goto(`/panel/operaciones/viajes/${tripId}/manifiesto/`);
      await expect(operatorPage.getByText('Embarcado', { exact: true })).toBeVisible();

      const publicResponse = await operatorPage.goto('/tickets/verify/?token=synthetic-invalid-token');
      expect(publicResponse.status()).toBe(200);
      await expect(operatorPage.getByText(/No se encontr/)).toBeVisible();
    } finally {
      await customerContext.close();
      await operatorContext.close();
    }
  });
});
