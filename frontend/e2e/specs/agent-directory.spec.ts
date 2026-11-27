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

  test('says "Anomaly detection" for the "Fraud detection" groups, on the graph and in the model card', async ({ page, backend }) => {
    void backend;
    await page.goto('/agents');

    await expect(page.getByRole('button', { name: 'Detection lead, Anomaly detection', exact: true })).toBeVisible();
    await expect(page.getByRole('button', { name: 'Layout & Rendering Analyst, Anomaly detection specialists', exact: true })).toBeVisible();
    await expect(page.getByRole('button', { name: /Fraud/ })).toHaveCount(0);

    await page.getByRole('button', { name: /^Content Validation Analyst,/ }).click();
    await expect(page.getByRole('dialog').locator('.agent-directory-role')).toHaveText('Anomaly detection specialists');
  });

  test('a model card lists each tool, opens its description on click, and the graph has no unversioned legend when all are versioned', async ({ page, backend }) => {
    void backend;
    await page.goto('/agents');
    await expect(page.getByText('Prompt not versioned')).toHaveCount(0);

    await page.getByRole('button', { name: /^Layout & Rendering Analyst,/ }).click();
    const dialog = page.getByRole('dialog', { name: 'Layout & Rendering Analyst' });
    await expect(dialog.getByRole('heading', { name: /^Tools/ })).toContainText('1');
    const tool = dialog.locator('.agent-tool', { hasText: 'font_consistency_tool' });
    await expect(tool).toBeVisible();
    await expect(tool.getByText('What font_consistency_tool does.')).toBeHidden();

    await tool.locator('summary').click();
    await expect(tool.getByText('What font_consistency_tool does.')).toBeVisible();
  });
});
