import { test, expect } from '../support/test';

test.describe('AI agents registry', () => {
  test('opens from the main navigation and explains the current agent roles and workflow', async ({ page, backend }) => {
    void backend;
    await page.goto('/dashboard');
    await page.getByRole('navigation', { name: 'Main' }).getByRole('link', { name: 'AI Agents' }).click();

    await expect(page).toHaveURL('/agents');
    await expect(page.getByRole('heading', { name: 'Know Your Agents' })).toBeVisible();
    await expect(page.getByRole('heading', { name: 'Agent Registry' })).toBeVisible();
    await expect(page.getByText('8 roles')).toBeVisible();
    await expect(page.getByRole('heading', { name: 'Document classifier' })).toBeVisible();
    await expect(page.getByRole('heading', { name: 'Field extractor' })).toBeVisible();
    await expect(page.getByRole('heading', { name: 'Detection lead' })).toBeVisible();
    await expect(page.getByRole('heading', { name: 'Layout & Rendering Analyst' })).toBeVisible();
    await expect(page.getByRole('heading', { name: 'Hypothesis planner' })).toBeVisible();
    await expect(page.getByText('MODEL CARD').first()).toBeVisible();
    await expect(page.getByRole('heading', { name: 'Where Each Agent Fits' })).toBeVisible();
    await expect(page.getByRole('heading', { name: 'Document Processing', exact: true })).toBeVisible();
    await expect(page.getByRole('heading', { name: 'Fraud Detection', exact: true })).toBeVisible();
    await expect(page.getByRole('heading', { name: 'Fraud Detection Specialists', exact: true })).toBeVisible();
    await expect(page.getByRole('heading', { name: 'Case Analysis', exact: true })).toBeVisible();
  });
});
