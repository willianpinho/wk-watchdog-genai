# Vendored static assets

These files are committed deliberately (not gitignored) and served by
the FastAPI `StaticFiles` mount at `/static`.

## uPlot 1.6.31

`uPlot.iife.min.js` (~50 KB) and `uPlot.min.css` (~2 KB) come from
the official uPlot release tag, vendored from:

```
https://cdn.jsdelivr.net/npm/uplot@1.6.31/dist/uPlot.iife.min.js
https://cdn.jsdelivr.net/npm/uplot@1.6.31/dist/uPlot.min.css
```

**Why vendored, not CDN.** Supply-chain hygiene: a runtime CDN can
be hijacked, return different bytes per request, or disappear. We
ship a single fixed version of uPlot pinned in this repo so the
deployed dashboard cannot be made to load arbitrary JS.

To upgrade, run:

```
curl -fsS "https://cdn.jsdelivr.net/npm/uplot@<NEW>/dist/uPlot.iife.min.js" \
    -o apps/api/src/watchdog_api/static/uPlot.iife.min.js
curl -fsS "https://cdn.jsdelivr.net/npm/uplot@<NEW>/dist/uPlot.min.css" \
    -o apps/api/src/watchdog_api/static/uPlot.min.css
```

Verify with `make test` afterwards.
