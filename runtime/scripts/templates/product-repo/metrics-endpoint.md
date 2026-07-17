# TEMPLATE — the product's metrics endpoint (for $PRODUCT_REPO's Go Lambda)

The org's demand-telemetry watcher (`scripts/metrics_watch.py`, DESIGN §16) polls the
product for its own counters. The contract is deliberately minimal: **one GET endpoint,
flat JSON, numeric values** — the org side maps fields to metrics in `metrics.yaml`
(`http_json` source, dotted `path` per metric), so adding a counter here needs only a
config line there, no collector code.

## Contract

- `GET ${REPORTS_API_URL}/reports/stats` (the endpoint already exists — grow it, don't
  move it; `metrics.yaml` points at it).
- Response: flat-ish JSON of **cumulative counters** (monotonic where possible — the
  watcher announces *changes*, so cumulative counts beat deltas):

```json
{
  "orders_count": 12,
  "reports_count": 47,
  "signups_count": 9,
  "pageviews_7d": 1043
}
```

- **No PII, ever** — counts only. The endpoint is public (it feeds a public build-in-
  public experiment); anything sensitive stays out. If abuse becomes a concern, gate it
  with a static header key and put the key in the org's `.env` (the watcher's
  `http_json` source can send headers — ADAPT `metrics.yaml` then).
- Cheap and cacheable: back it with the counters your backend already keeps (ADAPT:
  its primary datastore); add an item-count or a small `counters` table for
  the rest. A 60s cache header is fine — the watcher polls on the half hour.

## Implementation sketch (ADAPT to the real backend layout)

1. Extend the existing stats handler in `backend/` to read the extra counters from
   the datastore and add the new fields. Keep existing fields unchanged — anything
   the org's watcher already maps in `metrics.yaml` must keep its name.
2. Counters that don't exist yet (signups, pageviews) are OPTIONAL — the org-side
   extractor skips missing fields silently. Ship what's cheap now; add fields as the
   product grows. Pageviews are best served later by CloudFront/GA rather than the
   Lambda; don't block the endpoint on them.
3. Tests: a handler test asserting the JSON shape and that a missing counter renders
   as absent (not 0 — absent means "not instrumented", 0 means "instrumented, none").
4. Org side, after deploying: set `REPORTS_API_URL` in the org's `.env`, add any new
   fields to `metrics.yaml`, and run `scripts/metrics_watch.py sweep` once by hand to
   seed the store.
