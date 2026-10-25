import { defineConfig, devices } from '@playwright/test';

/**
 * Regression suite (SCRUM-206, SCRUM-246). Runs the real frontend against a mocked
 * backend (e2e/support/mockBackend.ts) — no FastAPI, Supabase or Dify.
 *
 * Test titles carry tags for filtering and reporting:
 *   @sprint1 / @sprint2 / @sprint3, the Jira story (@SCRUM-6) and the manual test case
 *   ID from the sprint test sheets (@DI-001), e.g.
 *   npx playwright test --grep @sprint1
 *   npx playwright test --grep @DI-008
 */
const PORT = 5174;

export default defineConfig({
  testDir: './e2e/specs',
  fullyParallel: true,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 1 : 0,
  // CI's default is half the cores, which is 1 on a 2-vCPU runner; the tests mostly wait on the browser.
  workers: process.env.CI ? 2 : undefined,
  reporter: [
    ['list'],
    ['html', { open: 'never', outputFolder: 'playwright-report' }],
    ['json', { outputFile: 'test-results/results.json' }],
  ],
  use: {
    baseURL: `http://localhost:${PORT}`,
    // Recorded only when a failed test is retried, not for every test.
    trace: 'on-first-retry',
    screenshot: 'only-on-failure',
  },
  projects: [{ name: 'chromium', use: { ...devices['Desktop Chrome'] } }],
  webServer: {
    // The production build, served as built: the dev server compiles modules on demand for every browser,
    // which is slow and timed tests out under load. Its own folder, because VITE_API_URL below is built in.
    command: `npm run build -- --outDir dist-e2e && npm run preview -- --outDir dist-e2e --port ${PORT} --strictPort`,
    url: `http://localhost:${PORT}`,
    // Its own port (not the usual 5173), so it never reuses a dev server
    // that's pointed at the real backend.
    reuseExistingServer: !process.env.CI,
    env: {
      // An unroutable API host: every call must be answered by the mock.
      VITE_API_URL: 'http://api.test',
    },
  },
});
