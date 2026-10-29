# Copertura dati (matrice)

Stato al 29 ottobre 2026. `presente` = osservazione attiva nello store;
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

**Lacune documentate**:
- La serie completa 2000–2023 (compravendite/mutui) e 1990–2024 (famiglie) **non
  è esposta** dalla vista HTML online (nessun pulsante di export sulla pagina
  `tavola.aspx`). Va cercata nel PDF dell'annuario o nell'open data ISPAT/ISTAT.
- 2013 e 2014 non sono nelle viste online: ΔH(2015) e Δ²H(2015) **non** sono
  calcolabili dai dati attuali (servirebbero 2014 e 2013).
- 2025 e 2026: `non pubblicato` (le serie si fermano a 2023/2024).
- Geografia famiglie = comunità di valle, **non** comune (I.28 è solo 2024);
  il totale provinciale è una riga distinta, non va sommato alle comunità.
- Licenza di riuso: `terms_verified: false` (robots.txt 404 ≠ licenza).

## Annunci immobiliari (primo pilota Tecnocasa)

Fonte: **Tecnocasa** (`www.tecnocasa.it`). robots.txt permissivo (consente il
crawling, sitemap `residenziali-*.xml.gz` esplicite); le schede espongono un JSON
strutturato in `<estate-show-v1 :estate="...">`. Acquisizione via HTTP, nessun
browser.

| Campo | Stato |
|---|---|
| Annunci acquisiti | 15 (campione, comuni di Andalo, Arco, Cavedago, ecc., provincia TN) |
| Campi | prezzo, prezzo precedente, sconto, superficie, locali, bagni, piano, ascensore, classe energetica, data pubblicazione, coordinate, URL |
| Comuni coperti | Andalo, Arco, Cavedago, ecc. (provincia di Trento) — NON l'intero mercato |
| Deduplica | per (source, native_id); storico prezzo conservato (osservazioni successive) |
| Note | superficie dichiarata "Mq" senza specifica commerciale/calpestabile; `surface_definition=non_specificata`. Codice ISTAT comunale non esposto dalla fonte (si usa il nome comune + provincia TN). |

La raccolta è un **primo campione**, non l'intero mercato provinciale. La soglia
di 30 annunci prevista richiede un ulteriore run (`--max-listings 30`) o la
schedulazione giornaliera (non ancora attiva).

