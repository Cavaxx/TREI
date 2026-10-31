"""Self-contained real-data dashboard served at ``/real``."""

REAL_DASHBOARD_HTML = """<!doctype html>
<html lang="it"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>TREI — dati reali</title>
<style>
body{font-family:system-ui,sans-serif;margin:1.5rem;max-width:1100px;color:#222}
h1,h2{font-weight:600}
.banner{background:#e7f3ff;border:1px solid #9ec5fe;padding:.6rem 1rem;border-radius:6px;font-size:.9rem}
table{border-collapse:collapse;width:100%;font-size:.85rem}
th,td{border:1px solid #ddd;padding:.35rem .5rem;text-align:left}
th{background:#f5f5f5}
.mono{font-family:ui-monospace,monospace;font-size:.8rem}
.filters{display:flex;flex-wrap:wrap;gap:.5rem;margin:.8rem 0;align-items:end}
.filters label{display:flex;flex-direction:column;font-size:.8rem}
.filters input,select{padding:.25rem;border:1px solid #ccc;border-radius:4px}
button{padding:.3rem .8rem;border:1px solid #333;border-radius:4px;background:#fff;cursor:pointer}
.sec{margin-top:2rem;border-top:1px solid #eee;padding-top:1rem}
.empty{color:#888;font-style:italic}
.muted{color:#666;font-size:.85rem}
</style></head>
<body>
<h1>Trentino Real Estate Intelligence — dati reali</h1>
<p class="banner">Campione <b>osservato</b> di annunci e serie statistiche acquisite. Non è il mercato complessivo; i dati mancanti non sono zero. Nessun dato sintetico.</p>

<div class="sec"><h2>Annunci</h2>
<div class="filters">
 <label>Comune <input id="f-comune" placeholder="es. Arco"></label>
 <label>Prezzo min <input id="f-pmin" type="number"></label>
 <label>Prezzo max <input id="f-pmax" type="number"></label>
 <label>Superficie min <input id="f-smin" type="number"></label>
 <label>Superficie max <input id="f-smax" type="number"></label>
 <label>Fonte <select id="f-source"><option value="">tutte</option></select></label>
 <label>Disponibilità <select id="f-status"><option value="">tutte</option><option value="available">disponibili</option><option value="to_reconfirm">da riconfermare</option><option value="confirmed_unavailable">non più disponibili</option><option value="uncertain">incerte</option></select></label>
 <button onclick="loadListings()">Applica</button>
 <a href="/api/v1/real/export?format=csv"><button type="button">Esporta CSV</button></a>
 <a href="/api/v1/real/export?format=json"><button type="button">Esporta JSON</button></a>
</div>
<div id="listings"></div>
</div>

<div class="sec"><h2>Statistiche</h2><div id="stats"></div></div>
<div class="sec"><h2>Copertura</h2><div id="coverage"></div></div>
<div class="sec"><h2>Ultimo run per fonte</h2><div id="runs"></div></div>
<div class="sec"><h2>€/m² (mediana campione, solo disponibili residenziali)</h2><div id="prices"></div></div>

<script>
async function j(url){const r=await fetch(url);return r.ok?r.json():null;}
function esc(s){return String(s==null?'':s).replace(/[&<>]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;'}[c]));}

async function loadListings(){
  const p=new URLSearchParams();
  const g=id=>document.getElementById(id).value;
  if(g('f-comune'))p.set('comune',g('f-comune'));
  if(g('f-pmin'))p.set('price_min',g('f-pmin'));
  if(g('f-pmax'))p.set('price_max',g('f-pmax'));
  if(g('f-smin'))p.set('surface_min',g('f-smin'));
  if(g('f-smax'))p.set('surface_max',g('f-smax'));
  if(g('f-source'))p.set('source',g('f-source'));
  if(g('f-status'))p.set('availability',g('f-status'));
  p.set('limit','200');
  const d=await j('/api/v1/real/listings?'+p.toString());
  const el=document.getElementById('listings');
  if(!d||!d.items||!d.items.length){el.innerHTML='<p class="empty">Nessun annuncio per questi filtri.</p>';return;}
  let h='<p class="muted">'+d.count+' annunci</p><table><tr><th>Comune</th><th>Titolo</th><th>Prezzo</th><th>€/m²</th><th>m²</th><th>Tipologia</th><th>Fonte</th><th>Stato</th><th>Ultima oss.</th><th></th></tr>';
  for(const r of d.items){
    h+='<tr><td>'+esc(r.comune)+'</td><td>'+esc(r.title)+'</td><td>'+esc(r.price)+' €'+(r.price_qualifier?' ('+esc(r.price_qualifier)+')':'')+'</td><td>'+(r.price_m2!=null?esc(r.price_m2):'—')+'</td><td>'+esc(r.surface_m2)+'</td><td>'+esc(r.typology)+'</td><td>'+esc(r.source)+'</td><td>'+esc(r.status)+'</td><td class="mono">'+esc(r.observed_at)+'</td><td><a href="/real/'+esc(r.native_id)+'">scheda</a></td></tr>';
  }
  h+='</table>';
  el.innerHTML=h;
}

async function loadStats(){
  const d=await j('/api/v1/real/statistics');
  const el=document.getElementById('stats');
  if(!d||!d.indicators.length){el.innerHTML='<p class="empty">Nessuna serie statistica.</p>';return;}
  let h='';
  for(const ind of d.indicators){
    h+='<h3>'+esc(ind.indicator)+' <span class="muted">('+esc(ind.definition)+', '+esc(ind.unit)+', '+esc(ind.source)+')</span></h3>';
    h+='<p class="mono">'+ind.periods.map(p=>p.period+'='+p.value).join('  ')+'</p>';
  }
  el.innerHTML=h;
}

async function loadCoverage(){
  const d=await j('/api/v1/real/coverage');
  const el=document.getElementById('coverage');
  if(!d){el.innerHTML='<p class="empty">Non disponibile.</p>';return;}
  el.innerHTML='<p>Annunci registrati: <b>'+d.listings_registered+'</b> · disponibili: <b>'+d.listings_available+'</b> · osservazioni statistiche attive: <b>'+d.statistical_observations+'</b></p>'
   +'<p class="muted">Per stato: '+JSON.stringify(d.status_breakdown)+'</p>'
   +'<p class="muted">Disponibili per fonte: '+JSON.stringify(d.available_by_source)+'</p>'
   +'<p class="muted">Disponibili per classe: '+JSON.stringify(d.available_by_unit_class)+'</p>'
   +'<p class="muted">Indicatori statistici: '+d.statistical_indicators.join(', ')+'</p>';
}

async function loadRuns(){
  const d=await j('/api/v1/real/runs');
  const el=document.getElementById('runs');
  if(!d||!d.runs.length){el.innerHTML='<p class="empty">Nessun run registrato.</p>';return;}
  let h='<table><tr><th>Fonte</th><th>Ultimo run</th><th>Stato</th><th>Scoperti</th><th>Tentati</th><th>Nuovi</th><th>Rivisitati</th><th>Coda</th></tr>';
  for(const r of d.runs){
    const o=r.outcomes||{}; const q=r.queue||{};
    h+='<tr><td>'+esc(r.source)+'</td><td class="mono">'+esc(r.started_at)+'</td><td>'+esc(r.status)+(r.inferred?' (ricostruito)':'')+'</td><td>'+esc(r.discovered)+'</td><td>'+esc(r.attempted)+'</td><td>'+esc(o.valid_new||0)+'</td><td>'+esc(o.revisit||0)+'</td><td>'+esc(JSON.stringify(q))+'</td></tr>';
  }
  h+='</table>';
  el.innerHTML=h;
}

async function loadPrices(){
  const d=await j('/api/v1/real/prices');
  const el=document.getElementById('prices');
  if(!d||!d.zones.length){el.innerHTML='<p class="empty">Dati €/m² non calcolabili (prezzo o superficie mancanti).</p>';return;}
  let h='<table><tr><th>Comune</th><th>n</th><th>mediana €/m²</th><th>min</th><th>max</th></tr>';
  for(const z of d.zones){h+='<tr><td>'+esc(z.comune)+'</td><td>'+z.n+'</td><td>'+z.median_eur_m2+'</td><td>'+z.min+'</td><td>'+z.max+'</td></tr>';}
  h+='</table><p class="muted">'+esc(d.note)+'</p>';
  el.innerHTML=h;
}

loadListings();loadStats();loadCoverage();loadRuns();loadPrices();
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
</style></head>
<body>
<a href="/real">&larr; elenco</a>
<h1>Annuncio <span class="mono">__NATIVE_ID__</span></h1>
<div id="detail"></div>
<script>
async function load(){
  const r=await fetch('/api/v1/real/listings/__NATIVE_ID__');
  if(!r.ok){document.getElementById('detail').innerHTML='<p>Annuncio non trovato.</p>';return;}
  const d=await r.json();
  const c=d.current;
  const esc=s=>String(s==null?'':s).replace(/[&<>]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;'}[c]));
  const rows=[['Titolo',c.title],['Tipologia',c.typology],['Contratto',c.contract],['Comune',c.comune+' ('+c.province+')'],['Indirizzo',c.address],['Prezzo richiesto',(c.price!=null?c.price+' €':'non disponibile')+(c.price_qualifier?' ('+c.price_qualifier+')':'')],['Prezzo precedente (dichiarato)',c.previous_price!=null?c.previous_price+' €':'non disponibile'],['Ribasso (dichiarato)',c.discount||'non disponibile'],['Superficie',c.surface_m2!=null?c.surface_m2+' m² ('+c.surface_definition+')':'non disponibile'],['€/m²',c.price_m2!=null?c.price_m2:'non calcolabile'],['Locali',c.rooms!=null?c.rooms:'non specificato'],['Camere',c.bedrooms!=null?c.bedrooms:'non specificato'],['Bagni',c.bathrooms!=null?c.bathrooms:'non specificato'],['Piano',c.floor||'non specificato'],['Ascensore',c.elevator||'non specificato'],['Classe energetica',c.energy_class||'non specificata'],['Data pubblicazione (fonte)',c.published_at||'non specificata'],['Prima osservazione',c.first_seen_at],['Ultima osservazione',c.last_seen_at],['Fonte',c.source],['URL',c.url]];
  let h='<table>';
  for(const [k,v] of rows){h+='<tr><th>'+esc(k)+'</th><td>'+(k==='URL'?'<a href="'+esc(v)+'" target="_blank" rel="noopener">'+esc(v)+'</a>':esc(v))+'</td></tr>';}
  h+='</table>';
  h+='<h2>Storico osservazioni ('+d.snapshots+')</h2><p class="muted">'+d.history.map(x=>x.observed_at+' → '+x.price+' €').join('<br>')+'</p>';
  h+='<p class="muted">Nota: prezzo precedente/ribasso sono dichiarati dalla fonte alla data di acquisizione, non variazioni osservate dal crawler.</p>';
  document.getElementById('detail').innerHTML=h;
}
load();
</script></body></html>
"""
