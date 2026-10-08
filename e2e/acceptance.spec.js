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
}

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
    await expect(page.locator('input[type="file"][name="voucher"]')).toBeVisible();
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
});
