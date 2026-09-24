import { defineConfig, devices } from '@playwright/test'

// E2E_BASE_URL points at an already running deployment; no dev server starts.
// E2E_CHROMIUM_EXECUTABLE can reuse a locally installed browser without a download.
const externalBaseURL = process.env.E2E_BASE_URL
const executablePath = process.env.E2E_CHROMIUM_EXECUTABLE

export default defineConfig({
  testDir: './tests',
  testMatch: '**/*.spec.ts',
  fullyParallel: true,
  forbidOnly: Boolean(process.env.CI),
  retries: 0,
  workers: 2,
  timeout: 30_000,
  expect: { timeout: 7_000 },
  reporter: [['list'], ['html', { open: 'never' }]],
  outputDir: 'test-results',
  use: {
    baseURL: externalBaseURL || 'http://127.0.0.1:5173',
    viewport: { width: 1440, height: 1000 },
    trace: 'retain-on-failure',
    screenshot: 'only-on-failure',
  },
  projects: [{
    name: 'chromium',
    use: {
      ...devices['Desktop Chrome'],
      viewport: { width: 1440, height: 1000 },
      launchOptions: executablePath ? { executablePath } : {},
    },
  }],
  webServer: externalBaseURL ? undefined : {
    command: 'npm run dev -- --host 127.0.0.1',
    url: 'http://127.0.0.1:5173',
    reuseExistingServer: !process.env.CI,
    timeout: 90_000,
  },
})
