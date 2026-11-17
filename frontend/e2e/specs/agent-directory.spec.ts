import { test, expect } from '../support/test';

test.describe('AI agents registry', () => {
  test('opens from the main navigation and maps the current agent roles', async ({ page, backend }) => {
    void backend;
    await page.goto('/dashboard');
    await page.getByRole('navigation', { name: 'Main' }).getByRole('link', { name: 'AI Agents' }).click();

    await expect(page).toHaveURL('/agents');
    await expect(page.getByRole('heading', { name: 'Know Your Agents' })).toBeVisible();
    await expect(page.getByRole('heading', { name: 'Agent Registry' })).toBeVisible();
    await expect(page.getByText('11 roles')).toBeVisible();
    for (const name of ['Document classifier', 'Field extractor', 'Detection lead', 'Layout & Rendering Analyst', 'Hypothesis planner']) {
      await expect(page.getByRole('button', { name: new RegExp(`^${name},`) })).toBeVisible();
    }
  });

  test('opens an agent model card in a dialog from the map', async ({ page, backend }) => {
    void backend;
    await page.goto('/agents');

    const node = page.getByRole('button', { name: /^Detection lead,/ });
    await node.click();

    const dialog = page.getByRole('dialog', { name: 'Detection lead' });
    await expect(dialog).toBeVisible();
    await expect(dialog.getByText('MODEL CARD')).toBeVisible();
    await expect(dialog.getByRole('button', { name: 'View prompt' })).toBeVisible();

    await page.keyboard.press('Escape');
    await expect(dialog).toBeHidden();
    await expect(node).toBeFocused();
  });
});
