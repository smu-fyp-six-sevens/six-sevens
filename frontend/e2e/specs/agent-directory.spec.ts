import { test, expect } from '../support/test';

test.describe('AI agents guide', () => {
  test('opens from the main navigation and explains the current agent roles', async ({ page, backend }) => {
    void backend;
    await page.goto('/dashboard');
    await page.getByRole('navigation', { name: 'Main' }).getByRole('link', { name: 'AI Agents' }).click();

    await expect(page).toHaveURL('/agents');
    await expect(page.getByRole('heading', { name: 'Know Your Agents' })).toBeVisible();
    await expect(page.getByRole('heading', { name: 'Document Processing' })).toBeVisible();
    await expect(page.getByRole('heading', { name: 'Detection Coordination' })).toBeVisible();
    await expect(page.getByRole('heading', { name: 'Fraud Detection Specialists' })).toBeVisible();
    await expect(page.getByRole('heading', { name: 'Case Analysis' })).toBeVisible();
    await expect(page.getByRole('heading', { name: 'Layout & Rendering Analyst' })).toBeVisible();
    await expect(page.getByRole('heading', { name: 'Hypothesis planner' })).toBeVisible();
  });
});
