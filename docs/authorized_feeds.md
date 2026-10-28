# Authorized Feeds (tier 1 / tier 2)

How to ingest a source that forbids ordinary crawling **without** bypassing
anything. This document is the runbook for the licence register at
[`../config/source_licences.yaml`](../config/source_licences.yaml) and the
adapter in `app/fetchers/adapters/feed.py`.

## 1. The two legitimate channels

A source can enter the platform automatically through exactly two channels:

| Channel | Tier | How it works | Code path |
|---------|------|--------------|-----------|
| **Open data** | `tier3_open_data` | Public dataset whose own licence permits reuse (ISTAT, OMI, Agenzia Entrate NTN, Banca d'Italia, BCE, Eurostat, ISPAT). | `OpenDataAdapter` |
| **Authorized feed** | `tier1_api` / `tier2_feed` | An API or export the operator is *contractually* entitled to consume, with credentials the operator legitimately holds. | `AuthorizedFeedAdapter` |

Everything else stays where it is: Tier-4 scraping only for URLs that
`robots.txt` and the ToS actually permit, and Tier-5 restricted sources as
manual import + link only.

## 2. The invariant

```
no active licence record  ->  no automated request
```

`AuthorizedFeedAdapter.run()` refuses at three independent gates, and every
refusal is written to the evidence log:

1. **Licence gate** - `LicenceRegistry.assert_licensed(source)`. Missing,
   expired, not-yet-in-force, or endpoint-less records all refuse
   (`AccessOutcome.REFUSED_NO_LICENCE`).
2. **Scope gate** - the item must match a URL/file prefix *named in the
   contract*. Same host is not enough (`REFUSED_OUT_OF_SCOPE`).
3. **Credential gate** - HTTP channels require credentials supplied through the
   environment. Nothing is guessed or brute-forced
   (`REFUSED_NO_CREDENTIALS`).

A registry flag alone never enables a feed: `subito_it` stays refused while the
register is empty (see `tests/test_authorized_feed.py`).

The registry records the posture **without** a contract; a signed licence is the
documented exception that allows a specific, named endpoint to be requested.
That is why the licence - not the registry tier - is what unlocks a feed, and
why an operator cannot widen access by editing the registry: editing a tier
changes nothing until a human adds a licence record with a contract reference
and an endpoint list.

## 3. Onboarding a source, step by step

**Step 1 - obtain the authorisation.** A signed data licence, a partner
agreement, an API key issued to you, or an export the provider delivers to you.
Nothing below is meaningful without this.

**Step 2 - record the licence.** Add an entry to
`config/source_licences.yaml` (template at the bottom of that file). Name every
endpoint the contract covers:

```yaml
licences:
  - source: immobiliare_it            # must match config/source_registry.yaml
    grantor: "Immobiliare.it S.p.A."
    contract_ref: "TREI-2026-001"
    valid_from: "2026-11-01"
    valid_to: "2027-10-31"
    endpoints:
      - "https://api.immobiliare.it/partner/v2/listings"
    scope: [read_listings]
    permitted_formats: [json]
    attribution: "Dati forniti da Immobiliare.it"
    robots_applies: true
    max_requests_per_minute: 60
```

**Step 3 - supply credentials out of band.** Never in the register, never in
git. Per source `TREI_<SOURCE>_*`:

| Variable | Meaning |
|----------|---------|
| `TREI_<SOURCE>_CREDENTIAL_KIND` | `api_key` \| `bearer` \| `basic` \| `oauth2_client_credentials` (optional; inferred when omitted) |
| `TREI_<SOURCE>_API_KEY` | API key value |
| `TREI_<SOURCE>_API_KEY_HEADER` | header name, default `X-API-Key` |
| `TREI_<SOURCE>_API_KEY_QUERY_PARAM` | set when the key must go in the query string instead |
| `TREI_<SOURCE>_BEARER_TOKEN` | static access token |
| `TREI_<SOURCE>_USERNAME` / `_PASSWORD` | HTTP Basic |
| `TREI_<SOURCE>_CLIENT_ID` / `_CLIENT_SECRET` / `_TOKEN_URL` / `_SCOPE` | OAuth2 client-credentials grant |

`<SOURCE>` is the registry name upper-cased with non-alphanumerics collapsed to
`_` (`immobiliare_it` -> `TREI_IMMOBILIARE_IT_API_KEY`).

**Step 4 - dry-run and inspect the evidence.**

```bash
make cli-licences                 # register vs. sources; nothing licensed yet
make cli-feed SRC=immobiliare_it  URLS='https://api.immobiliare.it/partner/v2/listings'
make cli-audit                    # every attempt, allowed or refused
```

Or with the CLI directly, including a file drop:

```bash
.venv/bin/python -m app.fetchers.cli feed immobiliare_it \
  --url https://api.immobiliare.it/partner/v2/listings \
  --format json --records-path data --next-field meta.next --max-pages 20

.venv/bin/python -m app.fetchers.cli feed casa_it \
  --directory /srv/drops/casa --format csv --actor ops@example.org
```

Exit code `4` means **refused** - read the message and the audit log; do not
"fix" it by loosening a gate.

**Step 5 - only then** schedule it on the worker.

## 4. robots.txt policy for licensed endpoints

robots.txt applies to feed requests by default. Skipping it is a **double
opt-in**, because it must be a conscious decision on both sides:

1. the licence record sets `robots_applies: false` (i.e. the contract explicitly
   grants access to that path), **and**
2. the operator sets `TREI_FEED_RESPECT_ROBOTS=false`.

Neither alone is sufficient. Both are visible in review and recorded in the
audit log via `credential_summary()["robots_applied"]`.

## 5. Evidence log

`TREI_AUDIT_LOG` (default `var/audit/access.jsonl`) is append-only JSON Lines,
one event per attempt, including refusals. Each event carries the source, the
licence id and grantor, the endpoint, format, HTTP status, record count,
content hash, the actor, and the *redacted* credential description.

Secret handling is enforced in `app/fetchers/audit.py`:

* `redact()` masks any key that looks like a credential, while keeping
  descriptive names such as `api_key_header` and `has_api_key` readable;
* `scrub_url()` masks credential-looking query values, so a key passed in the
  query string never reaches the log or a lineage record.

## 6. What this layer deliberately cannot do

The platform will not be extended to defeat access controls. Specifically, none
of the following will be implemented, because each one turns a contractual
dispute into an offence under Italian law (art. 615-ter and following c.p.,
art. 640 c.p.) and under EU law, breaks the data licence the whole product
depends on, and would invalidate every number derived from it:

* CAPTCHA solving services, Cloudflare/anti-bot bypass, TLS or JS challenge
  solving;
* browser/token fingerprint spoofing, stealth or "undetected" driver patches;
* IP rotation, residential proxies, or any rotation whose purpose is to defeat a
  block or a rate limit;
* fake, shared, or purchased accounts;
* authentication bypass, cookie/session forgery, token theft;
* intentionally exceeding a documented rate limit.

Rate limiting here is *contractual*: `max_requests_per_minute` from the licence
raises the politeness delay, and `TREI_MIN_DELAY` raises it further. It is never
lowered to get data faster.

## 7. When no licensed channel exists

Choose one, in this order:

1. **Ask for access.** Send a formal data-access request; many portals have a
   partnership or data-licensing contact. Record the request in
   `legal_data_access.md`.
2. **Use open data.** The Tier-3 sources already cover price levels (OMI),
   transaction counts (NTN), notarial series (ISPAT), mortgage conditions
   (Banca d'Italia) and EU aggregates (Eurostat, BCE) with no legal exposure.
3. **Manual import + link.** Keep the source at Tier 5: an operator records an
   observation by hand, the listing is stored with an attribution link, and no
   automated request is ever made.

An empty licence register is a valid, honest and expected state. It is what the
compliance posture of this platform is built on.

