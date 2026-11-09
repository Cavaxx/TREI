"""Self-contained real-data dashboard served at ``/real`` (home redirects here).

The table loads the available listings immediately, with four user-facing
orderings (price, surface, zone, convenience) in both directions, filters
kept across pages, and exports that mirror filters + ordering for ALL rows.
The synthetic demo stays confined to ``/demo``.
"""

REAL_DASHBOARD_HTML = """<!doctype html>
<html lang="it"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>TREI — annunci reali in provincia di Trento</title>
<style>
body{font-family:system-ui,sans-serif;margin:1.5rem;max-width:1200px;color:#222}
h1,h2{font-weight:600}
.banner{background:#e7f3ff;border:1px solid #9ec5fe;padding:.6rem 1rem;border-radius:6px;font-size:.9rem}
.err{background:#fdecea;border:1px solid #f5c6cb;padding:.6rem 1rem;border-radius:6px}
.warn{background:#fff3cd;border:1px solid #ffca2c;padding:.6rem 1rem;border-radius:6px}
table{border-collapse:collapse;width:100%;font-size:.85rem}
th,td{border:1px solid #ddd;padding:.35rem .5rem;text-align:left}
th{background:#f5f5f5}
.mono{font-family:ui-monospace,monospace;font-size:.8rem}
.filters{display:flex;flex-wrap:wrap;gap:.5rem;margin:.8rem 0;align-items:end}
.filters label{display:flex;flex-direction:column;font-size:.8rem}
.filters input,select{padding:.25rem;border:1px solid #ccc;border-radius:4px}
button{padding:.3rem .8rem;border:1px solid #333;border-radius:4px;background:#fff;cursor:pointer}
a{color:#06c}
.pager{display:flex;gap:.6rem;align-items:center;margin:.5rem 0;font-size:.9rem}
.muted{color:#666;font-size:.85rem}
.pos{color:#0a7d33;font-weight:600}.neg{color:#b02a37;font-weight:600}
</style></head>
<body>
<h1>Trentino Real Estate Intelligence — annunci reali</h1>
<p class="banner">Archivio di annunci <b>osservati</b> in provincia di Trento (Tecnocasa, Gabetti, Subito, CaseTrentine). Prezzi <b>richiesti</b>, non prezzi di vendita. I dati mancanti non sono zero. Nessun dato sintetico; la demo è su <a href="/demo">/demo</a>.</p>
<div id="archive-state"></div>

<div class="filters">
 <label>Comune <input id="f-comune" placeholder="es. Trento"></label>
 <label>Zona/frazione <input id="f-zone" placeholder="es. Centro storico"></label>
 <label>Fonte <select id="f-source"><option value="">tutte</option><option>tecnocasa_it</option><option>gabetti_it</option><option>subito_it</option><option>casetrentine_it</option></select></label>
 <label>Disponibilità <select id="f-status"><option value="">tutte</option><option value="available">disponibili</option><option value="to_reconfirm">da riconfermare</option><option value="confirmed_unavailable">non più disponibili</option><option value="uncertain">incerte</option></select></label>
 <label>Prezzo min <input id="f-pmin" type="number"></label>
 <label>Prezzo max <input id="f-pmax" type="number"></label>
 <label>Sup. min m² <input id="f-smin" type="number"></label>
 <label>Sup. max m² <input id="f-smax" type="number"></label>
 <label>Ordina per <select id="f-sort">
   <option value="value_score">Qualità dell'affare</option>
   <option value="price">Prezzo</option>
   <option value="surface">Superficie</option>
   <option value="zone">Zona</option>
   <option value="observed_at">Più recenti</option>
   <option value="price_m2">€/m²</option>
 </select></label>
 <label>Verso <select id="f-dir"><option value="desc">decrescente</option><option value="asc">crescente</option></select></label>
 <label>Per pagina <select id="f-limit"><option>25</option><option>50</option><option>100</option></select></label>
 <button onclick="apply()">Applica</button>
 <a id="x-csv" href="#">Esporta CSV</a> · <a id="x-json" href="#">Esporta JSON</a>
</div>
<p id="page-info" class="muted"></p>
<div class="pager" id="pager-top"></div>
<div id="listings"></div>
<div class="pager" id="pager-bottom"></div>
<div class="muted" id="legend"></div>

<script>
const state={page:0,sort:'value_score',direction:'desc',limit:25};
async function j(url){try{const r=await fetch(url);if(!r.ok)return {error:r.status};return await r.json();}catch(e){return {error:String(e)};}}
function esc(s){return String(s==null?'':s).replace(/[&<>]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;'}[c]));}
const g=id=>document.getElementById(id);

function params(){
  const p=new URLSearchParams();
  const set=(id,k)=>{const v=g(id).value;if(v)p.set(k,v);};
  set('f-comune','comune');set('f-zone','zone');set('f-source','source');set('f-status','availability');
  set('f-pmin','price_min');set('f-pmax','price_max');set('f-smin','surface_min');set('f-smax','surface_max');
  p.set('sort',state.sort);p.set('direction',state.direction);
  p.set('limit',state.limit);p.set('offset',state.page*state.limit);
  return p;
}
function exportHref(fmt){const p=params();p.delete('limit');p.delete('offset');return '/api/v1/real/export?'+p.toString()+'&format='+fmt;}

async function load(){
  g('x-csv').href=exportHref('csv');g('x-json').href=exportHref('json');
  const p=params();
  const d=await j('/api/v1/real/listings?'+p.toString());
  const el=g('listings');
  if(d.error){el.innerHTML='<p class="err">Errore API: '+esc(d.error)+'</p>';g('page-info').textContent='';renderPager(0,0);return;}
  if(!d.count){el.innerHTML='<p class="warn">Nessun annuncio per questi filtri (archivio presente).</p>';g('page-info').textContent='';renderPager(0,0);return;}
  g('page-info').textContent='Totale '+d.count+' annunci · mostrati '+d.from+'–'+d.to+' · ordinati per '+d.sort+' ('+d.direction+')';
  let h='<table><tr><th>Comune · zona</th><th>Titolo</th><th>Prezzo</th><th>m²</th><th>€/m²</th><th>Qualità affare</th><th>Fonte</th><th></th></tr>';
  for(const r of d.items){
    const v=r.vantage_pct;
    let conv='—';
    if(v!=null){conv='<span class="'+(v>=0?'pos':'neg')+'">'+(v>0?'+':'')+v.toFixed(1)+'%</span> <span class="muted">(n='+esc(r.comparables)+', '+esc(r.comparison_level)+')</span>';}
    else if(r.comparison_reason){conv='— <span class="muted" title="'+esc(r.comparison_reason)+'">'+esc(r.comparison_reason)+'</span>';}
    const loc=(r.comune||'—')+(r.zone?' · '+r.zone:'');
    const link='<a href="/real/'+esc(r.native_id)+'">scheda</a> · <a href="'+esc(r.url)+'" target="_blank" rel="noopener">originale</a>';
    h+='<tr><td>'+esc(loc)+'</td><td>'+esc(r.title)+'</td><td>'+(r.price!=null?esc(r.price)+' €':(r.price_qualifier?esc(r.price_qualifier):'—'))+'</td><td>'+(r.surface_m2!=null?esc(r.surface_m2):'—')+'</td><td>'+(r.price_m2!=null?esc(r.price_m2):'—')+'</td><td>'+conv+'</td><td>'+esc(r.source)+'</td><td>'+link+'</td></tr>';
  }
  h+='</table>';
  el.innerHTML=h;
  renderPager(d.count, d.offset);
}
function renderPager(total, offset){
  const pages=Math.max(1,Math.ceil(total/state.limit));
  const cur=Math.floor(offset/state.limit);
  const html='<button '+(cur<=0?'disabled':'')+' onclick="goto('+(cur-1)+')">← indietro</button>'+
    '<span>pagina '+(cur+1)+' di '+pages+'</span>'+
    '<button '+(cur>=pages-1?'disabled':'')+' onclick="goto('+(cur+1)+')">avanti →</button>';
  g('pager-top').innerHTML=html;g('pager-bottom').innerHTML=html;
  g('legend').innerHTML='Qualità dell\'affare = 100 × (1 − €/m² annuncio ÷ mediana €/m² dei comparabili). Comparabili: disponibili, residenziali, piena proprietà, stessa tipologia, superficie ±20%, minimo 10; livello zona, altrimenti comune. Aste, quote, multiproprietà e nuda proprietà restano consultabili ma fuori dal confronto standard.';
}
function goto(p){state.page=Math.max(0,p);load();}
function apply(){state.page=0;state.sort=g('f-sort').value;state.direction=g('f-dir').value;state.limit=parseInt(g('f-limit').value,10);load();}

async function health(){
  const h=await j('/api/v1/real/health');
  const el=g('archive-state');
  if(!h||h.error){el.innerHTML='<p class="err">API non raggiungibile.</p>';return;}
  if(h.status==='ok'){el.innerHTML='<p class="muted">Archivio: '+h.listings_registered+' annunci registrati · '+h.observations_active+' osservazioni statistiche attive.</p>';return;}
  if(h.status==='missing_dir'||h.status==='missing_files'){
    el.innerHTML='<p class="warn">Archivio assente: '+(h.message||'')+' — in docker compose il servizio api deve montare <code>../data:/app/data:ro</code> (la directory usata dal job cron); i dati restano fuori da Git e dall\'immagine.</p>';
    return;
  }
  if(h.status==='unreadable'){el.innerHTML='<p class="err">Archivio illeggibile: '+(h.message||'')+'</p>';return;}
  if(h.status==='empty'){el.innerHTML='<p class="warn">Archivio valido ma vuoto: eseguire il job di acquisizione (<code>python -m app.scheduler.daily run</code>).</p>';}
}
health();load();
</script></body></html>
"""

REAL_DETAIL_HTML = """<!doctype html>
<html lang="it"><head><meta charset="utf-8"><title>TREI — scheda annuncio</title>
<style>
body{font-family:system-ui,sans-serif;margin:1.5rem;max-width:900px;color:#222}
table{border-collapse:collapse;width:100%;font-size:.85rem}
th,td{border:1px solid #ddd;padding:.35rem .5rem;text-align:left}
th{background:#f5f5f5;width:40%}
.mono{font-family:ui-monospace,monospace;font-size:.8rem}
a{color:#06c}
.muted{color:#666;font-size:.85rem}
.pos{color:#0a7d33;font-weight:600}.neg{color:#b02a37;font-weight:600}
.sec{margin-top:1.5rem;border-top:1px solid #eee;padding-top:1rem}
</style></head>
<body>
<a href="/real">&larr; elenco</a>
<h1>Annuncio <span class="mono">__NATIVE_ID__</span></h1>
<div id="detail"></div>
<div class="sec" id="convenienza"></div>
<script>
async function load(){
  const r=await fetch('/api/v1/real/listings/__NATIVE_ID__');
  if(!r.ok){document.getElementById('detail').innerHTML='<p>Annuncio non trovato.</p>';return;}
  const d=await r.json();
  const c=d.current;
  const esc=s=>String(s==null?'':s).replace(/[&<>]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;'}[c]));
  const rows=[['Titolo',c.title],['Tipologia',c.typology],['Contratto',c.contract],['Offerta',c.offer_kind||'piena proprietà (nessuna etichetta speciale)'],['Comune',c.comune+' ('+c.province+')'],['Zona',c.zone||'non specificata'],['Indirizzo',c.address],['Prezzo richiesto',(c.price!=null?c.price+' €':'non disponibile')+(c.price_qualifier?' ('+c.price_qualifier+')':'')],['Prezzo precedente (dichiarato)',c.previous_price!=null?c.previous_price+' €':'non disponibile'],['Ribasso (dichiarato)',c.discount||'non disponibile'],['Superficie',c.surface_m2!=null?c.surface_m2+' m² ('+c.surface_definition+')':'non disponibile'],['€/m²',c.price_m2!=null?c.price_m2:'non calcolabile'],['Locali',c.rooms!=null?c.rooms:'non specificato'],['Camere',c.bedrooms!=null?c.bedrooms:'non specificato'],['Bagni',c.bathrooms!=null?c.bathrooms:'non specificato'],['Piano',c.floor||'non specificato'],['Ascensore',c.elevator||'non specificato'],['Classe energetica',c.energy_class||'non specificata'],['Inserzionista',c.advertiser||'non specificato'],['Data pubblicazione (fonte)',c.published_at||'non specificata'],['Prima osservazione',c.first_seen_at],['Ultima osservazione',c.last_seen_at],['Stato disponibilità',c.status||'—'],['Fonte',c.source],['URL originale',c.url]];
  let h='<table>';
  for(const [k,v] of rows){h+='<tr><th>'+esc(k)+'</th><td>'+(k==='URL originale'?'<a href="'+esc(v)+'" target="_blank" rel="noopener">'+esc(v)+'</a>':esc(v))+'</td></tr>';}
  h+='</table>';
  h+='<h2>Storico osservazioni ('+d.snapshots+')</h2><p class="muted">'+d.history.map(x=>x.observed_at+' → '+(x.price!=null?x.price+' €':'prezzo non indicato')).join('<br>')+'</p>';
  h+='<p class="muted">Nota: prezzo precedente/ribasso sono dichiarati dalla fonte alla data di acquisizione, non variazioni osservate dal crawler.</p>';
  document.getElementById('detail').innerHTML=h;
}
async function analysis(){
  const el=document.getElementById('convenienza');
  el.innerHTML='<p class="muted">Calcolo della convenienza…</p>';
  const r=await fetch('/api/v1/real/listings/__NATIVE_ID__/analysis');
  if(!r.ok){el.innerHTML='<p class="muted">Convenienza non calcolabile (annuncio non trovato).</p>';return;}
  const a=await r.json();
  const esc=s=>String(s==null?'':s).replace(/[&<>]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;'}[c]));
  const c=a.convenience||{};
  let h='<h2>Qualità dell\'affare (convenienza del prezzo richiesto)</h2>';
  if(a.offer_kind){h+='<p class="muted">Offerta con etichetta speciale ('+esc(a.offer_kind)+'): consultabile ma fuori dal confronto standard della piena proprietà.</p>';}
  if(c.vantage_pct==null){
    h+='<p>Valore: <b>nullo</b> — '+esc(c.reason||'campione insufficiente')+'</p>';
  }else{
    h+='<p>€/m² annuncio: <b>'+a.price_m2+'</b> · mediana comparabili: <b>'+esc(c.median_eur_m2)+' €/m²</b> ('+c.comparables+' comparabili, livello '+esc(c.level)+', fascia '+esc(c.band)+')</p>';
    h+='<p>vantaggio_percentuale = 100 × (1 − '+a.price_m2+' ÷ '+esc(c.median_eur_m2)+') = <span class="'+(c.vantage_pct>=0?'pos':'neg')+'">'+(c.vantage_pct>0?'+':'')+c.vantage_pct+'%</span> '+(c.vantage_pct>=0?'sotto':'sopra')+' la mediana dei prezzi richiesti.</p>';
  }
  const ctx=a.context||{};
  const fmt=(k,label)=>{const v=ctx[k]||{};return '<tr><th>'+esc(label)+'</th><td>'+(v.available===false?'<span class="muted">dato non disponibile</span>':(v.value!=null?esc(v.value):(v.count!=null?'n='+esc(v.count):'—'))+(v.last_period?' · ultimo periodo '+esc(v.last_period):'')+(v.note?'<div class="muted">'+esc(v.note)+'</div>':''))+'</td></tr>';};
  h+='<h2>Parametri TREI di contesto (non entrano nel calcolo)</h2><table>';
  h+=fmt('offer_depth','Profondità offerta osservata (fonte)');
  h+=fmt('permanence_days','Permanenza (giorni dalla prima osservazione)');
  h+='<tr><th>Ribassi verificati</th><td>'+(ctx.verified_price_cuts?esc(ctx.verified_price_cuts.count)+' riduzioni osservate'+(ctx.verified_price_cuts.note?'<div class="muted">'+esc(ctx.verified_price_cuts.note)+'</div>':''):'—')+'</td></tr>';
  h+=fmt('households','Famiglie (comunità di valle, ISPAT)');
  h+=fmt('compravendite_residenziali','Compravendite residenziali (provincia, ISPAT V.07)');
  h+=fmt('mortgage_rates','Tassi mutui nuove operazioni (Italia, BCE)');
  h+=fmt('inflation','Inflazione IPCA/HICP (Italia, Eurostat)');
  h+='</table>';
  h+='<p class="muted">'+esc(a.note||'')+'</p>';
  el.innerHTML=h;
}
load();analysis();
</script></body></html>
"""
