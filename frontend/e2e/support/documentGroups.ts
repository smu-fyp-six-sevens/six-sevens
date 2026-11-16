import { expect, type Page } from '@playwright/test';

/**
 * The Documents card's groups (Uploaded, Approved, Rejected) fold and unfold, so a test that looks at a row opens the
 * group holding it first. What the case still lacks is in the header's Checklist panel, not here.
 */

const toggles = (page: Page) => page.getByTestId('documents-card').getByTestId('document-group');

// The categories can arrive a moment after the first one (the case's snapshot lands in pieces): wait until their number
// has held still between two looks, for at most the poll's timeout.
async function settled(page: Page): Promise<number> {
  await toggles(page).first().waitFor();
  let previous = -1;
  await expect
    .poll(
      async () => {
        const count = await toggles(page).count();
        const held = count === previous;
        previous = count;
        return held;
      },
      { intervals: [250], timeout: 10_000, message: 'the Documents card kept changing its categories' },
    )
    .toBe(true);
  return toggles(page).count();
}

/** Opens the named category, unless it is open already. */
export async function openGroup(page: Page, name: string): Promise<void> {
  const toggle = page.getByTestId('documents-card').getByTestId('document-group').filter({ hasText: name });
  if ((await toggle.getAttribute('aria-expanded')) !== 'true') await toggle.click();
}

/** Opens whichever category holds the text (a filename, a type): the open one if it is there, else the first that has it. */
export async function openGroupWith(page: Page, text: string): Promise<void> {
  const card = page.getByTestId('documents-card');
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
  const rows = page.getByTestId('documents-card').getByTestId('document-row');
  const texts: string[] = [];
  const count = await settled(page);
  for (let index = 0; index < count; index += 1) {
    const toggle = toggles(page).nth(index);
    if ((await toggle.getAttribute('aria-expanded')) !== 'true') await toggle.click();
    texts.push(...(await rows.allInnerTexts()));
  }
  return texts;
}
