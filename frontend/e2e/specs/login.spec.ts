import type { Page } from '@playwright/test';
import { test, expect } from '../support/test';
import { makeCase } from '../support/mockBackend';

/**
 * Signing in, and what happens without a session: the real login form against the mock's /auth/login (it
 * accepts only backend.credentials and answers anything else with 401, like routers/auth), and the redirects
 * App.tsx's RequireAuth and lib/api.ts's authFetch make when there is no token or the API rejects it.
 */

const signIn = async (page: Page, username: string, password: string) => {
  await page.getByLabel('Username').fill(username);
  await page.getByLabel('Password', { exact: true }).fill(password);
  await page.getByRole('button', { name: /Sign in/ }).click();
};
const storedToken = (page: Page) => page.evaluate(() => localStorage.getItem('authToken'));

test.describe('Signed out', () => {
  test.use({ loggedIn: false });

  test('a wrong password is refused with an error, and the officer stays signed out', async ({ page, backend }) => {
    await page.goto('/login');

    await signIn(page, backend.credentials.username, 'wrong-password');

    await expect(page.getByRole('alert')).toContainText('Invalid username or password.');
    await expect(page).toHaveURL('/login');
    expect(await storedToken(page)).toBeNull();
    expect(backend.requestsTo('POST', '/auth/login')).toHaveLength(1);
  });

  test('the right password signs in and opens the case queue', async ({ page, backend }) => {
    await page.goto('/login');

    await signIn(page, backend.credentials.username, backend.credentials.password);

    await expect(page).toHaveURL('/cases');
    expect(await storedToken(page)).not.toBeNull();
  });

  test('opening a case without signing in goes to the sign-in page instead', async ({ page, backend }) => {
    const theCase = makeCase({ greenlink_case_id: 'GLDB-1001' });
    backend.cases.push(theCase);

    await page.goto(`/cases/${theCase.greenlink_case_id}`);

    await expect(page).toHaveURL('/login');
    await expect(page.getByRole('button', { name: /Sign in/ })).toBeVisible();
    // Nothing about the case was requested without a session.
    expect(backend.requests.filter((r) => r.path.startsWith('/cases'))).toEqual([]);
  });
});

test('an expired session sends the officer back to sign in', async ({ page, backend }) => {
  backend.sessionExpired = true;

  await page.goto('/cases');

  // authFetch got a 401, cleared the token and left for the sign-in page. (The test's own token is put back on
  // every page load by support/test.ts, so the cleared token itself can't be checked here.)
  await expect(page).toHaveURL('/login');
  await expect(page.getByRole('button', { name: /Sign in/ })).toBeVisible();
  expect(backend.requestsTo('GET', /^\/cases/)).not.toHaveLength(0);
});
