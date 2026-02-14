import { expect, test } from '@playwright/test';

const MOCK_AUTH_MODE_KEY = 'kura_mock_auth_mode';

test.describe('Web Confidence Surface Smoke', () => {
  test('login flow renders when forced to logged-out mock mode', async ({ page }) => {
    await page.addInitScript((key) => {
      localStorage.setItem(key, 'logged_out');
    }, MOCK_AUTH_MODE_KEY);

    await page.goto('/en/login');
    await expect(page.getByTestId('login-form')).toBeVisible();
    await expect(page.getByTestId('login-email')).toBeVisible();
    await expect(page.getByTestId('login-password')).toBeVisible();
    await expect(page.getByTestId('login-submit')).toBeVisible();
  });

  test('setup exposes logged-out account state and explicit MCP availability signal', async ({ page }) => {
    await page.addInitScript((key) => {
      localStorage.setItem(key, 'logged_out');
    }, MOCK_AUTH_MODE_KEY);

    await page.goto('/en/setup');
    await expect(page.getByTestId('setup-account-description')).toHaveAttribute(
      'data-auth-state',
      'logged-out',
    );

    await page.getByRole('button', { name: /MCP/i }).click();
    await expect(page.getByTestId('setup-mcp-status')).toHaveAttribute('data-mcp-live', 'false');
  });

  test('settings redirects to login when auth is unavailable', async ({ page }) => {
    await page.addInitScript((key) => {
      localStorage.setItem(key, 'logged_out');
    }, MOCK_AUTH_MODE_KEY);

    await page.goto('/en/settings');
    await expect(page).toHaveURL(/\/login$/);
  });

  test('settings shows explicit security state for authenticated mock mode', async ({ page }) => {
    await page.addInitScript((key) => {
      localStorage.removeItem(key);
    }, MOCK_AUTH_MODE_KEY);

    await page.goto('/en/settings');
    await expect(page.getByTestId('settings-nav-security')).toBeVisible();
    await page.getByTestId('settings-nav-security').click();
    await expect(page.getByTestId('settings-security-state')).toHaveAttribute(
      'data-security-state',
      'unavailable',
    );
  });
});
