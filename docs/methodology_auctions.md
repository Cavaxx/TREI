# Methodology: Auctions

Implements sections 29-32. Auction lots are a **separate asset class** with a
separate model; they are never valued with the ordinary asking-price model. A
low base price is **not** a bargain.

## 1. Lot identity & dedup (section 3)

PVP is the source of truth. The same lot re-listed on Astalegale/Fallco/
Asteannunci must collapse to one canonical auction.

- **Primary key**: `(tribunale, n. procedura, lotto)` → `canonical_procedure_key`.
- **Fallback**: PVP id, then a content hash.
- `merge_auction_records` merges contributors with **PVP-winning** precedence and
  keeps the `sources[]` list (provenance preserved).
- `deduplicate_auctions` groups by `master_auction_id` and merges each group.

## 2. Effective economic value (section 30)

Total acquisition cost, not the headline price:

```
estimated_total_acquisition_cost =
      auction_purchase_price
    + taxes + transaction_costs
    + renovation + regularization
    + arrears (condominio / tributi)
    + possession_cost (occupancy / release)
    + risk_adjustment
    + other_costs
```

Two discounts are reported:

- `naive_discount = 1 − minimum_offer / appraisal_value` (what headlines show);
- `risk_adjusted_discount = 1 − total_cost / estimated_market_value` (what
  actually matters).

The cost breakdown is exposed so the gap between the two is visible.

## 3. Anomaly & risk detection (section 31)

Every lot is scanned for legal/physical risk flags:

`occupied`, `surface_right` (diritto di superficie), `usufruct`,
`bare_ownership`, `co_ownership`, `undivided_share`, `building_abuse`,
`cadastral_discrepancy`, `needs_regularization`, `mortgages`, `foreclosures`,
`condominium_arrears`, `not_full_ownership`, `timeshare`, `missing_documents`.

Weighted into a `risk_level` (`LOW`/`MEDIUM`/`HIGH`). The engine must never label
a lot an "affare" on price alone — the risk flags are always shown.

## 4. Documents

Perizia / avviso di vendita PDFs are stored by `document_hash`; the hash is part
of the lot identity so a changed document is detectable.

## 5. Rules

- Auction prices (`auction_base`, `auction_minimum_offer`, `appraisal_value`) are
  separate `measure_type` values — never mixed with asking or transacted prices.
- Auction lots live in `auction_lot`, not in the ordinary listing tables.
- Unless otherwise stated, a lot's value is an estimate with its own interval and
  confidence; occupancy and building abuse can dominate the economics.
