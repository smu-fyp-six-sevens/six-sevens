// Captures the real portal's screens for the film, against the frontend's own mock backend
// (frontend/e2e/support/mockBackend.ts) and a real captured case (anchorpoint, GLDB-02, 20 documents).
// Run from the repo root: see video/capture/README.md.
import { defineConfig } from '@playwright/test';

const PORT = 5175;
const DIST = process.env.CAPTURE_DIST ?? 'dist-capture';

export default defineConfig({
  testDir: '.',
  testMatch: /capture\.spec\.ts/,
  workers: 1,
  reporter: [['list']],
  use: {
    baseURL: `http://localhost:${PORT}`,
    viewport: { width: 1440, height: 900 },
    deviceScaleFactor: 2,
    launchOptions: { executablePath: process.env.CHROMIUM_PATH },
  },
  webServer: {
    command: `npx vite preview --outDir ${DIST} --port ${PORT} --strictPort`,
    cwd: '../../frontend',
    url: `http://localhost:${PORT}`,
    reuseExistingServer: true,
  },
});
