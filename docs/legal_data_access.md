# Legal Data Access Policy

This document is the binding policy for how the platform may acquire data.
It implements section 1 (mandatory source verification) and section 4 (tiers)
of the specification. Any adapter that does not comply is a bug.

## 1. Non-negotiable rules

1. **Verify before you build.** robots.txt, ToS, API terms and rate limits are
   checked *before* an adapter is written. Unverified ⇒ `UNKNOWN` ⇒ no adapter.
2. **Never bypass** CAPTCHAs, Cloudflare/anti-bot, authentication, paywalls or
   rate limits. Such sources are **Tier 5** (manual import + link only).
3. **Identify honestly.** A descriptive User-Agent with a contact address is
   mandatory (`TREI_USER_AGENT`). Do not impersonate a browser.
4. **Respect robots.txt programmatically.** `app/fetchers/robots.py` enforces it
   on every request; `TREI_RESPECT_ROBOTS=true` by default and must not be
   disabled in production.
5. **Be polite.** Minimum inter-request delay (default 2 s) per domain, honour
   any advertised `Crawl-delay`, back off on 429/5xx.
6. **Store only what we are allowed to.** Raw snapshots are kept for audit; do
   not redistribute restricted content (personal data, contact details, photos)
   beyond what the licence permits.
7. **Personal data minimisation (GDPR).** Do not store agency/owner personal
   data beyond what is necessary; keep the source link for attribution.

## 2. Tier decisions (see `config/source_registry.yaml`)

| Source | Tier | Automated? | Mode |
|--------|------|-----------|------|
| ISTAT, ISPAT, OMI, Agenzia Entrate, Banca d'Italia, BCE, Eurostat | 3 | yes | API/download, attribution |
| Astalegale | 4 | yes (robots allow) | polite crawl, low rate |
| immobiliare.it | 4 | conditional | permitted listing URLs only; prefer licensed feed |
| casa.it | 4 | conditional | permitted listing URLs only; prefer licensed feed |
| PVP | 3 | conditional | verify manually; source of truth for lots |
| idealista.it | 5 | **no** | data licence or omit |
| subito.it | 5 | **no** | manual import + link |
| wikicasa/trovocasa/bakeca/attico/fallco/asteannunci | 5 | **no (UNKNOWN)** | verify first |

The registry is the enforcement point: `SourceConfig.auto_acquisition_allowed`
returns `False` for unknown/restricted/licence-required sources and for any
Tier-5 source, and `SourceAdapter.assert_allowed()` refuses to run for them.

## 3. Licensing & attribution

- **Open data** (ISTAT CC BY 3.0 IT, Eurostat, BCE, Banca d'Italia): attribution
  required; store the source name and period on every point.
- **OMI / Agenzia Entrate**: public official quotations; keep `source='OMI'`.
- **Commercial portals**: only via an explicit licence. Record the licence id in
  the registry before enabling automated acquisition.

## 4. Data governance

- **Lineage:** every record carries source, URL, retrieved-at, `content_hash`,
  `parser_version`, `schema_version` (see `app/core/lineage.py`).
- **No invention:** missing values stay `unavailable`/`estimated`, never imputed
  silently (section 63).
- **Raw snapshots** live in object storage (MinIO/S3); the DB stores only
  metadata + hash (`raw_object` table).

## 5. Change protocol

When a source's terms change, or a new source is onboarded:

1. Update `config/source_registry.yaml` (`status`, `tier`, `recommended_mode`,
   `last_verified`).
2. Update `docs/data_sources.md`.
3. If a source moves to `restricted`, disable its adapter in the same PR.
