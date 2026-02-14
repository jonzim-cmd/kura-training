# Web Smoke Tests

Run from `web/`:

```bash
npm install
npx playwright install chromium
npm run test:smoke
```

What is covered:
- Login page renders in logged-out mode.
- Setup page exposes explicit auth + MCP availability states.
- Settings redirects to login when auth is unavailable.
- Settings security section exposes explicit backend-driven availability state.
