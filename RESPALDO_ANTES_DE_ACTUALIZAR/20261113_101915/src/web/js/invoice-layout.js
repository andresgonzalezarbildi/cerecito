/* Invoice presentation; decisions continue through the existing workflow API. */
lineHTML=function(i,l,n){
  const choice=i.decision.lines?.[n]||i.proposals[n]?.rule;
  const chosen=state.catalog.find(c=>c.kind===choice?.kind&&c.code===choice?.code);
  const tax={'1':'Exento','2':'10%','3':'22%','5':'Entrega gratuita (sin IVA)','6':'No facturable','7':'No facturable','11':'Impuesto percibido','12':'IVA en suspenso'}[l.indicadorIVA]||'Indicador fiscal '+l.indicadorIVA;
  if(i.auto_rounding_lines?.includes(n)){
    const groupedRounding=i.rounding_adjustment?.origenes.includes(n);
    return `<div class="invoice-row ignored"><div class="line-description"><span class="line-number">${n+1}</span><div><strong>${esc(l.descripcion)}</strong><small>${groupedRounding?'El ajuste se carga en un renglón separado.':'Redondeo omitido por defecto; se conserva en el XML.'}</small></div></div><div class="numeric" data-label="Cantidad">${esc(l.cantidad)}</div><div class="numeric" data-label="IVA">${esc(tax)}</div><div class="numeric" data-label="Importe">${esc(l.monto)}</div><div class="line-assignment"><small>${groupedRounding?'Elegí el destino en Agrupar por IVA':'No requiere destino'}</small></div></div>`;
  }
  if(Number(l.monto)===0||choice?.ignore||!['1','2','3','6','7'].includes(l.indicadorIVA)){
    const message=choice?.ignore?'Línea ignorada. Se conserva en el XML.':(i.proposals[n]?.reason||'');
    const canIgnore=Number(l.monto)===0||['6','7'].includes(l.indicadorIVA);
    return `<div class="invoice-row ${choice?.ignore?'ignored':''}"><div class="line-description"><span class="line-number">${n+1}</span><div><strong>${esc(l.descripcion)}</strong><small>${esc(message)}</small></div></div><div class="numeric" data-label="Cantidad">${esc(l.cantidad)}</div><div class="numeric" data-label="IVA">${esc(tax)}</div><div class="numeric" data-label="Importe">${esc(l.monto)}</div><div class="line-assignment">${canIgnore?`<button class="text-button" id="ignore${n}">${choice?.ignore?'Volver a incluir':'Ignorar línea'}</button>`:''}</div></div>`;
  }
  return `<div class="invoice-row ${choice?.ignore?'ignored':''}">
    <div class="line-description"><span class="line-number">${n+1}</span><div><strong>${esc(l.descripcion)}</strong>${choice?.ignore?'<small>Línea ignorada</small>':''}</div></div>
    <div class="numeric" data-label="Cantidad">${esc(l.cantidad)}</div>
    <div class="numeric" data-label="IVA">${esc(tax)}</div>
    <div class="numeric" data-label="Importe">${esc(l.monto)}</div>
    <div class="line-assignment"><details ${chosen&&!choice.ivaZeta&&!supportsVat(chosen,l.indicadorIVA)?'open':''}><summary class="destination ${chosen?'assigned':'pending'}">${chosen?esc(chosen.name):'Elegir destino'}<span>⌄</span></summary>
      <div class="line-editor"><label hidden>Tipo<select id="kind${n}"><option value="article">Artículo</option><option value="concept" ${(i.decision.destinationKind||choice?.kind)==='concept'?'selected':''}>Gasto</option></select></label>
      <label>Buscar destino<input id="search${n}" placeholder="Código o descripción"></label>
      <div class="choices" id="choices${n}"></div>
      ${['1','2','3'].includes(l.indicadorIVA)?`<label>IVA en Zeta<select id="lineVat${n}">${zetaVatOptions(choice?.ivaZeta)}</select></label>`:''}
      <small class="muted">${esc(i.proposals[n]?.reason||'')}</small>
      <small class="muted">El IVA elegido se guarda para este comprobante.</small></div></details>
      ${['6','7'].includes(l.indicadorIVA)?`<button class="text-button" id="ignore${n}">${choice?.ignore?'Volver a incluir':'Ignorar línea'}</button>`:''}
    </div></div>`;
};

function invoiceLayout(invoice,panel){
  const sheet=$('content').querySelector('.card');
  sheet.classList.add('invoice-sheet');
  const head=sheet.querySelector('.invoice-head');
  head.classList.add('invoice-sticky');
  if(invoice.finesa){
    const address=document.createElement('p');
    address.className='muted';
    address.textContent='DirRecep · '+(invoice.source.dirRecep||'Sin dato');
    head.querySelector('h2').after(address);
  }
  const title=panel.querySelector('h3');title.remove();
  panel.className='invoice-sector';
  const mapping=$('sectorMapping');
  const settings=document.createElement('details');settings.className='sector-settings';
  settings.innerHTML='<summary>Destinos por IVA</summary>';
  mapping.before(settings);settings.append(mapping);
  const note=panel.querySelector(':scope > .muted');if(note)note.remove();
  head.append(panel);
  if(invoice.source.tipo==='112'){
    const refs=(invoice.source.referencias||[]).map(r=>r.global==='1'?`Referencia global: ${r.motivo||'sin motivo'}`:`CFE ${r.tipo||'?'} · ${r.serie||'?'} / ${r.numero||'?'}`);
    const note=document.createElement('p');note.className='muted';
    note.textContent='Nota de crédito (importe fiscal positivo; en Zeta se carga como devolución). '+(refs.length?refs.join(' · '):'Sin referencia fiscal identificada.');
    head.append(note);
  }
  function compactMapping(){
    const apply=$('applySector');
    if(apply){apply.textContent='Aplicar a la factura';apply.setAttribute('aria-label','Aplicar sector a toda la factura');panel.append(apply);}
    settings.hidden=!$('invoiceSector').value;
    panel.querySelector('.distribution-note')?.remove();
    if($('invoiceSector').value==='__none__'){
      const note=document.createElement('small');note.className='distribution-note muted';
      note.textContent='Centro vacío para distribuir. Podés elegir conceptos en Destinos por IVA o conservar las líneas actuales.';
      panel.append(note);
      if(!invoice.decision.lines||!Object.keys(invoice.decision.lines).length)settings.open=true;
    }
  }
  const change=$('invoiceSector').onchange;
  $('invoiceSector').onchange=()=>{panel.querySelector('#applySector')?.remove();change();compactMapping();};
  compactMapping();
  const fields=sheet.querySelector(':scope > .fields');fields.classList.add('invoice-fields');
  const extra=document.createElement('details');extra.className='invoice-extra';
  extra.innerHTML='<summary>Proveedor en Zeta y otros datos</summary>';
  fields.before(extra);extra.append(fields);
  const destination=document.createElement('label');
  destination.innerHTML=`Tipo para toda la factura<select id="destinationKind"><option value="article">Artículo</option><option value="concept">Concepto</option></select>`;
  head.append(destination);
  $('destinationKind').value=invoice.decision.destinationKind||invoice.payload.lineas[0]?.tipoDestino||'article';
  $('destinationKind').onchange=()=>decide(invoice.key,{destinationKind:$('destinationKind').value});
  for(let n=0;n<invoice.source.lineas.length;n++){
    if(!$('kind'+n))continue; // Línea no monetaria sin editor de destino.
    $('kind'+n).value=$('destinationKind').value;
    renderChoices(invoice,n,$('search'+n).value,$('destinationKind').value);
    const vat=$('lineVat'+n);
    if(vat)vat.onchange=()=>{
      const line=invoice.payload.lineas.find(l=>l.origenes[0]===n);
      if(line&&line.tipoDestino===$('kind'+n).value){
        setLine(invoice,n,{kind:line.tipoDestino,code:line.codigoZeta,...(vat.value?{ivaZeta:vat.value}:{})});
      }
    };
  }
  const supplier=invoice.payload.proveedor;
  const rutRejected=/RUT del proveedor contradictorio/i.test(invoice.last_result?.motivo||'');
  if(!supplier.codigoZeta||supplier.seleccionManual||rutRejected||invoice.reasons.some(r=>/proveedor/i.test(r)))supplierSearch();
  else {
    $('supplier').closest('label').hidden=true;
    const provider=document.createElement('p');provider.className='muted';
    provider.textContent='Proveedor: '+$('supplier').selectedOptions[0].textContent;
    const changeSupplier=document.createElement('button');
    changeSupplier.type='button';changeSupplier.textContent='Cambiar proveedor';
    changeSupplier.onclick=()=>{provider.remove();changeSupplier.remove();supplierSearch();$('supplierSearch').focus();};
    fields.append(provider,changeSupplier);
  }
  if(supplier.seleccionManual){
    const note=document.createElement('p');note.className='muted';
    note.textContent='Para este comprobante se usa el código de proveedor elegido en Zeta y se conserva el RUT del XML.';
    fields.append(note);
  }
  const excluded=state.settings['excluded_suppliers:'+state.settings.company.rut]||{};
  const skipSupplier=document.createElement('button');skipSupplier.type='button';
  skipSupplier.textContent=excluded[invoice.source.rut]?'Volver a incluir este proveedor':'Saltear siempre este proveedor';
  skipSupplier.onclick=()=>{focusKey=null;decide(invoice.key,{skipSupplier:!excluded[invoice.source.rut]});};
  sheet.querySelector(':scope > .actions').append(skipSupplier);
  // Modality remains readily accessible alongside the invoice identity.
  const mode=$('mode').closest('label');mode.classList.add('invoice-mode');head.children[1].append(mode);
  if(rutRejected||invoice.reasons.some(r=>/proveedor|pago|centro/i.test(r)))extra.open=true;
  const rows=Array.from(sheet.querySelectorAll('.invoice-row'));
  const table=document.createElement('section');table.className='invoice-lines';table.setAttribute('aria-label','Detalle de la factura');
  table.innerHTML='<div class="invoice-columns"><span>Descripción</span><span>Cant.</span><span>IVA</span><span>Importe</span><span>Destino en Zeta</span></div>';
  if(rows.length){rows[0].before(table);rows.forEach(row=>table.append(row));}
  const totals=document.createElement('div');totals.className='invoice-total';
  totals.innerHTML=`<span>Total del comprobante</span><strong>${esc(invoice.source.moneda)} ${esc(invoice.source.totales.total)}</strong>`;
  if(rows.length)table.after(totals);
  else sheet.querySelector(':scope > .actions').before(totals);
  addTaxGrouping(invoice, rows.length ? table : totals);
  sheet.querySelector(':scope > .actions').classList.add('invoice-actions');
  sheet.querySelectorAll('.warning').forEach(el=>{if(!el.textContent.trim())el.remove();});
}

function addTaxGrouping(invoice,before){
  const panel=document.createElement('section');
  panel.className='tax-grouping';
  const grouped=invoice.decision.groupByVat===true;
  const manual=invoice.state==='manual';
  panel.innerHTML=`<button type="button" id="toggleTaxGrouping" aria-pressed="${grouped}">${grouped?'Quitar agrupación por IVA':'Agrupar por IVA'}</button>`;
  before.before(panel);
  $('toggleTaxGrouping').onclick=()=>decide(invoice.key,{groupByVat:!grouped});
  if(!grouped)return;

  const planByVat=new Map((invoice.plan?.lineas||[]).filter(line=>!line.esRedondeo).map(line=>[line.indicadorIVA,line]));
  const groups=(invoice.tax_groups||[]).map(group=>{
    const line=planByVat.get(group.iva);
    return line?{...group,etiqueta:line.descripcionXML,base:line.baseEsperada,impuesto:line.ivaEsperado,importe:line.totalEsperado}:group;
  });
  const kind=$('destinationKind').value;
  const linesByOrigin=new Map(invoice.payload.lineas.map(line=>[line.origenes[0],line]));
  const formatter=new Intl.NumberFormat('es-UY',{minimumFractionDigits:2,maximumFractionDigits:2});
  const fmt=value=>value==null?'':formatter.format(value);
  const adjustment=invoice.rounding_adjustment;
  const savedAdjustment=invoice.decision.roundingDestination;
  const adjustmentOptions=adjustment&&!manual?state.catalog.filter(c=>c.kind===kind)
    .sort((a,b)=>Number(/redondeo/i.test(b.name))-Number(/redondeo/i.test(a.name))):[];
  function destination(group){
    if(manual||!['1','2','3','12'].includes(group.iva)||!group.origenes.length)return '';
    const choices=group.origenes.map(index=>linesByOrigin.get(index));
    const selected=choices[0];
    const chosen=selected?.tipoDestino===kind&&choices.every(line=>line?.tipoDestino===kind&&line.codigoZeta===selected.codigoZeta)
      ?selected.codigoZeta:null;
    const rate=choices.every(line=>line?.ivaZeta===selected?.ivaZeta)?selected?.ivaZeta:'';
    const destinations=state.catalog.filter(c=>c.kind===kind)
      .sort((a,b)=>Number(supportsVat(b,group.iva))-Number(supportsVat(a,group.iva)));
    return `<select data-tax-group="${esc(group.iva)}" aria-label="Destino para ${esc(group.etiqueta)}"><option value="">Elegir destino…</option>${destinations.map(c=>`<option value="${esc(c.code)}" ${chosen===c.code?'selected':''}>${esc(c.code)} · ${esc(c.name)}</option>`).join('')}</select>
      <label>IVA en Zeta<select data-group-vat="${esc(group.iva)}" aria-label="IVA en Zeta para ${esc(group.etiqueta)}">${zetaVatOptions(rate)}</select></label>`;
  }
  panel.insertAdjacentHTML('beforeend',`<p class="muted">${manual?'Desglose del XML para completar en Zeta. Cada tratamiento especial queda separado.':'Elegí el destino y el IVA en Zeta por separado. Cerecito compara los importes con el XML.'}</p>
    <div class="catalog-table"><table><thead><tr><th>Grupo</th><th>Base</th><th>IVA</th><th>Importe</th>${manual?'':'<th>Destino en Zeta</th>'}</tr></thead>
    <tbody>${groups.map(g=>`<tr><td>${esc(g.etiqueta)}</td><td>${esc(fmt(g.base))}</td><td>${esc(fmt(g.impuesto))}</td><td>${esc(fmt(g.importe))}</td>${manual?'':`<td>${destination(g)}</td>`}</tr>`).join('')}</tbody></table></div>
    <p class="muted">Total fiscal del XML · ${esc(invoice.source.moneda)} ${esc(fmt(invoice.source.totales.total))}${invoice.source.totales.pagar? ` · Importe a pagar ${esc(fmt(invoice.source.totales.pagar))}`:''}</p>
    ${adjustment&&!manual?`<label>Destino del redondeo en Zeta · ${esc(invoice.source.moneda)} ${esc(fmt(adjustment.monto))} · Sin IVA
      <select id="roundingDestination"><option value="">Elegí el destino que usás para redondeos…</option>${adjustmentOptions.map(c=>`<option value="${esc(c.code)}" ${savedAdjustment?.kind===kind&&savedAdjustment.code===c.code?'selected':''}>${esc(c.code)} · ${esc(c.name)}</option>`).join('')}</select>
    </label>`:''}
    <button type="button" id="copyTaxGrouping">Copiar resumen</button>`);
  const rounding=$('roundingDestination');
  if(rounding)rounding.onchange=()=>decide(invoice.key,{roundingDestination:rounding.value?{kind,code:rounding.value}:null});
  panel.querySelectorAll('[data-tax-group],[data-group-vat]').forEach(select=>select.onchange=()=>{
    const vat=select.dataset.taxGroup||select.dataset.groupVat;
    const code=panel.querySelector(`[data-tax-group="${vat}"]`).value;
    if(!code)return;
    const ivaZeta=panel.querySelector(`[data-group-vat="${vat}"]`).value;
    const group=groups.find(g=>g.iva===vat);
    const lines=Object.fromEntries(group.origenes.map(index=>[index,{kind,code,...(ivaZeta?{ivaZeta}:{})}]));
    decide(invoice.key,{lines});
  });
  $('copyTaxGrouping').onclick=()=>action(async()=>{
    const s=invoice.source;
    const header=`${s.nombre} · ${s.serie}/${s.numero} · ${s.moneda}`;
    const text=groups.map(g=>[g.etiqueta,fmt(g.base),fmt(g.impuesto),fmt(g.importe)].join('\t'));
    text.push(['Total fiscal del XML','','',fmt(s.totales.total)].join('\t'));
    if(s.totales.pagar!=null)text.push(['Importe a pagar','','',fmt(s.totales.pagar)].join('\t'));
    await navigator.clipboard.writeText([header,'Grupo\tBase\tIVA\tImporte',...text].join('\n'));
    $('notice').textContent='Resumen por IVA copiado.';
  });
}

function supplierSearch(){
  const select=$('supplier'),label=select.closest('label');
  label.hidden=false;select.hidden=true;
  const input=document.createElement('input');input.id='supplierSearch';input.type='search';
  input.placeholder='Escribí nombre, código o RUT';input.setAttribute('aria-label','Buscar proveedor');
  input.autocomplete='off';
  const selected=state.catalog.find(c=>c.kind==='supplier'&&c.code===select.value);
  const current=document.createElement('small');current.className='muted supplier-current';
  current.textContent=selected?'Elegido: '+selected.code+' · '+selected.name:'Sin proveedor elegido';
  const results=document.createElement('div');results.id='supplierMatches';results.className='supplier-matches';
  const count=document.createElement('small');count.className='muted';count.setAttribute('aria-live','polite');
  label.append(input,current);
  label.after(results,count);
  const field=document.createElement('div');field.className='supplier-field';
  label.before(field);field.append(label,results,count);
  const normalize=text=>String(text||'').normalize('NFD').replace(/[\u0300-\u036f]/g,'').toLocaleLowerCase();
  function render(){
    const terms=normalize(input.value).trim().split(/\s+/).filter(Boolean);
    const matches=state.catalog.filter(c=>c.kind==='supplier'&&terms.every(t=>normalize(c.code+' '+c.name+' '+(c.rut||'')).includes(t)));
    results.innerHTML=matches.slice(0,30).map(c=>`<button type="button" data-supplier="${esc(c.code)}"><strong>${esc(c.name)}</strong><small>${esc(c.code)}${c.rut?' · RUT '+esc(c.rut):' · Sin RUT'}</small></button>`).join('');
    count.textContent=matches.length>30?`${matches.length} coincidencias. Escribí un poco más para acotar.`:matches.length?`${matches.length} coincidencias`:'No hay proveedores que coincidan.';
    results.querySelectorAll('button').forEach(button=>button.onclick=()=>{select.value=button.dataset.supplier;select.onchange();});
  }
  input.oninput=render;input.onfocus=render;
  input.onkeydown=event=>{
    if(event.key==='ArrowDown'){event.preventDefault();results.querySelector('button')?.focus();}
    if(event.key==='Enter'){
      event.preventDefault();
      if(results.querySelectorAll('button').length===1)results.querySelector('button').click();
    }
    if(event.key==='Escape'){results.innerHTML='';count.textContent='';input.blur();}
  };
}
