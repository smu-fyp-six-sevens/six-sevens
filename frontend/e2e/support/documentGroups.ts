import type { Page } from '@playwright/test';

/**
 * The Documents card's categories (Missing, Additional, Needs Review, Pending Approval, Approved, Rejected) open one
 * at a time, so a test that looks at a row opens the category holding it first.
 */

const toggles = (page: Page) => page.locator('.documents-card .documents-table-group--toggle');

// The categories can arrive a moment after the first one (the case's snapshot lands in pieces): wait until their number
// has held still.
async function settled(page: Page): Promise<number> {
  await toggles(page).first().waitFor();
  let count = await toggles(page).count();
  for (;;) {
    await page.waitForTimeout(250);
    const next = await toggles(page).count();
    if (next === count) return count;
    count = next;
  }
}

/** Opens the named category, unless it is open already. */
export async function openGroup(page: Page, name: string): Promise<void> {
  const toggle = page.locator('.documents-card .documents-table-group--toggle', { hasText: name });
  if ((await toggle.getAttribute('aria-expanded')) !== 'true') await toggle.click();
}

/** Opens whichever category holds the text (a filename, a type): the open one if it is there, else the first that has it. */
export async function openGroupWith(page: Page, text: string): Promise<void> {
  const card = page.locator('.documents-card');
  const holds = async () => (await card.getByText(text).count()) > 0;
  if (await holds()) return;
  const count = await settled(page);
  for (let index = 0; index < count; index += 1) {
    const toggle = toggles(page).nth(index);
    if ((await toggle.getAttribute('aria-expanded')) !== 'true') await toggle.click();
    if (await holds()) return;
  }
  throw new Error(`No category of the Documents card holds "${text}"`);
}

/** The text of every document row in the card, whichever category it sits in. */
export async function allRowTexts(page: Page): Promise<string[]> {
  const rows = page.locator('.documents-card .documents-table-row');
  const texts: string[] = [];
  const count = await settled(page);
  for (let index = 0; index < count; index += 1) {
    const toggle = toggles(page).nth(index);
    if ((await toggle.getAttribute('aria-expanded')) !== 'true') await toggle.click();
    texts.push(...(await rows.allInnerTexts()));
  }
  return texts;
}
