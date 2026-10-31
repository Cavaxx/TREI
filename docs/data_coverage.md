# Copertura dati (matrice)

Stato al 31 ottobre 2026. `presente` = osservazione attiva nello store;
`non trovato` = cercato nelle viste online esaminate e assente;
`non pubblicato` = anno oltre l'ultimo dato pubblicato; `bloccato` = accesso non
verificato/licenza non confermata; `non confrontabile` = definizione/geografia non
omogenea.

## Statistiche ISPAT (annuario online, statweb.provincia.tn.it)

Fonte: Istat / ISPAT. Navigazione ASP.NET cookieless (richiede browser).

| Indicatore | Territorio | Anni disponibili | Note |
|---|---|---|---|
| compravendite_residenziali (abitazione ed accessori) | provincia ITH20 | 2000, 2005, 2010, 2015, 2019–2023 | unità immobiliari; la vista online mostra anni selezionati, non l'intero 2000–2023 |
| compravendite_economiche (artigianale/commerciale/industriale/ufficio/rurale) | provincia ITH20 | idem | unità immobiliari |
| mutui (con ipoteca immobiliare) | provincia ITH20 | idem | numero di mutui |
| households (famiglie) | 16 comunità di valle + Provincia | 1990, 2010, 2015, 2020, 2023, 2024 | la vista online mostra 6 annate, non l'intero 1990–2024 |

**Lacune documentate** (invariate rispetto al 29/10, nuova sessione 31/10 interrotta prima del collaudo dell'indice: da rifare con `TREI_ALLOW_BROWSER=true`):
- La serie completa 2000–2023 (compravendite/mutui) e 1990–2024 (famiglie) **non
  è esposta** dalla vista HTML online (nessun pulsante di export sulla pagina
  `tavola.aspx`). Va cercata nel PDF dell'annuario o nell'open data ISPAT/ISTAT.
- 2013 e 2014 non sono nelle viste online: ΔH(2015) e Δ²H(2015) **non** sono
  calcolabili dai dati attuali (servirebbero 2014 e 2013).
- 2025 e 2026: `non pubblicato` (le serie si fermano a 2023/2024).
- Geografia famiglie = comunità di valle, **non** comune (I.28 è solo 2024);
  il totale provinciale è una riga distinta, non va sommato alle comunità.
- Licenza di riuso: `terms_verified: false` (robots.txt 404 ≠ licenza).

## Annunci immobiliari (2 fonti alimentate + 3 portali autorizzati in collaudo)

| Fonte | Gruppo | Annunci | Metodo |
|---|---|---|---|
| Tecnocasa (`www.tecnocasa.it`) | Tecnocasa | 257 (256 visti nell'ultimo run completo; 1 da riconfermare) | HTTP (JSON in `<estate-show-v1>`) |
| Gabetti (`www.gabetti.it`) | Gabetti Group | 7 (5 residenziali, 1 commerciale, 1 ricettivo) | HTTP (meta tag) |

Totale **264 ID registrati**, **263 disponibili**, 1 `to_reconfirm`
(61174027, Riva del Garda: 404 dal 30/10 e assente dalla sitemap del 31/10 —
NON una vendita). Perimetro Tecnocasa: vendita/appartamenti + ville +
rustici-cascine-case (256 URL). Esclusi dal perimetro e contati nel run
manifest: affitti 16, box 12, terreni 20, commerciali 91, cantieri (progetti) 5.

**Portali autorizzati** (config/portal_authorization_2026-10-31.txt, stealth
Kernel consentito per questo progetto): idealista_it, subito_it,
immobiliare_it — connettore `app/acquisition/connectors/portals.py` pronto,
collaudo live del 31/10: le pagine di ricerca rispondono vuote/guscio
(idealista `textlen=0`, subito solo header, immobiliare 0 anchor) anche con
stealth e headed: **bloccati**, con evidenza nei run (search_debug). Prossima
azione: verificare gli URL di ricerca corretti e l'effettiva applicazione
dello stealth sul piano Kernel.

**Candidati terza fonte verificati (31/10)**: Toscano (robots `Disallow: /`,
escluso), Engel & Völkers (sitemap senza pagine immobile, URL di ricerca 404,
non utilizzabile senza nuovi indizi), Casa.it (sitemap vendita/residenziale
raggiungibile con 240 URL provincia di Trento, ma pagine di annuncio 403 a
HTTP semplice: serve browser e non rientra nell'autorizzazione dei 3 portali).

## Macro

| Serie | Fonte | Chiave | Osservazioni | Stato |
|---|---|---|---|---|
| Tassi mutui nuove operazioni (Italia) | BCE | MIR.M.IT.B.A2C.A.R.A.2250.EUR.N | 140 (mensile 2015-01–2026-08, nessun mese mancante) | acquisita; aggiornamento incrementale verificato (run 31/10: 0 nuovi, ultimo 2026-08) |
| Indice NIC (Italia) | ISTAT | 167_744_DF_DCSP_NIC1B2015_1 / M.IT.39.4.000000 | 0 | bloccato: metadata ok, endpoint dati SDMX 404 su 8 varianti (evidenza in run); fallback: export CSV/XLSX ufficiale o databrowser via Kernel |

## Definizioni condivise (store = API = UI = export)

- **Disponibilità** (regola unica in `app/acquisition/ledger.availability`):
  `available` = visto con successo nell'ultimo run completo della fonte (o entro
  72h se il run è parziale); `to_reconfirm` = 404/410 o assente dalla sitemap;
  `confirmed_unavailable` = ≥3 404 in ≥7 giorni + assente dalla sitemap;
  `uncertain` = errore o non visitato. Un run parziale non disattiva mai gli URL
  non visitati. Una scomparsa NON è mai una vendita.
- **€/m²**: solo `available` + `unit_class=residenziale`, prezzo/superficie
  interpretabili, una rilevazione per ID.
- **Run** (data/runs.jsonl): scoperti, tentati, esiti, coda (pending,
  errori recuperabili, rinviati per budget, terminali), flag `complete`.
  Eventi per URL in data/listing_status.jsonl.





