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

## Annunci immobiliari (2 fonti indipendenti, manifest completo Tecnocasa)

| Fonte | Gruppo | Annunci | Metodo |
|---|---|---|---|
| Tecnocasa (`www.tecnocasa.it`) | Tecnocasa | 185 | HTTP (JSON in `<estate-show-v1>`) |
| Gabetti (`www.gabetti.it`) | Gabetti Group | 7 | HTTP (meta tag) |

Totale **192 annunci** da 2 gruppi indipendenti. Tecnocasa: **manifest completo** —
185 URL scoperti, 184 processati (99 revisit, 85 nuovi, 1 non più disponibile),
backlog 0. Gabetti in Trento copre solo l'Alto Garda. Dedup per firma stabile;
rilevazioni (visite) separate dagli stati (contenuto). Gruppi candidati cross-fonte: 0.

Il campione non rappresenta l'intero mercato provinciale; copertura del mercato
**non determinabile**. Terza fonte non ancora attiva (Tempocasa Cloudflare, RE/MAX
sitemap blog).

## Macro

| Serie | Fonte | Chiave | Osservazioni | Stato |
|---|---|---|---|---|
| Tassi mutui nuove operazioni (Italia) | BCE | MIR.M.IT.B.A2C.A.R.A.2250.EUR.N | 140 (mensile 2015–) | acquisita |
| Indice NIC (Italia) | ISTAT | (dataflow non risolto) | 0 | bloccato (SSL sdmx.istat.it) |




