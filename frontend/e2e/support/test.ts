import { test as base, expect } from '@playwright/test';
import { MockBackend } from './mockBackend';

/**
 * Every regression spec imports `test` from here, not @playwright/test:
 * each test gets its own MockBackend already wired into the page, and a
 * logged-in browser (the officer's name the app keeps in localStorage; the
 * session itself is a cookie the mock doesn't check).
 *
 * login.spec.ts opts out with `test.use({ loggedIn: false })` so it can
 * exercise the real login form.
 */
export const test = base.extend<{ backend: MockBackend; loggedIn: boolean }>({
  loggedIn: [true, { option: true }],

  backend: async ({ page, loggedIn }, use) => {
    const backend = new MockBackend();
    await backend.install(page);
    if (loggedIn) {
      await page.addInitScript(() => localStorage.setItem('authUser', 'officer'));
    }

    await use(backend);

    expect(backend.unmockedRequests, 'requests the mock backend did not handle').toEqual([]);
  },
});

export { expect };
