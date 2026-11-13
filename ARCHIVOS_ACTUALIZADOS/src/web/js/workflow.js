'use strict';
let state,page='home',cursor=0,preview=[],busy=false,focusKey=null;
let reviewMode='pending',reviewKeys=[];
const historyOpen=new Set();
let historyFilter='pending';
const $=id=>document.getElementById(id),esc=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const labels={manual:'Carga manual en Zeta',ready:'Listas para cargar',needs_review:'Necesitan una decisión',existing:'Ya existentes en Zeta',loaded:'Cargadas',skipped:'Salteadas',non_invoice:'Documentos que no son facturas'};
async function api(path,data){const r=await fetch('/api/'+path,{method:data?'POST':'GET',headers:data?{'Content-Type':'application/json','X-Cerecito-Token':state.token}:{},body:data?JSON.stringify(data):undefined});const v=await r.json();if(!r.ok)throw Error(v.error);return v;}
const drafts=new Map();
const batchRevision=batch=>batch?.reset_at||batch?.date;
function saveDraft(){if(page!=='review'||!focusKey||document.querySelector('[data-invoice-key]')?.dataset.invoiceKey!==focusKey)return;const values={__scrollTop:document.querySelector('.invoice-scroll')?.scrollTop||0};document.querySelectorAll('#content input,#sectorMapping select').forEach(e=>{const key=e.id||e.dataset.sectorVat;if(key)values[key]=e.value;});drafts.set(focusKey,values);}
function restoreDraft(){if(page!=='review'||!focusKey)return;for(const [key,value] of Object.entries(drafts.get(focusKey)||{})){const e=$(key)||document.querySelector('[data-sector-vat="'+key+'"]');if(!e)continue;e.value=value;if(e.id.startsWith('search'))e.oninput?.();}const scroll=document.querySelector('.invoice-scroll');if(scroll)scroll.scrollTop=drafts.get(focusKey)?.__scrollTop||0;}
async function refresh(render=true){saveDraft();const previousBatch=batchRevision(state?.settings.batch);state=await api('state');syncResetButton();if(previousBatch!==batchRevision(state.settings.batch)){drafts.clear();focusKey=null;cursor=0;reviewKeys=[];}if(render||(page==='review'&&$('precheckProgress'))){draw();restoreDraft();}}
async function action(fn){try{$('notice').textContent='';await fn();}catch(e){$('notice').textContent=e.message;$('notice').scrollIntoView({block:'center'});}}
function options(kind,value,empty='Elegir…'){return `<option value="">${empty}</option>`+state.catalog.filter(c=>c.kind===kind).map(c=>`<option value="${esc(c.code)}" ${c.code===value?'selected':''}>${esc(c.code)} · ${esc(c.name)}</option>`).join('');}
function supplierNeedsConfirmation(invoice,supplier){
  return !!supplier&&(!supplier.rut||invoice.supplier_confirmation_required===true)&&
    !invoice.payload.proveedor.seleccionManual&&state.settings.approvals?.[supplier.code]!==invoice.source.rut;
}
function amountLabel(source){
  const value=source.totales.pagar??source.totales.total;
  return source.moneda+' '+new Intl.NumberFormat('es-UY',{minimumFractionDigits:2,maximumFractionDigits:2}).format(value);
}
function updateNavigation(counts){
  const pending=(counts.needs_review||0)+state.invoices.filter(i=>i.state==='manual'&&!i.decision.reviewed).length;
  $('reviewCount').textContent=pending;
  $('readyCount').textContent=counts.ready||0;
  document.querySelectorAll('.toolbar button').forEach(button=>{
    const selected=button.id===({home:'summary',summary:'summary',results:'results',catalogs:'catalogs',review:reviewMode==='ready'?'readyReview':'review'}[page]);
    button.classList.toggle('nav-active',selected);
    if(selected)button.setAttribute('aria-current','page');else button.removeAttribute('aria-current');
  });
  const title=({home:'Tu lote de comprobantes',summary:'Resumen del lote',results:'Actividad e historial',catalogs:'Catálogos',review:reviewMode==='ready'?'Comprobantes listos':'Revisión de comprobantes'})[page];
  $('stages').textContent=title;
}
function invoiceList(title,invoices,{open=false}={}){
  if(!invoices.length)return '';
  return `<details class="invoice-list card" ${open?'open':''}><summary><span>${esc(title)}</span><span class="count-badge">${invoices.length}</span></summary>
    <div class="invoice-list-body">${invoices.map(i=>{
      const editable=['needs_review','ready','manual'].includes(i.state);
      const description=i.auto_collection?'Cobranza salteada automáticamente':i.finesa?i.source.dirRecep:labels[i.state];
      return `<div class="document-item"><div><strong>${esc(i.source.nombre)}</strong><small>${esc(i.source.serie)}/${esc(i.source.numero)} · ${esc(i.source.fecha)}${i.source.tipo==='112'?' · Nota de crédito':''}</small><small>${esc(description)}</small></div>
        <span class="document-amount">${esc(amountLabel(i.source))}</span>
        ${editable?`<button data-edit="${esc(i.key)}">${i.state==='ready'?'Ver comprobante':'Resolver'}</button>`:i.state==='skipped'&&!i.auto_collection?`<button data-restore="${esc(i.key)}">Volver a revisar</button>`:''}</div>`;
    }).join('')}</div></details>`;
}
function draw(){
  if(!state)return;
  const counts={};
  for(const invoice of state.invoices)counts[invoice.state]=(counts[invoice.state]||0)+1;
  updateNavigation(counts);
  document.querySelector('main').classList.toggle('reviewing',page==='review');
  if(page==='catalogs'){catalogPage();return;}
  if(page==='review'){review();return;}
  if(page==='results'){results();return;}
  const activeJob=state.jobs.find(j=>['queued','running','cancelling'].includes(j.status));
  const pending=state.invoices.filter(i=>i.state==='needs_review'||(i.state==='manual'&&!i.decision.reviewed));
  const ready=state.invoices.filter(i=>i.state==='ready');
  const batch=state.settings.batch;
  $('content').innerHTML=`<section class="card batch-overview"><div><p class="eyebrow">${batch?'LOTE ACTUAL':'COMENZAR'}</p><h2>${pending.length?'Continuá con los comprobantes pendientes':ready.length?'Tus comprobantes están preparados':'Todo en un solo lugar'}</h2>
    <p class="muted">${batch?`${pending.length} por revisar · ${ready.length} listos para cargar · ${(counts.loaded||0)+(counts.existing||0)} registrados en Zeta`:'Importá un ZIP o XML de FacturaLista para comenzar.'}</p></div>
    <div class="actions"><button id="reviewPending" ${!pending.length?'disabled':''}>Resolver pendientes</button><button class="primary" id="load" ${!ready.length||activeJob?'disabled':''}>${activeJob?'Trabajo en curso…':'Cargar comprobantes'}</button></div>
    <div class="batch-tools"><button id="checkExisting" ${!batch||activeJob?'disabled':''}>Buscar ya cargadas</button><button id="verify" ${!ready.length||activeJob?'disabled':''}>Verificar antes de cargar</button></div></section>`+
    invoiceList('Necesitan tu atención',pending,{open:true})+
    invoiceList('Listos para cargar · revisar si querés',ready)+
    invoiceList('Ya registrados en Zeta',state.invoices.filter(i=>['existing','loaded'].includes(i.state)))+
    invoiceList('Revisión manual completada',state.invoices.filter(i=>i.state==='manual'&&i.decision.reviewed))+
    invoiceList('Salteados',state.invoices.filter(i=>i.state==='skipped'))+
    invoiceList('Otros documentos',state.invoices.filter(i=>i.state==='non_invoice'));
  document.querySelectorAll('[data-edit]').forEach(button=>button.onclick=()=>openReview(state.invoices.find(i=>i.key===button.dataset.edit)?.state==='ready'?'ready':'pending',button.dataset.edit));
  document.querySelectorAll('[data-restore]').forEach(button=>button.onclick=()=>decide(button.dataset.restore,{skip:false,skipSupplier:false}));
  $('reviewPending').onclick=()=>openReview();
  $('checkExisting').onclick=precheck;
  $('verify').onclick=()=>start(false);
  $('load').onclick=()=>start(true);
}
function openReview(mode='pending',key=null){
  saveDraft();reviewMode=mode;reviewKeys=[];focusKey=key;cursor=0;page='review';draw();restoreDraft();
}
function previousReview(){
  const pending=reviewQueue();
  const position=pending.findIndex(i=>i.key===focusKey);
  if(position<=0)return;
  saveDraft();focusKey=pending[position-1].key;cursor=position-1;draw();restoreDraft();
}
async function decide(key,patch,advance=false){
  const next=advance?reviewQueue()[cursor+1]?.key:null;
  const controls=Array.from(document.querySelectorAll('#content button,#content select')).filter(e=>!e.disabled);
  controls.forEach(e=>e.disabled=true);
  await action(async()=>{
    await api('decision',{key,patch});
    if(advance){
      saveDraft();focusKey=next||null;
      if(!next){page='summary';reviewKeys=[];cursor=0;}
    }
    await refresh();
  });
  controls.filter(e=>e.isConnected).forEach(e=>e.disabled=false);
}
function review(){const pending=reviewQueue();if(!pending.length){page='summary';draw();return;}cursor=Math.min(cursor,pending.length-1);if(focusKey){const pos=pending.findIndex(i=>i.key===focusKey);if(pos>=0)cursor=pos;}const i=pending[cursor],s=i.source,d=i.decision;focusKey=i.key;$('content').innerHTML=`<section class="card" data-invoice-key="${esc(i.key)}"><div class="invoice-head"><div><p class="muted">Revisión ${cursor+1} de ${pending.length}</p><h2>${esc(s.nombre)}</h2><p>RUT ${esc(s.rut)} · ${esc(s.serie)}/${esc(s.numero)} · ${esc(s.fecha)}</p>${reviewPicker(pending,i)}</div><div><h2>${esc(s.moneda)} ${esc(s.totales.total)}</h2><span class="badge">${esc(i.state==='manual'?labels.manual:i.form)}</span></div></div><div class="warning">${i.reasons.map(esc).join('<br>')}</div><div class="fields"><label>Crédito / Contado<select id="mode" ${i.modeLocked?'disabled title="Crédito según el XML"':''}>${i.modePending?'<option value="" selected>Corroborar modalidad...</option>':''}<option value="credit" ${!i.modePending&&i.mode==='credit'?'selected':''}>Crédito</option><option value="cash" ${!i.modePending&&i.mode==='cash'?'selected':''}>Contado</option></select></label><label>Proveedor<select id="supplier">${options('supplier',d.supplier||i.payload.proveedor.codigoZeta)}</select></label><label>Centro de costos (opcional)<select id="center">${options('center',d.center??i.suggested_center,'Sin centro')}</select></label>${i.mode==='cash'?`<label>Forma de pago<select id="payment">${options('payment',d.payment||i.paymentCode,i.paymentDefault?'Efectivo por defecto':'Elegir...')}</select></label>`:''}</div><div id="identity"></div>${s.lineas.map((l,n)=>lineHTML(i,l,n)).join('')}<div class="actions"><button id="previous" ${cursor===0?'disabled':''}>Anterior</button><button id="skip">Saltear</button><button class="primary" id="confirm">${i.state==='manual'?'Dejar para carga manual y seguir':'Confirmar y siguiente'}</button></div><p class="muted">Cada cambio se guarda inmediatamente.</p></section>`;const picker=$('reviewInvoice');if(picker)picker.onchange=()=>{saveDraft();focusKey=picker.value;draw();restoreDraft();};for(const field of ['mode','supplier','center','payment'])if($(field))$(field).onchange=()=>decide(i.key,{[field]:$(field).value});const supplier=state.catalog.find(c=>c.kind==='supplier'&&c.code===(d.supplier||i.payload.proveedor.codigoZeta));if(supplierNeedsConfirmation(i,supplier)){$('identity').innerHTML=`<div class="warning">Zeta: ${esc(supplier.code)} · ${esc(supplier.name)} · RUT vacío<br>XML: ${esc(s.nombre)} · RUT ${esc(s.rut)}<p><button id="approve">Confirmar relación</button></p></div>`;$('approve').onclick=()=>action(async()=>{await api('supplier/confirm',{key:i.key,code:supplier.code});await refresh();});}for(let n=0;n<s.lineas.length;n++){const ignore=$('ignore'+n);if(ignore)ignore.onclick=()=>decide(i.key,{lines:{[n]:{ignore:!(d.lines?.[n]?.ignore===true||i.auto_ignored_lines?.includes(n))}}});const search=$('search'+n);if(!search)continue;search.oninput=()=>renderChoices(i,n,search.value,$('kind'+n).value);$('kind'+n).onchange=()=>renderChoices(i,n,search.value,$('kind'+n).value);renderChoices(i,n,'',$('kind'+n).value);if($('rule'+n))$('rule'+n).onclick=()=>action(async()=>{await api('rule',{key:i.key,index:n});$('notice').textContent='Regla confirmada para esta empresa, proveedor e IVA.';});}$('previous').onclick=previousReview;$('skip').onclick=()=>decide(i.key,{skip:true},true);$('confirm').onclick=()=>{if(i.state!=='manual'&&i.reasons.length){$('notice').textContent='Resolvé los campos indicados o salteá esta factura.';return;}decide(i.key,{reviewed:true},true);};}
function lineHTML(i,l,n){const choice=i.decision.lines?.[n]||i.proposals[n]?.rule,chosen=state.catalog.find(c=>c.kind===choice?.kind&&c.code===choice?.code);return `<div class="line"><h3>${n+1} · ${esc(l.descripcion)}</h3><p class="muted">Cantidad ${esc(l.cantidad)} · Importe ${esc(l.monto)} · IVA original ${esc(l.indicadorIVA)} · ${esc(i.proposals[n]?.reason)}</p>${chosen?`<p class="badge">Elegida: ${esc(chosen.code)} · ${esc(chosen.name)}</p>`:''}${choice?.ignore?'<p>Línea ignorada; el XML se conserva.</p>':''}<div class="fields"><label>Destino<select id="kind${n}"><option value="article">Artículo</option><option value="concept" ${choice?.kind==='concept'?'selected':''}>Gasto</option></select></label><label>Buscar código o descripción<input id="search${n}" placeholder="Escribí para buscar"></label></div><div class="choices" id="choices${n}"></div><p>${['6','7'].includes(l.indicadorIVA)?`<button id="ignore${n}">${choice?.ignore?'Volver a incluir':'Ignorar línea'}</button>`:''}${chosen?` <button id="rule${n}">Usar también como regla futura</button>`:''}</p></div>`;}
function supportsVat(destination,indicator){
  return (Array.isArray(destination?.vat)?destination.vat:String(destination?.vat||'').split('|')).includes(indicator);
}
function zetaVatOptions(selected=''){
  return [['','Según XML'],['1','Sin IVA (0%)'],['2','10%'],['3','22%']]
    .map(([value,label])=>`<option value="${value}" ${value===selected?'selected':''}>${label}</option>`).join('');
}
function renderChoices(i,n,query,kind){
  const raw=i.source.lineas[n];
  const searchable=['1','2','3'].includes(raw.indicadorIVA);
  const compatible=c=>supportsVat(c,raw.indicadorIVA);
  const describes=c=>c.name.toLowerCase().includes(raw.descripcion.toLowerCase());
  const matches=state.catalog.filter(c=>c.kind===kind&&(searchable||compatible(c))&&
    (!query||`${c.code} ${c.name} ${(c.synonyms||[]).join(' ')}`.toLowerCase().includes(query.toLowerCase())))
    .sort((a,b)=>Number(compatible(b))-Number(compatible(a))||Number(describes(b))-Number(describes(a))).slice(0,8);
  $('choices'+n).innerHTML=matches.map(c=>`<button data-code="${esc(c.code)}">${esc(c.code)} · ${esc(c.name)}</button>`).join('')||'<p>No hay coincidencias. Importá o actualizá el maestro.</p>';
  $('choices'+n).querySelectorAll('button').forEach(b=>b.onclick=()=>{
    const ivaZeta=$('lineVat'+n)?.value;
    setLine(i,n,{kind,code:b.dataset.code,...(ivaZeta?{ivaZeta}:{})});
  });
}
async function setLine(i,n,choice){
  const controls=Array.from(document.querySelectorAll('#content button,#content select')).filter(e=>!e.disabled);controls.forEach(e=>e.disabled=true);
  await action(async()=>{
    const destination=state.catalog.find(c=>c.kind===choice.kind&&c.code===choice.code);
    const learn=!!destination&&!choice.ivaZeta&&supportsVat(destination,i.source.lineas[n].indicadorIVA);
    await api('decision',{key:i.key,patch:{lines:{[n]:choice}},learn_lines:learn});
    await refresh();
    if(choice.kind&&choice.code)$('notice').textContent=learn&&!i.finesa?'Elección aprendida para próximas facturas.':'Elección guardada para este comprobante.';
  });
  controls.filter(e=>e.isConnected).forEach(e=>e.disabled=false);
}

// The precheck runs independently of line classification, before entering review.
let allowUnverifiedBatch=null,prechecking=false;
function precheckCandidates(){
  return state.invoices.filter(i=>i.can_precheck&&i.state!=='existing');
}
function reviewQueue(){
  const eligible=state.invoices.filter(i=>['needs_review','ready','manual'].includes(i.state));
  const desired=eligible.filter(i=>reviewMode==='ready'?i.state==='ready':
    i.state==='needs_review'||(i.state==='manual'&&!i.decision.reviewed));
  const byKey=new Map(eligible.map(i=>[i.key,i]));
  reviewKeys=reviewKeys.filter(key=>byKey.has(key));
  for(const invoice of desired)if(!reviewKeys.includes(invoice.key))reviewKeys.push(invoice.key);
  if(byKey.has(focusKey)&&!reviewKeys.includes(focusKey))reviewKeys.push(focusKey);
  return reviewKeys.map(key=>byKey.get(key));
}
function reviewPicker(pending,current){
  if(pending.length<2)return '';
  return `<label>Ir a comprobante<select id="reviewInvoice">${pending.map(i=>`
    <option value="${esc(i.key)}" ${i.key===current.key?'selected':''}>${esc(i.source.serie)}/${esc(i.source.numero)} · ${esc(i.source.nombre)}${i.state==='manual'?' · Carga manual':''}</option>`).join('')}</select></label>`;
}
async function precheck(){
  if(prechecking)return;
  prechecking=true;
  await action(async()=>{await api('jobs',{id:crypto.randomUUID(),precheck:true});page='review';focusKey=null;reviewKeys=[];await refresh();});
  prechecking=false;
}
function syncResetButton(){
  $('resetReview').disabled=busy||!state?.settings.batch?.keys?.length||
    state.jobs.some(job=>['queued','running','cancelling'].includes(job.status));
}
async function resetReview(){
  if($('resetReview').disabled)return;
  if(!window.confirm('Se reiniciarán las elecciones de este lote y se volverán a aplicar las correlaciones aprendidas. ¿Volver a revisar?'))return;
  busy=true;
  syncResetButton();
  try{
    await action(async()=>{
      await api('review/reset',{id:crypto.randomUUID(),revision:batchRevision(state.settings.batch)});
      page='review';
      focusKey=null;reviewKeys=[];reviewMode='pending';
      await refresh();
      if(page==='summary')$('notice').textContent='Elecciones del lote reiniciadas. Consultá el resumen.';
    });
  }finally{
    busy=false;
    syncResetButton();
  }
}
const invoiceReview=review;
review=function(){
  const batch=batchRevision(state.settings.batch);
  const candidates=precheckCandidates();
  const check=state.jobs.find(j=>j.data.precheck&&j.data.batch_date===batch);
  const active=check&&['queued','running','cancelling'].includes(check.status);
  const manualFocus=state.invoices.some(i=>i.key===focusKey&&i.state==='manual');
  if(!manualFocus&&(active||(candidates.length&&!check&&allowUnverifiedBatch!==batch))){
    $('content').innerHTML=`<section class="card" id="precheckProgress"><h2>Primero buscamos las facturas en Zeta</h2><p>Las que ya existen se apartan de la revisión. Esta consulta no guarda comprobantes.</p><progress max="${check?.data.total||1}" value="${check?.data.done||0}"></progress><p>${check?.data.done||0} de ${check?.data.total||candidates.length} comprobadas</p></section>`;
    if(!check)precheck();
    return;
  }
  if(state.invoices.some(i=>i.key===focusKey&&['existing','loaded'].includes(i.state)))focusKey=null;
  invoiceReview();
  const invoice=state.invoices.find(i=>i.key===focusKey);
  if(page!=='review'||!invoice||!$('mode'))return;
  if(invoice.duplicate_state!=='absent'&&(invoice.state!=='manual'||invoice.duplicate_state==='uncertain')){
    const warning=document.createElement('p');warning.className='warning';
    const relationConfirmed=invoice.last_result?.estado==='BLOQUEADO_CONFIRMACION_PROVEEDOR'&&
      (invoice.payload.proveedor.seleccionManual||state.settings.approvals?.[invoice.payload.proveedor.codigoZeta]===invoice.source.rut);
    warning.textContent=relationConfirmed?'Relación del proveedor confirmada. Se volverá a comprobar esta factura antes de cargar.':
      (invoice.last_result?.motivo||'Esta factura todavía no pudo comprobarse en Zeta. Podés preparar sus destinos; se volverá a buscar antes de guardar.');
    $('content').prepend(warning);
  }
  const panel=document.createElement('section');panel.className='card';panel.id='sectorPanel';
  panel.innerHTML=`<h3>Asignar toda la factura a un sector</h3><label>Sector<select id="invoiceSector"><option value="">Elegir sector…</option>${(state.sectors||[]).map(s=>`<option value="${esc(s.code||"__none__")}" ${s.code===(invoice.decision.center??invoice.suggested_center)?'selected':''}>${esc(s.name)}</option>`).join('')}</select></label><div id="sectorMapping"></div><p class="muted">Los destinos se asignan según el IVA original. Podés ajustar excepciones por línea después.</p>`;
  $('content').querySelector('.card .fields').before(panel);
  function mappings(){
    const sector=state.sectors.find(s=>(s.code||'__none__')===$('invoiceSector').value);
    if(!sector){$('sectorMapping').innerHTML='';return;}
    $('sectorMapping').innerHTML='<div class="fields">'+[['1','Exento'],['2','IVA 10%'],['3','IVA 22%']].map(([vat,label])=>{
      const selected=sector.mapping[vat];
      const options=state.catalog.filter(c=>c.kind===(invoice.decision.destinationKind||invoice.payload.lineas[0]?.tipoDestino||'article')&&(Array.isArray(c.vat)?c.vat:String(c.vat||'').split('|')).includes(vat));
      return `<label>${label}<select data-sector-vat="${vat}"><option value="">Dejar pendiente</option>${options.map(c=>`<option value="${esc(JSON.stringify({kind:c.kind,code:c.code}))}" ${selected?.kind===c.kind&&selected?.code===c.code?'selected':''}>${esc(c.code)} · ${esc(c.name)}</option>`).join('')}</select></label>`;
    }).join('')+'</div><button id="applySector" class="primary">Aplicar sector a toda la factura</button><p class="muted">Esta distribución queda disponible para las próximas facturas de este sector.</p>';
    $('applySector').onclick=()=>action(async()=>{
      const mapping={};document.querySelectorAll('[data-sector-vat]').forEach(el=>{if(el.value)mapping[el.dataset.sectorVat]=JSON.parse(el.value);});
      $('applySector').disabled=true;
      try{await api('sector',{key:invoice.key,center:sector.code,mapping});await refresh();}finally{if($('applySector'))$('applySector').disabled=false;}
    });
  }
  $('invoiceSector').onchange=()=>{
    mappings();
    const selected=state.sectors.find(s=>(s.code||'__none__')===$('invoiceSector').value);
    if(selected)action(async()=>{
      await api('decision',{key:invoice.key,patch:{center:selected.code}});
      // Guardar en la base SQLite sin perder los destinos que se estén eligiendo.
      invoice.decision.center=selected.code;
      if(invoice.finesa)await refresh();
      $('notice').textContent=invoice.finesa?'Sector guardado para este comprobante.':'Sector guardado para próximas facturas de este proveedor.';
    });
  };mappings();
  if(invoice.state==='manual'){
    const note=document.createElement('p');note.className='muted';
    note.textContent='Al continuar, este comprobante queda para cargar manualmente en Zeta. Cerecito guarda tu revisión.';
    $('content').querySelector('.card .actions').before(note);
  }
  invoiceLayout(invoice,panel);
};
async function start(save){if(busy)return;busy=true;const button=$(save?'load':'verify');if(button){button.disabled=true;button.textContent='Iniciando...';}await action(async()=>{const started=await api('jobs',{id:crypto.randomUUID(),save});if(save){historyFilter='pending';historyOpen.add(started.id);}page='results';focusKey=null;await refresh();$('content').scrollIntoView({block:'start'});});busy=false;if(page!=='results')draw();}
function resultCategory(item){
  if(['CONFIRMADO','RESULTADOS_EN_ZETA_NO_CARGAR'].includes(item.estado))return 'loaded';
  if(['EXCLUIDO','DOCUMENTO_NO_COMPRA'].includes(item.estado))return 'skipped';
  if(item.guardado_parcial||/INCIERTO|DETENIDO/.test(item.estado||''))return 'review';
  if(['APTO_PENDIENTE_GUARDADO','AUSENTE_VERIFICADO'].includes(item.estado))return 'ready';
  return (item.estado||'').startsWith('BLOQUEADO')?'not_loaded':'review';
}
function resultLabel(item){
  const category=resultCategory(item);
  if(category==='review')return item.guardado_parcial?'Revisar carga parcial':'Revisar en Zeta';
  return ({CONFIRMADO:'Cargada',RESULTADOS_EN_ZETA_NO_CARGAR:'Ya registrada',
    APTO_PENDIENTE_GUARDADO:'Lista para cargar',AUSENTE_VERIFICADO:'Pendiente de carga',
    EXCLUIDO:'Salteada',DOCUMENTO_NO_COMPRA:'Otro documento'})[item.estado]||'No cargada';
}
function latestHistoryResults(){
  const latest=new Map();
  for(const job of state.jobs){
    for(const item of [...(job.data.items||[])].reverse()){
      if(item.clave&&!latest.has(item.clave))latest.set(item.clave,item);
    }
  }
  return latest;
}
function unresolvedResult(item,latest){
  const invoice=state.invoices.find(invoice=>invoice.key===item.clave);
  if(invoice&&['existing','loaded','skipped','non_invoice'].includes(invoice.state))return false;
  const last=latest.get(item.clave);
  if(last&&last!==item)return false;
  return ['ready','not_loaded','review'].includes(resultCategory(item));
}
function historyMatches(item,latest){
  if(historyFilter==='all')return true;
  if(historyFilter==='pending')return unresolvedResult(item,latest);
  return resultCategory(item)===historyFilter;
}
function results(){
  const statusNames={queued:'En espera',running:'Procesando',cancelling:'Finalizando el comprobante actual',cancelled:'Cancelado',finished:'Finalizado',failed:'Requiere atención',interrupted:'Interrumpido'};
  const active=state.jobs.find(job=>['queued','running','cancelling'].includes(job.status));
  const title=job=>job.data.precheck?'Búsqueda en Zeta':job.data.save?'Carga de comprobantes':'Verificación';
  const latest=latestHistoryResults();
  const filters=[['pending','Pendientes'],['loaded','Cargadas'],['not_loaded','No cargadas'],['review','Revisar en Zeta'],['all','Todas']];
  const jobs=state.jobs.map(job=>({job,items:(job.data.items||[]).filter(item=>historyMatches(item,latest))}))
    .filter(({job,items})=>items.length||((job.data.error||['failed','interrupted'].includes(job.status)||(job.status==='cancelled'&&job.data.done<job.data.total))&&['pending','all'].includes(historyFilter)));
  $('content').innerHTML=(active?`<section class="card job-progress"><p class="eyebrow">EN CURSO</p><h2>${title(active)}</h2><progress max="${active.data.total||1}" value="${active.data.done}"></progress><p>${active.data.done} de ${active.data.total} comprobantes procesados</p><button data-cancel="${esc(active.id)}">Cancelar después de esta factura</button></section>`:'')+
    `<div class="history-filters" role="group" aria-label="Filtrar resultados">${filters.map(([value,label])=>`<button data-history-filter="${value}" aria-pressed="${historyFilter===value}" class="${historyFilter===value?'primary':''}">${label}</button>`).join('')}</div>`+
    (jobs.length?`<section class="history-list">${jobs.map(({job,items})=>{
      const pending=(job.data.items||[]).filter(item=>unresolvedResult(item,latest)).length;
      const loaded=(job.data.items||[]).filter(item=>resultCategory(item)==='loaded').length;
      return `<details class="card history-job" data-job="${esc(job.id)}" ${historyOpen.has(job.id)?'open':''}><summary><span><strong>${title(job)}</strong><small>${esc(new Date(job.created).toLocaleString('es-UY'))} · ${loaded} cargadas o existentes · ${pending} pendientes · ${items.length} visibles</small></span><span class="status-pill ${pending?'needs-attention':''}">${pending?pending+' pendientes':esc(statusNames[job.status]||job.status)}</span></summary>
        <div class="history-body">${job.data.error?`<p class="warning">${esc(job.data.error)}</p>`:''}${['cancelled','failed','interrupted'].includes(job.status)&&job.data.done<job.data.total?`<p class="warning">Quedaron ${job.data.total-job.data.done} comprobantes sin resultado registrado. Revisalos en el resumen del lote y en Zeta.</p>`:''}${items.map(item=>{
          const invoice=state.invoices.find(invoice=>invoice.key===item.clave);
          const editable=invoice&&['needs_review','ready','manual'].includes(invoice.state);
          return `<details class="result-item"><summary><span><strong>${esc(item.proveedor||invoice?.source.nombre||item.clave)}</strong><small>${item.numero?`${item.serie?esc(item.serie)+'/':''}${esc(item.numero)}`:invoice?esc(invoice.source.serie+'/'+invoice.source.numero):''}${item.docid?' · ID '+esc(item.docid):''}</small>${item.motivo?`<small class="result-reason">${esc(item.motivo)}</small>`:''}</span><span class="status-pill ${resultCategory(item)==='loaded'?'success':'needs-attention'}">${esc(resultLabel(item))}</span></summary>
            ${editable?`<button data-result-edit="${esc(invoice.key)}">Revisar comprobante</button>`:''}<details class="support-detail"><summary>Detalles para soporte</summary><pre>${esc(JSON.stringify(item,null,2))}</pre></details></details>`;
        }).join('')||'<p class="muted">El trabajo terminó antes de registrar el resultado de los comprobantes. Revisá el motivo indicado.</p>'}</div></details>`;
    }).join('')}</section>`:`<section class="card empty-state"><h2>${historyFilter==='pending'?'No hay comprobantes pendientes en el historial':'No hay resultados para este filtro'}</h2><p class="muted">${state.jobs.length?'Podés cambiar el filtro para consultar las otras cargas.':'Las búsquedas, verificaciones y cargas aparecerán acá.'}</p></section>`);
  document.querySelectorAll('[data-history-filter]').forEach(button=>button.onclick=()=>{historyFilter=button.dataset.historyFilter;results();});
  document.querySelectorAll('[data-job]').forEach(details=>details.ontoggle=()=>{
    if(!details.isConnected)return;
    if(details.open)historyOpen.add(details.dataset.job);else historyOpen.delete(details.dataset.job);
  });
  document.querySelectorAll('[data-result-edit]').forEach(button=>button.onclick=()=>openReview('pending',button.dataset.resultEdit));
  document.querySelectorAll('[data-cancel]').forEach(button=>button.onclick=()=>action(async()=>{await api('jobs/cancel',{id:button.dataset.cancel});await refresh();}));
}
async function fileData(file){return new Promise((resolve,reject)=>{const r=new FileReader();r.onload=()=>resolve({filename:file.name,content:r.result.split(',')[1]});r.onerror=reject;r.readAsDataURL(file);});}
$('import').onchange=()=>action(async()=>{if(!$('import').files[0])return;await api('import',await fileData($('import').files[0]));page='review';cursor=0;focusKey=null;reviewKeys=[];reviewMode='pending';drafts.clear();await refresh();$('import').value='';});$('review').onclick=()=>action(async()=>{await refresh(false);openReview();});$('readyReview').onclick=()=>action(async()=>{await refresh(false);openReview('ready');});$('resetReview').onclick=resetReview;$('summary').onclick=()=>action(async()=>{page='summary';await refresh();});$('results').onclick=()=>action(async()=>{page='results';await refresh();});
function setup(){const c=state.settings.company||{};for(const name of ['name','rut','tolerance'])if(c[name])$('setupForm').elements[name].value=c[name];for(const name of ['numero','serie','proveedor','nombre'])$('setupForm').elements[name].value=c.control?.[name]||'';$('dataPath').textContent='Carpeta de datos: '+state.data_dir;const folder=$('setupForm').elements.dataDirectory;folder.value=state.data_dir;folder.disabled=!!state.settings.company;$('setup').showModal();}
$('settings').onclick=setup;$('closeSetup').onclick=()=>$('setup').close();$('setupForm').onsubmit=e=>{e.preventDefault();action(async()=>{const f=new FormData(e.target);if(f.get('dataDirectory')&&f.get('dataDirectory')!==state.data_dir){await api('data-dir',{directory:f.get('dataDirectory')});await refresh();}await api('configure',{name:f.get('name'),rut:f.get('rut'),tolerance:f.get('tolerance'),control:Object.fromEntries(['numero','serie','proveedor','nombre'].map(k=>[k,f.get(k)]))});await refresh();$('notice').textContent='Empresa guardada. Importá sus maestros para empezar.';});};
$('catalogFile').onchange=()=>action(async()=>{preview=(await api('catalog/preview',await fileData($('catalogFile').files[0]))).rows;$('catalogPreview').innerHTML=`<p>${preview.length} registros. Las decisiones se conservan.</p><pre>${esc(JSON.stringify(preview.slice(0,5),null,2))}</pre><button id="commitCatalog">Confirmar importación</button>`;$('commitCatalog').onclick=()=>action(async()=>{await api('catalog/import',{rows:preview});$('catalogPreview').textContent='Catálogo importado.';await refresh();});});$('chrome').onclick=()=>action(async()=>{await api('chrome',{});$('notice').textContent='Abriendo Chrome de Zeta…';});action(async()=>{await refresh();if(!state.settings.company)setup();});let pollingJobs=false;
async function pollJobs(){
  if(pollingJobs||!state)return;
  const waiting=page==='review'&&!!$('precheckProgress');
  if(!waiting&&!state.jobs.some(j=>['queued','running','cancelling'].includes(j.status)))return;
  pollingJobs=true;
  try{await action(()=>refresh(['results','summary','home'].includes(page)||waiting));}finally{pollingJobs=false;}
}
setInterval(pollJobs,1500);

$('setup').insertAdjacentHTML('beforeend', '<hr><h3>Histórico y decisiones anteriores</h3><label class="button">Importar histórico Zeta<input id="historyFile" type="file" accept=".xlsx" hidden></label><label class="button">Migrar decisiones anteriores<input id="legacyFile" type="file" accept=".json" hidden></label><div id="legacyPreview"></div>');
$('historyFile').onchange=()=>action(async()=>{await api('history',await fileData($('historyFile').files[0]));$('notice').textContent='Hist?rico validado y conservado fuera del repositorio.';});
$('legacyFile').onchange=()=>action(async()=>{const data=await fileData($('legacyFile').files[0]),result=await api('legacy/preview',data);$('legacyPreview').innerHTML=`<p>${result.count} facturas identificadas.</p><pre>${esc(JSON.stringify(result.warnings,null,2))}</pre><button id="legacyCommit" ${result.warnings.some(w=>!w.startsWith('Las reglas anteriores'))?'disabled':''}>Confirmar migraci?n</button>`;$('legacyCommit').onclick=()=>action(async()=>{await api('legacy/import',data);await refresh();$('legacyPreview').textContent='Decisiones migradas. La exportaci?n original qued? archivada.';});});

$('dataPath').insertAdjacentHTML('afterend','<label>Carpeta de datos (antes de configurar empresa)<input name="dataDirectory" form="setupForm"></label>');

// Si hay una carga en curso, advertir antes de cerrar la ventana dedicada.
window.addEventListener('beforeunload', event => {
  if(!state?.jobs?.some(job=>['queued','running','cancelling'].includes(job.status)))return;
  event.preventDefault();
  event.returnValue='';
});
