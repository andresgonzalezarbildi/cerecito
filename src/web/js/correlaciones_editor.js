"use strict";

let providerRelationLimit = 400, lineRelationLimit = 300;
const RELATION_EDITOR_VERSION = 4, RELATION_PAGE_SIZE = 50;
let providerRelationPage = 1, lineRelationPage = 1, invoiceRelationPage = 1;
let providerPendingOnly = false;

function relationPageRows(rows, type) {
  const pages = Math.max(1, Math.ceil(rows.length / RELATION_PAGE_SIZE));
  let current = type==='provider' ? providerRelationPage : type==='line' ? lineRelationPage : invoiceRelationPage;
  current = Math.min(Math.max(current,1),pages);
  if(type==='provider')providerRelationPage=current;
  else if(type==='line')lineRelationPage=current;else invoiceRelationPage=current;
  const start = (current-1)*RELATION_PAGE_SIZE;
  document.getElementById(type+'RelCount').textContent = `${rows.length ? start+1 : 0}–${Math.min(start+RELATION_PAGE_SIZE,rows.length)} de ${rows.length} · Página ${current} de ${pages}`;
  document.getElementById(type+'RelPrev').disabled = current===1;
  document.getElementById(type+'RelNext').disabled = current===pages;
  return rows.slice(start,start+RELATION_PAGE_SIZE);
}

function changeRelationPage(type, delta) {
  if (!saveCorrelationChanges().ok) return;
  if(type==='provider')providerRelationPage+=delta;
  else if(type==='line')lineRelationPage+=delta;else invoiceRelationPage+=delta;
  renderRelations();
}

function showRelationSection(section) {
  document.querySelectorAll('[data-relation-section]').forEach(node=>node.hidden=node.dataset.relationSection!==section);
  document.querySelectorAll('[data-relation-tab]').forEach(button=>{
    const selected=button.dataset.relationTab===section;
    button.classList.toggle('active',selected);button.setAttribute('aria-pressed',String(selected));
  });
}

function invoiceTaxLabel(indicator) {
  return {'1':'Exento · 0%','2':'IVA 10%','3':'IVA 22%','6':'Línea no facturable · XML 6','7':'Línea no facturable negativa · XML 7','MIXTO':'IVA mixto: separar líneas'}[String(indicator)] || 'Indicador XML '+String(indicator || 'ausente');
}

function invoiceBotWarnings(x) {
  if(x.skipped)return ['Salteado por decisión del usuario'];
  const warnings=x.reviewed?[]:[...x.reasons];
  if(x.voucher && !['Compra Crédito','Compra Crédito Gastos'].includes(x.voucher))
    warnings.push('El bot todavía no tiene validado el formulario '+x.voucher+'.');
  if(x.activeLines.some(row=>!['1','2','3'].includes(String(row.item.ind))))
    warnings.push('Hay indicadores fiscales que requieren un flujo específico.');
  return [...new Set(warnings)];
}

function invoiceNextSteps(x) {
  const unsupported=x.baseLines.filter(r=>!r.excluded&&!['1','2','3'].includes(String(r.item.ind)));
  if(unsupported.length)return unsupported.map(r=>['6','7'].includes(String(r.item.ind))?'La línea «'+r.item.name+'» puede marcarse Ignorar línea. El bot verificará que los totales del XML sigan coincidiendo.':'La línea «'+r.item.name+'» tiene indicador XML '+r.item.ind+'. Requiere corregir el bot; no cambies el formulario ni el IVA para resolverla.');
  const steps=[];
  if(!x.p)steps.push('Relacioná el RUT '+x.cfe.rut+' con el proveedor real en la sección Proveedores.');
  if(!x.voucher)steps.push('Elegí Comprobante Zeta: artículos → Compra Crédito; conceptos → Compra Crédito Gastos.');
  if(x.cash&&!x.paymentForm)steps.push('Elegí la Forma de pago de esta compra.');
  if(x.center&&!x.centerAuto&&!x.reviewed)steps.push('Confirmá el Centro de esta compra o dejalo vacío.');
  if(!purchaseCategoriesComplete(x.cfe,x.saved))steps.push('Elegí el uso de cada producto: Alimentos y bebidas o Housekeeping.');
  for(const row of x.baseLines.filter(r=>!r.excluded&&(!r.target?.code||(!r.automatic&&!x.reviewed))))
    steps.push('Producto «'+row.item.name+'»: elegí explícitamente un artículo o concepto del catálogo.');
  const wrong=x.activeLines.some(r=>r.target?.kind && r.target.kind!==(x.voucher.includes('Gastos')?'concept':'article'));
  if(x.voucher&&wrong)steps.push('El formulario no coincide con las líneas. Usá artículos con Compra Crédito y conceptos con Compra Crédito Gastos.');
  if(x.complete&&x.status==='pending')steps.push('Verificá las selecciones; si son correctas, pulsá Confirmar revisión.');
  return [...new Set(steps)];
}

function invoiceTaskGroup(x) {
  const previous=window.CERECITO_LAST_REPORT?.items.find(i=>i.clave===x.key);
  if(previous?.resultado==='YA CARGADA'||previous?.resultado==='CARGADA')return 'loaded';
  if(['181','182','183'].includes(String(x.cfe.tipo))||x.skipped)return 'excluded';
  if(x.baseLines.some(r=>!r.excluded&&!['1','2','3','6','7'].includes(String(r.item.ind))))return 'bot';
  if(x.baseLines.some(r=>!r.excluded&&['6','7'].includes(String(r.item.ind))))return 'user';
  if(previous?.grupo==='Revisar en Zeta')return 'zeta';
  if(invoiceNextSteps(x).length)return 'user';
  if(invoiceBotWarnings(x).length)return 'bot';
  return 'ready';
}

function renderInvoiceRelationEditor() {
  const term=norm(document.getElementById('invoiceRelSearch').value), filter=document.getElementById('invoiceRelFilter').value;
  const all=state.invoices.map(classify);
  let summary=document.getElementById('pendingTaskSummary');
  if(!summary){summary=document.createElement('div');summary.id='pendingTaskSummary';document.getElementById('invoiceRelBody').closest('.tableWrap').before(summary);}
  const groups={user:'Completar datos',bot:'Requiere corregir el bot',zeta:'Revisar en Zeta',ready:'Listas para intentar',loaded:'Ya cargadas',excluded:'No cargar'};
  summary.innerHTML='<p>Elegí un grupo. Cada factura muestra pasos concretos y el resultado de la última ejecución.</p><button type="button" id="refreshBotReport">Actualizar resultado del bot</button><div class="taskFilters">'+Object.entries(groups).map(([id,label])=>`<button type="button" data-task-filter="${id}">${label} (${all.filter(x=>invoiceTaskGroup(x)===id).length})</button>`).join('')+'</div>';
  summary.querySelector('#refreshBotReport').onclick=refreshBotReport;
  summary.querySelectorAll('[data-task-filter]').forEach(b=>b.onclick=()=>{document.getElementById('invoiceRelFilter').value=b.dataset.taskFilter;invoiceRelationPage=1;renderInvoiceRelationEditor();});
  const filtered=all.filter(x=>(!term || norm([x.cfe.emisor,x.p?.providerName,x.cfe.rut,x.cfe.serie,x.cfe.numero].join(' ')).includes(term)) &&
    (filter==='all' || (filter==='pending' && !x.skipped && invoiceBotWarnings(x).length>0) ||
     (filter==='skipped' && x.skipped) || invoiceTaskGroup(x)===filter));
  const rows=relationPageRows(filtered,'invoice');
  document.getElementById('invoiceRelBody').innerHTML=rows.map(x=>{
    const warnings=invoiceBotWarnings(x), key=esc(x.key),steps=invoiceNextSteps(x),previous=window.CERECITO_LAST_REPORT?.items.find(i=>i.clave===x.key);
    return `<tr data-invoice-key="${key}" class="${x.skipped?'row-skipped':warnings.length?'row-pending':''}">
      <td><b>${esc(x.p?.providerName||x.cfe.emisor)}</b><div>${esc(x.cfe.serie)}-${esc(x.cfe.numero)}</div><div class="small">${esc(x.cfe.rut)} · ${esc(x.cfe.fecha)}</div><div>${esc(money(x.cfe.total,x.cfe.moneda))}</div></td>
      <td>${purchaseCategoryControl(x)}<label>Comprobante Zeta<select aria-label="Comprobante Zeta" data-invoice-field="voucher">${voucherOptions(x.voucher)}</select></label><label>Centro (opcional)<select aria-label="Centro de costos" data-invoice-field="center">${centerOptions(x.center)}</select></label><label>Forma de pago${x.cash?' (obligatoria)':''}<select aria-label="Forma de pago" data-invoice-field="payment" ${x.cash?'':'disabled'}>${paymentOptions(x.paymentForm)}</select></label></td>
      <td>${x.baseLines.map(row=>invoiceLineEditor(x,row)).join('') || 'Sin líneas de compra'}</td>
      <td><b>${esc(groups[invoiceTaskGroup(x)])}</b>${previous?`<div class="lastBotResult"><b>Última ejecución · ${esc(window.CERECITO_LAST_REPORT.fecha)}</b><p>${esc(previous.resultado)}${previous.docid?' · ID '+esc(previous.docid):''}</p><p>${esc(previous.motivo||'')}</p></div>`:''}<h3>Qué hacer ahora</h3><ol>${steps.map(message=>`<li>${esc(message)}</li>`).join('')}</ol>${!steps.length&&invoiceTaskGroup(x)==='bot'?'<p>Este pendiente necesita corregir el bot. No cambies el IVA ni los importes del XML.</p>':''}<details><summary>Detalles de validación</summary>${warnings.map(message=>`<p>${esc(message)}</p>`).join('')}</details>${x.complete&&x.status==='pending'?`<button type="button" data-invoice-review>Confirmar revisión</button>`:''}<button type="button" data-invoice-skip>${x.skipped?'Volver a incluir':'Saltear comprobante'}</button></td></tr>`;
  }).join('') || '<tr><td colspan="4">No hay comprobantes con este filtro. Los comprobantes vienen de los XML de la carga actual.</td></tr>';
}

function providerRelationRuts() {
  return [...new Set([...Object.keys(DB.providers || {}), ...Object.keys(rel.providers || {}),
    ...state.invoices.map(c => c.rut)])].filter(Boolean).sort();
}

function relationProviderName(rut) {
  return providerInfo(rut)?.providerName || state.invoices.find(c => c.rut === rut)?.emisor || "";
}

function appendInvoiceRelations(out, seen) {
  for (const cfe of state.invoices) {
    const x = classify(cfe);
    for (const row of x.baseLines) {
      const key = ruleKey(cfe.rut, x.center, row.item.name);
      if (seen.has(key)) continue;
      seen.add(key);
      out.push({key, rut:cfe.rut, center:x.center, item:row.item.name, historical:null,
        confidence:row.confidence || 0, count:row.count || 0, total:row.total || 0,
        target:row.target, manual:!!row.manual, disabled:!!manualRules[key]?.disabled});
    }
  }
}

function relationMessage(id, text, error = false) {
  const node = document.getElementById(id);
  node.textContent = text;
  node.classList.toggle("relationError", error);
}

function saveCorrelationChanges() {
  const nextRel = JSON.parse(JSON.stringify(rel)), nextRules = JSON.parse(JSON.stringify(manualRules));
  const fail = text => {
    if (document.getElementById('correlationSaveStatus')) relationMessage('correlationSaveStatus', text, true);
    return {ok:false, error:text};
  };
  for (const row of document.querySelectorAll('#providerRelBody tr[data-provider-rut]')) {
    const rut = row.dataset.providerRut, values = {};
    for (const input of row.querySelectorAll('[data-provider-field]')) values[input.dataset.providerField] = input.value.trim();
    const previous = {providerCode:providerInfo(rut)?.providerCode || '', providerName:relationProviderName(rut), center:defaultCenter(rut), paymentForm:paymentDefault(rut)};
    const changes = Object.fromEntries(Object.entries(values).filter(([key,value]) => value !== previous[key]));
    if (!Object.keys(changes).length) continue;
    if (!isValidCenter(values.center)) return fail('Centro no disponible para ' + rut);
    if (changes.providerCode && providerRelationRuts().some(r => r !== rut && providerInfo(r)?.providerCode === changes.providerCode)) return fail('Código de proveedor asociado a otro RUT: ' + changes.providerCode);
    nextRel.providers[rut] = {...nextRel.providers[rut], ...changes};
  }
  const observed = new Map();
  for (const row of document.querySelectorAll('#lineRelBody tr[data-rule-key]')) {
    const key = row.dataset.ruleKey, kind = row.querySelector('[data-rule-field="kind"]').value;
    const code = canonicalCode(kind, row.querySelector('[data-rule-field="code"]').value);
    const signature = JSON.stringify({kind,code});
    if (observed.has(key) && observed.get(key) !== signature) return fail('Hay valores diferentes para una misma relación. Revisá las filas.');
    observed.set(key, signature);
    const current = flattenRelations().find(r => r.key === key)?.target;
    if (kind === (current?.kind || '') && code === (current?.code || '')) continue;
    if (code && !targetAllowed(kind, code)) return fail('Código de destino no disponible: ' + code);
    if (!kind && code) return fail('Elegí Artículo o Concepto para ' + code);
    nextRules[key] = {...nextRules[key], kind, code, desc:catalogDesc(kind,code)};
  }
  const oldRel = localStorage.getItem(STORE_REL), oldRules = localStorage.getItem(STORE_RULES);
  try {
    localStorage.setItem(STORE_REL, JSON.stringify(nextRel));
    localStorage.setItem(STORE_RULES, JSON.stringify(nextRules));
  } catch (error) {
    try {
      if (oldRel === null) localStorage.removeItem(STORE_REL); else localStorage.setItem(STORE_REL,oldRel);
      if (oldRules === null) localStorage.removeItem(STORE_RULES); else localStorage.setItem(STORE_RULES,oldRules);
    } catch (_) { return fail('Falló el almacenamiento. No cierres esta pestaña; exportá las relaciones para conservarlas.'); }
    return fail('No se pudieron guardar los cambios. Se conservan los valores anteriores.');
  }
  rel = nextRel; manualRules = nextRules;
  render(); renderRelations();
  if (document.getElementById('correlationSaveStatus')) relationMessage('correlationSaveStatus','Cambios guardados en este navegador. Exportar relaciones JSON permite guardar una copia.');
  return {ok:true};
}

function renderProviderRelationEditor() {
  const term = norm(document.getElementById("providerRelSearch").value);
  const related=providerRelationRuts().map(rut=>({rut,code:providerInfo(rut)?.providerCode,name:relationProviderName(rut),pending:!providerInfo(rut)?.providerCode}));
  const codes=new Set(related.map(row=>catalogCodeKey(row.code||'')));
  const entries=[...related,...(DB.providerCatalog||[]).filter(row=>!codes.has(catalogCodeKey(row.code))).map(row=>({code:row.code,name:row.name,rut:'',pending:true}))];
  const all=entries.filter(row=>(!term || norm([row.rut,row.name,row.code].join(' ')).includes(term)) && (!providerPendingOnly || row.pending));
  const rows = relationPageRows(all,'provider');
  const pendingButton=document.getElementById('providerRelPendingBtn');
  pendingButton.classList.toggle('active',providerPendingOnly);
  pendingButton.textContent=(providerPendingOnly?'Mostrando pendientes':'Solo pendientes')+` (${entries.filter(row=>row.pending).length})`;
  document.getElementById("providerRelBody").innerHTML = rows.map(entry => {
    if(!entry.rut)return `<tr class="row-pending"><td>Sin RUT en el catálogo</td><td>${esc(entry.name)}</td><td>${esc(entry.code)}</td><td>${esc(entry.name)}</td><td></td><td></td><td><button type="button" data-provider-link="${esc(entry.code)}">Relacionar con RUT del XML</button></td></tr>`;
    const r=entry.rut;
    const base = DB.providers[r], p = providerInfo(r), missing = !p?.providerCode;
    return `<tr data-provider-rut="${esc(r)}" class="${missing ? 'row-pending' : ''}">
      <td>${esc(r)}${missing ? '<div class="small">Sin correlación</div>' : ''}</td>
      <td>${esc(base?.providerName || relationProviderName(r))}</td>
      <td><input aria-label="Código Zeta" data-provider-field="providerCode" value="${esc(p?.providerCode || '')}"></td>
      <td><input aria-label="Nombre Zeta" class="wide" data-provider-field="providerName" value="${esc(p?.providerName || relationProviderName(r))}"></td>
      <td><select aria-label="Centro por defecto" data-provider-field="center">${centerOptions(defaultCenter(r))}</select></td>
      <td><select aria-label="Pago contado por defecto" data-provider-field="paymentForm">${paymentOptions(paymentDefault(r))}</select></td>
      <td><button type="button" data-provider-reset>${base ? 'Restaurar histórico' : 'Quitar relación'}</button></td></tr>`;
  }).join("") || '<tr><td colspan="7">No hay proveedores con ese filtro. Podés agregarlo con el formulario.</td></tr>';
  document.getElementById("relationProviderOptions").innerHTML = providerRelationRuts().map(r => `<option value="${esc(r)}">${esc(relationProviderName(r))}</option>`).join("");
  document.getElementById("relationItemOptions").innerHTML = [...new Set(state.invoices.flatMap(c => c.items.map(i => i.name)))].map(name => `<option value="${esc(name)}"></option>`).join("");
}

function renderLineRelationEditor() {
  const term = norm(document.getElementById("lineRelSearch").value), all = flattenRelations();
  const pending = all.filter(relationNeedsChoice).length;
  const btn = document.getElementById("lineRelPendingBtn");
  btn.classList.toggle("active", relationPendingOnly);
  btn.textContent = relationPendingOnly ? `Mostrando pendientes (${pending})` : `Solo pendientes de elegir (${pending})`;
  const filtered = all.filter(r => (!term || norm([relationProviderName(r.rut),r.rut,r.center,r.item,r.target?.code,r.target?.desc].join(" ")).includes(term)) && (!relationPendingOnly || relationNeedsChoice(r)));
  const rows = relationPageRows(filtered,'line');
  document.getElementById("lineRelBody").innerHTML = rows.map(r => {
    const t = r.target || {};
    return `<tr data-rule-key="${esc(r.key)}" class="${relationNeedsChoice(r) ? 'row-pending' : ''}">
      <td>${esc(relationProviderName(r.rut) || r.rut)}<div class="small">${esc(r.rut)}</div></td>
      <td>${esc(r.center || 'Sin centro')}</td><td>${esc(r.item)}</td>
      <td>${r.historical ? esc(r.historical.code + ' · ' + r.historical.desc) : 'Sin histórico'}</td>
      <td><select aria-label="Tipo de destino" data-rule-field="kind"><option value="" disabled ${!t.kind?'selected':''}>Elegir tipo</option><option value="article" ${t.kind==='article'?'selected':''}>Artículo</option><option value="concept" ${t.kind==='concept'?'selected':''}>Concepto</option></select></td>
      <td><input aria-label="Código de destino" list="codeCatalog" data-rule-field="code" value="${esc(t.code || '')}"></td>
      <td>${esc(catalogDesc(t.kind,t.code) || t.desc || '')}</td><td>${pct(r.confidence)}<div class="small">${r.count}/${r.total}</div></td>
      <td><button type="button" data-rule-reset>${r.historical ? 'Restaurar histórico' : 'Quitar regla'}</button></td></tr>`;
  }).join("") || '<tr><td colspan="9">No hay relaciones con ese filtro. Podés crear una nueva.</td></tr>';
}

function addProviderRelation() {
  const form = document.getElementById("newProviderForm"), fields = form.elements;
  const rut = fields.rut.value.trim(), code = fields.code.value.trim(), name = fields.name.value.trim();
  const fail = text => relationMessage("newProviderStatus",text,true);
  if (!/^\d{12}$/.test(rut)) return fail("Ingresá un RUT de 12 dígitos.");
  if (!code || !name) return fail("Completá el código y el nombre reales del proveedor en Zeta.");
  if (providerInfo(rut)?.providerCode) return fail("Este proveedor ya está relacionado. Editalo en la tabla.");
  if (providerRelationRuts().some(r => r !== rut && providerInfo(r)?.providerCode === code))
    return fail("Ese código Zeta ya está relacionado con otro RUT. Revisá la identificación.");
  const center = fields.center.value, payment = fields.payment.value;
  if (!isValidCenter(center)) return fail("Elegí un centro disponible.");
  const next = {...rel, providers:{...rel.providers, [rut]:{...rel.providers[rut],providerCode:code,providerName:name,center,paymentForm:payment}}};
  if (!storageSet(STORE_REL,next)) return fail("No se pudo guardar. La relación anterior se conserva.");
  rel = next;
  document.getElementById("providerRelSearch").value = rut;
  fields.rut.value = fields.code.value = fields.name.value = "";
  fields.center.value = fields.payment.value = "";
  render(); renderRelations();
  relationMessage("newProviderStatus","Proveedor guardado. Podés editar sus valores en la tabla.");
}

function refreshNewRelationCodes() {
  const form = document.getElementById("newLineForm"), kind = form.elements.kind.value;
  document.getElementById("newRelationCodes").innerHTML = DB.catalog.filter(x => x.kind === kind).map(x => `<option value="${esc(x.code)}">${esc(x.desc)}</option>`).join("");
  document.getElementById("newRelationDescription").textContent = catalogDesc(kind,form.elements.code.value) || "Elegí un código del catálogo de Zeta.";
}

function addLineRelation() {
  const fields = document.getElementById("newLineForm").elements;
  const rut = fields.rut.value.trim(), center = fields.center.value, item = fields.namedItem('item').value.trim(), kind = fields.kind.value;
  const code = canonicalCode(kind,fields.code.value), fail = text => relationMessage("newLineStatus",text,true);
  if (!/^\d{12}$/.test(rut) || !providerInfo(rut)?.providerCode) return fail("Primero relacioná el proveedor con su código Zeta.");
  if (!item || item.includes('|')) return fail("Ingresá la descripción del XML, sin el carácter |.");
  if (!isValidCenter(center)) return fail("Elegí un centro disponible.");
  if (!['article','concept'].includes(kind) || !targetAllowed(kind,code)) return fail("Elegí un artículo activo o un concepto existente en el catálogo.");
  const key = ruleKey(rut,center,item);
  if (manualRules[key]) return fail("Ya existe una regla personalizada para esa descripción y centro. Editala en la tabla.");
  const next = {...manualRules,[key]:{kind,code,desc:catalogDesc(kind,code),descriptionXML:item}};
  if (!storageSet(STORE_RULES,next)) return fail("No se pudo guardar. Las reglas anteriores se conservan.");
  manualRules = next;
  relationPendingOnly = false;
  document.getElementById("lineRelSearch").value = rut;
  render(); renderRelations();
  relationMessage("newLineStatus","Regla guardada para ese proveedor, centro y descripción.");
}

function initRelationEditor() {
  for (const id of ['newProviderCenter','newLineCenter']) document.getElementById(id).innerHTML = centerOptions('');
  document.getElementById("newProviderPayment").innerHTML = paymentOptions('');
  document.getElementById("newProviderForm").addEventListener('submit',e => {e.preventDefault();addProviderRelation();});
  document.getElementById("newLineForm").addEventListener('submit',e => {e.preventDefault();addLineRelation();});
  document.getElementById("newLineKind").addEventListener('change',refreshNewRelationCodes);
  document.getElementById("newLineCode").addEventListener('input',refreshNewRelationCodes);
  for(const type of ['provider','line','invoice'])for(const [suffix,delta] of [['Prev',-1],['Next',1]])
    document.getElementById(type+'Rel'+suffix).addEventListener('click',()=>changeRelationPage(type,delta));
  document.querySelectorAll('[data-relation-tab]').forEach(button=>button.addEventListener('click',()=>{
    if(saveCorrelationChanges().ok)showRelationSection(button.dataset.relationTab);
  }));
  document.getElementById('providerRelPendingBtn').addEventListener('click',()=>{providerPendingOnly=!providerPendingOnly;providerRelationPage=1;renderProviderRelations();});
  document.getElementById('providerRelSearch').addEventListener('input',()=>{providerRelationPage=1;renderProviderRelations();});
  document.getElementById('lineRelSearch').addEventListener('input',()=>{lineRelationPage=1;renderLineRelations();});
  document.getElementById('invoiceRelSearch').addEventListener('input',()=>{invoiceRelationPage=1;renderInvoiceRelationEditor();});
  document.getElementById('invoiceRelFilter').addEventListener('change',()=>{invoiceRelationPage=1;renderInvoiceRelationEditor();});
  document.getElementById('invoiceRelBody').addEventListener('change',e=>{
    const row=e.target.closest('[data-invoice-key]');if(!row)return;
    const key=row.dataset.invoiceKey,field=e.target.dataset.invoiceField;
    if(field){({'voucher':setVoucher,'center':setCenter,'payment':setPayment}[field])(key,e.target.value);renderRelations();}
    const lineField=e.target.dataset.invoiceLineField;
    if(lineField){const index=e.target.closest('[data-invoice-line]').dataset.invoiceLine;
      setLine(key,index,lineField==='code'?e.target.value:null,lineField==='kind'?e.target.value:null);}
  });
  document.getElementById('invoiceRelBody').addEventListener('click',e=>{
    if(e.target.closest('[data-invoice-skip]')){toggleSkip(e.target.closest('[data-invoice-key]').dataset.invoiceKey);renderRelations();}
  });
  document.getElementById("providerRelBody").addEventListener('change',e => {
    if (e.target.dataset.providerField) editProvider(e.target.closest('tr').dataset.providerRut,e.target.dataset.providerField,e.target.value);
  });
  document.getElementById("providerRelBody").addEventListener('click',e => {
    const link=e.target.closest('[data-provider-link]');
    if(link){const record=DB.providerCatalog.find(p=>p.code===link.dataset.providerLink);if(record){
      document.getElementById('newProviderCode').value=record.code;document.getElementById('newProviderName').value=record.name;
      document.getElementById('newProviderRut').value='';document.getElementById('newProviderRut').focus();}return;}
    if (e.target.closest('[data-provider-reset]')) resetProvider(e.target.closest('tr').dataset.providerRut);
  });
  document.getElementById("lineRelBody").addEventListener('change',e => {
    const field = e.target.dataset.ruleField;
    if (field) {
      const row = e.target.closest('tr'), key=row.dataset.ruleKey;
      if (field === 'code' && !row.querySelector('[data-rule-field="kind"]').value) {
        relationMessage('newLineStatus','Elegí Artículo o Concepto antes de asignar el código.',true);
        return;
      }
      // Una fila sin histórico toma el tipo visible antes de resolver el código.
      if (field === 'code' && !manualRules[key] && !flattenRelations().find(r=>r.key===key)?.target)
        editLineRule(key,'kind',row.querySelector('[data-rule-field="kind"]').value);
      editLineRule(key,field,e.target.value);
    }
  });
  document.getElementById("lineRelBody").addEventListener('click',e => {
    if (e.target.closest('[data-rule-reset]')) resetLineRule(e.target.closest('tr').dataset.ruleKey);
  });
  refreshNewRelationCodes(); renderRelations();
}

initRelationEditor();
document.getElementById('invoiceRelBody').addEventListener('click',event=>{
  const ignore=event.target.closest('[data-ignore-line]');
  if(ignore){
    const key=ignore.closest('[data-invoice-key]').dataset.invoiceKey,index=Number(ignore.dataset.ignoreLine),cfe=getInvoiceByKey(key);
    const row=classify(cfe).baseLines.find(r=>r.origIndex===index);
    if(row.excluded){const saved=invState(key);saved.excludedLines=(saved.excludedLines||[]).filter(i=>Number(i)!==index);saved.reviewed=false;storageSet(STORE_INV,invoiceStates);render();}
    else removeLine(key,index);
    renderRelations();return;
  }
  if(event.target.closest('[data-invoice-review]')){markReviewed(event.target.closest('[data-invoice-key]').dataset.invoiceKey);renderRelations();}
});

function invoiceLineEditor(x,row){
  const canIgnore=['6','7'].includes(String(row.item.ind));
  return `<div class="invoiceRelationLine ${row.excluded?'line-ignored':''}" data-invoice-line="${row.origIndex}">
    <div class="lineTitle"><b>${esc(row.item.name)}</b>${canIgnore||row.excluded?`<button type="button" data-ignore-line="${row.origIndex}">${row.excluded?'Volver a incluir':'Ignorar línea'}</button>`:''}</div>
    <div class="lineAmounts">Cantidad: ${esc(row.item.qty)} · Importe XML: ${esc(money(row.item.amount,x.cfe.moneda))}</div>
    <div class="xmlTax">${esc(invoiceTaxLabel(row.item.ind))}</div>
    ${row.excluded?'<p class="ignoredNotice">Ignorada por tu elección. Se conserva en el XML; solo se omitirá si los totales siguen coincidiendo.</p>':canIgnore?'<p>Para omitir esta línea, pulsá Ignorar línea. No hace falta asignarle un artículo o concepto. Si necesitás cargarla, requiere un flujo específico.</p>':`${purchaseLineCategoryControl(x,row)}<div class="lineFields"><label>Tipo de destino<select aria-label="Tipo de destino" data-invoice-line-field="kind"><option value="" ${!row.target?.kind?'selected':''}>Elegir tipo</option><option value="article" ${row.target?.kind==='article'?'selected':''}>Artículo</option><option value="concept" ${row.target?.kind==='concept'?'selected':''}>Concepto</option></select></label><label>Artículo / código Zeta${purchaseArticleControl(x,row)}</label></div><div class="small">${esc(row.target?.desc||'Falta artículo o concepto')}</div>`}
  </div>`;
}

function refreshBotReport(){
  const script=document.createElement('script');
  script.src='js/ultimo_resultado.js?t='+Date.now();
  script.onload=()=>{script.remove();renderInvoiceRelationEditor();toast('Resultado del bot actualizado');};
  script.onerror=()=>{script.remove();toast('No se pudo leer el último resultado del bot');};
  document.head.appendChild(script);
}


