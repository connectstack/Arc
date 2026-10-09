# Reel Studio (web app source)

React 19 + TypeScript + Vite + Tailwind v4 + Radix primitives. The production build goes straight into the Python
package (`../src/reel/server/static`, committed), so `reel serve` needs no Node: this folder is only for changing the app.

```bash
npm ci                 # or: make ui-install (from the repository root)
npm run dev            # hot reload on :5173; proxies /api to `reel serve --no-token` on :8765
VITE_API=mock npm run dev   # the whole app on an in-memory mock engine, no server at all
npm test               # Vitest + Testing Library (components run on the mock engine)
npm run build          # type check + production build into ../src/reel/server/static
```

Architecture, the API, the security model and the accessibility checks are in [../docs/ui.md](../docs/ui.md).
