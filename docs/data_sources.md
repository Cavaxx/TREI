# Data Sources Audit

> **Status:** Phase-1 deliverable. Machine-readable counterpart: `config/source_registry.yaml`.
> **Audit date:** 2026-10-03. Every adapter MUST be gated by this registry.

## 1. Method

For each candidate source we verified, before writing any code:

1. **robots.txt** — fetched live and parsed (this file records what was found).
2. **Terms of Service / conditions of use** — reviewed for scraping/redistribution.
3. **API / feed availability** — public, partner, or commercial.
4. **Licence** — attribution and redistribution constraints (esp. open data).
5. **Rate limits** — explicit crawl-delay or documented API quotas.

A source that could not be verified is marked `UNKNOWN` and **no adapter is built**.

## 2. Acquisition tiers

| Tier | Meaning | Examples |
|------|---------|----------|
| 1 | Official / licensed API | (to be sourced: agency-network feed, Geoapify/OSM geocoding) |
| 2 | Authorized feed / partner export | agency exports, portal data licence |
| 3 | Open/public dataset | ISTAT, ISPAT, OMI, Agenzia Entrate, Banca d'Italia, BCE, Eurostat |
| 4 | Ordinary scraping (after legal check) | Astalegale (robots allow), immobiliare.it (partial) |
| 5 | Restricted — manual import + link only | idealista, subito |

**Rule:** never advance a source to Tier 4 without a documented robots+ToS check.
Never bypass CAPTCHAs, Cloudflare, authentication or rate limits — those sources are Tier 5.

## 3. Portals (asking-price listings)

### immobiliare.it — `tier4_scraping` / **WARNING**
- robots.txt: `Allow` for general listing pages; `Disallow` for `/dettaglio.php`,
  `/ricerca.php`, `/ricerca-mappa/`, `/search-*`, `/user/`, `/utente/`, print views.
- Sitemaps: `/sitemap.xml`, `/news/sitemap_index.xml`.
- No crawl-delay advertised → we enforce ≥ 2 s/request ourselves.
- Commercial data services exist → **prefer them over scraping**.
- Duplication risk: high (cross-listed with the others).

### idealista.it — `tier5_restricted` / **LICENSE_REQUIRED**
- robots.txt is highly restrictive: blocks `/ajax/`, listing-dispatcher, language
  trees, ordering query params, several filter patterns.
- **Do not scrape.** Obtain a data licence or omit the source.

### casa.it — `tier4_scraping` / **WARNING**
- robots.txt `Allow: /` with many `Disallow` (`/srp/`, `/client-pdp/api/`,
  `/immobili/idl/`, `/pubblica/`, …). Per-category sitemaps.
- Only permitted listing-detail URLs, ≥ 2 s/request.

### subito.it — `tier5_restricted` / **restricted**
- robots.txt not retrievable (HTTP 403 to our user-agent); anti-bot protection present.
- **Manual import + link only. Do not attempt to bypass.**

### wikicasa.it, trovocasa.it, bakeca.it, attico.it — **UNKNOWN**
- Not yet verified. No adapter until robots.txt and ToS are reviewed.

## 4. Auction sources

### PVP (pvponline.giustizia.it) — `tier3_open_data` / **WARNING**
- Authoritative for procedure id, lot, auction date and documents.
- robots.txt fetch failed during the audit → verify manually before any automation.
- **PVP is the source of truth** when data is available.

### Astalegale (astalegale.net) — `tier4_scraping` / **OK**
- robots.txt: `User-agent: * Allow: /` with a few disallows (hash URLs, `CmsFiles`).
- Frequently re-lists PVP lots → dedup by (tribunale, n. procedura, lotto).

### Fallco Aste, Asteannunci — **UNKNOWN**
- Not verified. No adapter until robots/ToS reviewed.

## 5. Official statistics (Tier 3)

| Source | Provides | Cadence | Licence |
|--------|----------|---------|---------|
| ISTAT | IPAB price indices, population, households | quarterly/annual | CC BY 3.0 IT |
| ISPAT (Prov. Trento) | dwellings, households, buildings | annual | open (check per dataset) |
| OMI / Agenzia Entrate | semi-annual **quotations** per OMI zone | S1/S2 | public, attribution |
| Agenzia Entrate NTN | transaction counts (series `OMI_NTN`) | annual | public, attribution |
| FIMAA | FIMAA/AE report (series `FIMAA_AE`) | annual | check terms |
| Banca d'Italia | mortgage rates, credit | monthly/quarterly | open |
| BCE | policy rates, credit conditions | monthly | open |
| Eurostat | HPI, NUTS-2 housing | monthly/quarterly | open |

> **OMI warning:** OMI values are *quotations over a semester*, NOT transactions.
> They are stored with `measure_type = omi_min/omi_max` and semester period semantics.

## 6. Known gaps / next verification steps

1. Resolve PVP and ISPAT access manually (both failed automated fetch).
2. Verify wikicasa, trovocasa, bakeca, attico, fallco, asteannunci (robots + ToS).
3. Identify the first Tier-1/Tier-2 partner (agency network) to obtain listings legally.
4. Confirm geocoding provider and its licence (OSM/Nominatim usage policy or a paid API).
