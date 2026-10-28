# Frontend

Two things live here conceptually:

1. **The static demo dashboard** is currently served directly by the API at `/`
   (`DASHBOARD_HTML` in `app/api/main.py`). It requires no build step and clearly
   warns that the data is synthetic.

2. **The Next.js application** (map, dashboards, valuation explorer) is the
   intended production frontend. It is intentionally **not yet built** here:
   - no Node toolchain is available in this environment, and
   - the meaningful screens depend on real, legally-verified data.

When the data layer is fed by real sources, scaffold the app here:

```bash
npx create-next-app@latest . --ts --app
# then point it at the API:
#   NEXT_PUBLIC_API_BASE=http://localhost:8000
```

Planned pages: market dashboard (€/m² by comune, regime), map explorer (with the
"visible supply ≠ total supply" disclaimer), listing explorer, valuation tool,
auction explorer (with risk flags), and a data-quality/lineage view.
