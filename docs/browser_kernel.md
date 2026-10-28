# Kernel browser provider

> Stato: **implementato, testato offline, NON ancora verificato live** (vedi §6).

## 1. Cosa aggiunge (e cosa non aggiunge)

Kernel aggiunge un **browser remoto** per pagine dinamiche: navigazione,
selezione di periodo/territorio, paginazione via JavaScript e download tramite
browser. Non fornisce un dataset immobiliare, non recupera storico mancante e non
conferisce diritti di riuso su una fonte.

Ordine di preferenza del router (`app/acquisition/router.py`):

1. API, feed, export o file ufficiali usabili direttamente;
2. HTTP e parsing di pagine pubbliche, quando sufficienti e ammessi;
3. **Kernel** quando servono JS, moduli, paginazione dinamica, selezione di
   periodo/territorio o download via browser;
4. import manuale o feed autorizzato per fonti non acquisibili automaticamente.

Il fallimento di HTTP **non** autorizza il passaggio al browser: il browser è una
scelta di *metodo* (rendering), non un ripiego per un diniego di accesso.

## 2. Adapter

`app/browser/provider.py` — `KernelBrowserProvider` (Playwright Execution):

- la chiave è verificata **prima** di istanziare il client SDK: senza
  `KERNEL_API_KEY` ogni comando fallisce con `configuration_missing`, senza aprire
  sessioni e senza dati fittizi;
- il rilascio avviene in `close()`/`finally` (anche su errori di parsing,
  cancellazione, timeout o download) con `browsers.delete_by_id`, non con un
  `close` locale di Playwright;
- `timeout_seconds` (standby) resta distinto dalla scadenza applicativa
  (`TREI_KERNEL_MAX_SESSION_WALL_SECONDS`) e dal budget cumulativo
  (`TREI_KERNEL_MAX_TOTAL_BROWSER_SECONDS`);
- i download sono letti con `fs.read_file` **prima** del rilascio (i file remoti
  non sopravvivono alla distruzione) e validati per tipo/dimensione/SHA-256
  (`app/browser/downloads.py`), rifiutando una pagina HTML di errore/login come se
  fosse un dataset;
- il fallimento del cleanup conserva il `session_id` nell'errore
  (`CLEANUP_FAILED`) per un recupero limitato; non elimina sessioni estranee;
- `max_retries=0` sull'SDK: i 429 sono contabilizzati e decisi da noi, mai ritentati
  ciecamente (evita browser duplicati dopo risposte incerte).

`app/browser/budget.py` — `RunBudget`: limite di sessioni, budget cumulativo in
secondi e checkpoint su file con lock `fcntl`, così più worker/processi sullo
stesso host condividono lo stesso budget. I limiti sono **di run**, non per worker.

## 3. Configurazione

Tutte le variabili sono parametri applicativi letti da `os.environ`; il loader
`app/core/envfile.py` importa solo `KERNEL_API_KEY` e `TREI_*` da `.env` senza
sovrascrivere l'ambiente. Vedi `.env.example` per default e significato. In
particolare:

| Variabile | Default | Significato |
|---|---|---|
| `KERNEL_API_KEY` | vuota | unica credenziale richiesta da Kernel |
| `TREI_BROWSER_PROVIDER` | `kernel` | provider per job che richiedono browser |
| `TREI_ACQUISITION_MODE` | `pilot` | esecuzione limitata, avviata esplicitamente |
| `TREI_TARGET_START_YEAR`/`_END_YEAR` | 2015/2026 | finestra richiesta, non garanzia di copertura |
| `TREI_KERNEL_MAX_SESSIONS_PER_RUN` | 5 | sessioni/tentativi di creazione, retry inclusi |
| `TREI_KERNEL_MAX_SESSION_WALL_SECONDS` | 180 | scadenza applicativa per sessione |
| `TREI_KERNEL_MAX_TOTAL_BROWSER_SECONDS` | 900 | budget cumulativo del run |
| `TREI_KERNEL_STANDBY_TIMEOUT_SECONDS` | 60 | timeout di standby passato a Kernel |
| `TREI_BROWSER_ALLOWED_DOMAINS` | `statweb.provincia.tn.it` | filtro tecnico dei siti pilota |

Una raccolta troncata dal budget deve risultare `partial` con checkpoint e motivo,
mai `completed`.

## 4. Comandi

```bash
pip install -e ".[browser]"          # aggiunge kernel==0.132.0

python -m app.acquisition.cli config-check     # offline, nessuna chiave richiesta
python -m app.acquisition.cli route <source>   # metodo di acquisizione per fonte
python -m app.acquisition.cli report           # legge lo store delle osservazioni
python -m app.acquisition.cli kernel-smoke --url URL   # richiede KERNEL_API_KEY
```

`config-check` e `route` non toccano la rete; `kernel-smoke` fallisce con
`configuration_missing` senza chiave.

## 5. Fonti e contratto dati

`app/acquisition/contracts.py` — `Observation`: fonte, URL, `retrieved_at` UTC,
periodo, territorio/codice e livello, indicatore, valore/unità, definizione e
versione metodologica, artefatto originale + SHA-256, versione parser, run e
qualità. `published_at` (fonte) e `retrieved_at` (nostro fetch) sono campi
distinti. `series` e `measure_type` tengono separate compravendite/NTN/annunci e
asking/OMI/transato.

`app/acquisition/store.py` — `ObservationStore`: JSONL append-only, idempotente e
con revisioni conservate; rifiuta `synthetic_demo` nel flusso reale. I dati
sintetici restano solo nei fixture/endpoint marcati demo.

## 6. Stato live (28 ottobre 2026)

- **Non eseguito**: smoke test Kernel e pilota reale. `KERNEL_API_KEY` è vuota nel
  template (la chiave va impostata localmente in `.env`) e l'host ISPAT
  `www.statistica.provincia.tn.it` non risponde da questa macchina (timeout); il
  portale reale è `statweb.provincia.tn.it/annuario/`, con navigazione ASP.NET
  `__doPostBack`.
- **Correzione fonte**: nell'annuario corrente il capitolo V è «Giustizia»; i dati
  immobiliari stanno nel cap. IX «Economia». Il riferimento storico a «V.07
  Compravendite immobiliari e mutui» è superato e il suo id/export resta da
  confermare live. I.26 «Numero di famiglie per comunità di valle» copre
  1990–2024, quindi ΔH/Δ²H sarebbero calcolabili al più fino al 2024, con
  2025–2026 fuori copertura (non interpolati).
