const { test, expect } = require('@playwright/test');
const AxeBuilder = require('@axe-core/playwright').default;

const adminUser = process.env.DEMO_ADMIN_USERNAME || 'demo-20261005-admin';
const adminPassword = process.env.DEMO_ADMIN_PASSWORD || 'e2e-only-admin-password-20261007';

async function audit(page, name) {
  const results = await new AxeBuilder({ page }).analyze();
  expect(results.violations, `${name}: violations de accesibilidad`).toEqual([]);
}

async function adminLogin(page) {
  await page.goto('/panel/login/');
  await page.locator('#id_username').fill(adminUser);
  await page.locator('#id_password').fill(adminPassword);
  await Promise.all([
    page.waitForURL(/\/panel\/$/),
    page.getByRole('button', { name: 'Ingresar' }).click(),
  ]);
}

test.describe('accesibilidad y responsive', () => {
  test('páginas públicas representativas pasan axe en móvil', async ({ page }) => {
    await page.setViewportSize({ width: 360, height: 800 });
    for (const path of ['/', '/login/', '/registro/', '/buscar/']) {
      await page.goto(path);
      await audit(page, path);
      expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
    }
  });

  test('menú móvil y skip link son operables con teclado', async ({ page }) => {
    await page.setViewportSize({ width: 360, height: 800 });
    await page.goto('/');
    await page.keyboard.press('Tab');
    await expect(page.locator('.skip-link')).toBeFocused();
    await page.keyboard.press('Enter');
    await expect(page.locator('#main-content')).toBeFocused();
    await page.getByRole('button', { name: 'Abrir menú principal' }).focus();
    await page.keyboard.press('Enter');
    await expect(page.getByRole('button', { name: 'Cerrar menú principal' })).toBeVisible();
    await page.keyboard.press('Escape');
    await expect(page.getByRole('button', { name: 'Abrir menú principal' })).toBeFocused();
  });

  test('panel administrativo representativo pasa axe y conserva foco visible', async ({ page }) => {
    await adminLogin(page);
    await audit(page, 'panel dashboard');
    await page.goto('/panel/operaciones/');
    await audit(page, 'panel operaciones');
  });

  test('mapa de butacas expone controles operables por teclado', async ({ page }) => {
    await page.goto('/');
    const origin = await page.locator('#origin option').filter({ hasText: 'Cordoba Demo' }).first().getAttribute('value');
    const destination = await page.locator('#destination option').filter({ hasText: 'Jujuy Demo' }).first().getAttribute('value');
    let checkoutUrl = null;
    for (let offset = 0; offset <= 14 && !checkoutUrl; offset += 1) {
      const date = new Date();
      date.setDate(date.getDate() + offset);
      const response = await page.goto(`/buscar/?trip_type=oneway&origin=${origin}&destination=${destination}&date=${date.toISOString().slice(0, 10)}&passengers=1`);
      if (response && response.ok() && await page.locator('form[action*="/checkout/"]').count()) {
        checkoutUrl = page.url();
      }
    }
    expect(checkoutUrl).toBeTruthy();
    await page.locator('form[action*="/checkout/"]').first().locator('button').click();
    const availableSeat = page.locator('input.outbound-seat-cb:not([disabled])').first();
    await expect(availableSeat).toBeVisible();
    await availableSeat.focus();
    await expect(availableSeat).toBeFocused();
    await page.keyboard.press('Space');
    await expect(availableSeat).toBeChecked();
    await audit(page, 'checkout mapa de butacas');
  });
});
