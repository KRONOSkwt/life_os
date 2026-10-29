import { defineConfig } from '@playwright/test'
import { fileURLToPath } from 'node:url'

export default defineConfig({
  testDir: './e2e',
  workers: 1,
  fullyParallel: false,
  retries: 0,
  timeout: 30000,
  reporter: 'list',
  outputDir: fileURLToPath(new URL('../../.cache/browser-results/', import.meta.url)),
  use: {
    headless: true,
    viewport: { width: 1365, height: 900 },
    screenshot: 'only-on-failure',
    trace: 'off',
  },
  projects: [{
    name: 'chromium',
    use: {
      browserName: 'chromium',
      channel: process.env.LIFE_OS_BROWSER_CHANNEL ?? (process.platform === 'win32' ? 'msedge' : 'chromium'),
    },
  }],
})
