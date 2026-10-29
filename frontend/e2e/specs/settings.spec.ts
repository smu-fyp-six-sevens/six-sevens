import { test, expect } from '../support/test';

test.describe('Finding reviews setting', () => {
  test('an admin turns finding reviews off, and the dashboard drops its Accuracy tab', async ({ page, backend }) => {
    backend.isAdmin = true;
    await page.goto('/settings');

    const toggle = page.getByRole('switch', { name: 'Review findings' });
    await expect(toggle).toBeChecked();
    await toggle.click();

    await expect(toggle).not.toBeChecked();
    await expect.poll(() => backend.requestsTo('PUT', '/settings/finding-reviews').length).toBe(1);
    expect(backend.findingReviewsEnabled).toBe(false);

    await page.getByRole('link', { name: 'Dashboard' }).click();
    await expect(page.getByRole('button', { name: 'Overview' })).toBeVisible();
    await expect(page.getByRole('button', { name: 'Accuracy' })).toHaveCount(0);
  });

  test('a loan officer sees the setting but cannot change it', async ({ page, backend }) => {
    backend.findingReviewsEnabled = false;
    await page.goto('/settings');

    const toggle = page.getByRole('switch', { name: 'Review findings' });
    await expect(toggle).not.toBeChecked();
    await expect(toggle).toBeDisabled();
  });
});
