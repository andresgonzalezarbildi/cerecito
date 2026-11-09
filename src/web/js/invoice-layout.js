/* Invoice presentation; decisions continue through the existing workflow API. */
lineHTML=function(i,l,n){
  const choice=i.decision.lines?.[n]||i.proposals[n]?.rule;
  const chosen=state.catalog.find(c=>c.kind===choice?.kind&&c.code===choice?.code);
  const tax={'1':'Exento','2':'10%','3':'22%','5':'Entrega gratuita (sin IVA)','6':'No facturable','7':'No facturable','11':'Impuesto percibido','12':'IVA en suspenso'}[l.indicadorIVA]||'Indicador fiscal '+l.indicadorIVA;
  if(i.auto_rounding_lines?.includes(n)){
    return `<div class="invoice-row ignored"><div class="line-description"><span class="line-number">${n+1}</span><div><strong>${esc(l.descripcion)}</strong><small>Redondeo omitido por defecto; se conserva en el XML y se controla contra el total.</small></div></div><div class="numeric" data-label="Cantidad">${esc(l.cantidad)}</div><div class="numeric" data-label="IVA">${esc(tax)}</div><div class="numeric" data-label="Importe">${esc(l.monto)}</div><div class="line-assignment"><small>No requiere destino</small></div></div>`;
  }
  if(l.indicadorIVA==='5' && Number(l.monto)===0){
    return `<div class="invoice-row"><div class="line-description"><span class="line-number">${n+1}</span><div><strong>${esc(l.descripcion)}</strong><small>Entrega gratuita de importe cero; conservada en el XML, no se carga como compra</small></div></div><div class="numeric" data-label="Cantidad">${esc(l.cantidad)}</div><div class="numeric" data-label="IVA">${esc(tax)}</div><div class="numeric" data-label="Importe">${esc(l.monto)}</div><div class="line-assignment"><small>No requiere destino</small></div></div>`;
  }
  return `<div class="invoice-row ${choice?.ignore?'ignored':''}">
    <div class="line-description"><span class="line-number">${n+1}</span><div><strong>${esc(l.descripcion)}</strong>${choice?.ignore?'<small>Línea ignorada</small>':''}</div></div>
    <div class="numeric" data-label="Cantidad">${esc(l.cantidad)}</div>
    <div class="numeric" data-label="IVA">${esc(tax)}</div>
    <div class="numeric" data-label="Importe">${esc(l.monto)}</div>
    <div class="line-assignment"><details><summary class="destination ${chosen?'assigned':'pending'}">${chosen?esc(chosen.name):'Elegir destino'}<span>⌄</span></summary>
      <div class="line-editor"><label hidden>Tipo<select id="kind${n}"><option value="article">Artículo</option><option value="concept" ${(i.decision.destinationKind||choice?.kind)==='concept'?'selected':''}>Gasto</option></select></label>
      <label>Buscar destino<input id="search${n}" placeholder="Código o descripción"></label>
      <div class="choices" id="choices${n}"></div>
      <small class="muted">${esc(i.proposals[n]?.reason||'')}</small>
      <small class="muted">${i.finesa?'Los cambios de destino se guardan para este comprobante.':'Cada destino que elijas se aprende para próximas facturas del mismo proveedor, descripción e IVA.'}</small></div></details>
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
  }
  if(!invoice.payload.proveedor.codigoZeta||invoice.payload.proveedor.generico||invoice.reasons.some(r=>/proveedor/i.test(r)))supplierSearch();
  else {
    $('supplier').closest('label').hidden=true;
    const provider=document.createElement('p');provider.className='muted';
    provider.textContent='Proveedor: '+$('supplier').selectedOptions[0].textContent;
    fields.append(provider);
  }
  if(invoice.payload.proveedor.generico){
    const note=document.createElement('p');note.className='muted';
    note.textContent='Proveedor genérico para este comprobante: el RUT original del XML se conserva y esta asociación no se aprende para otras facturas.';
    fields.append(note);
  }
  const excluded=state.settings['excluded_suppliers:'+state.settings.company.rut]||{};
  const skipSupplier=document.createElement('button');skipSupplier.type='button';
  skipSupplier.textContent=excluded[invoice.source.rut]?'Volver a incluir este proveedor':'Saltear siempre este proveedor';
  skipSupplier.onclick=()=>{focusKey=null;decide(invoice.key,{skipSupplier:!excluded[invoice.source.rut]});};
  sheet.querySelector(':scope > .actions').append(skipSupplier);
  // Modality remains readily accessible alongside the invoice identity.
  const mode=$('mode').closest('label');mode.classList.add('invoice-mode');head.children[1].append(mode);
  if(invoice.reasons.some(r=>/proveedor|pago|centro/i.test(r)))extra.open=true;
  const rows=Array.from(sheet.querySelectorAll('.invoice-row'));
  const table=document.createElement('section');table.className='invoice-lines';table.setAttribute('aria-label','Detalle de la factura');
  table.innerHTML='<div class="invoice-columns"><span>Descripción</span><span>Cant.</span><span>IVA</span><span>Importe</span><span>Destino en Zeta</span></div>';
  if(rows.length){rows[0].before(table);rows.forEach(row=>table.append(row));}
  const totals=document.createElement('div');totals.className='invoice-total';
  totals.innerHTML=`<span>Total del comprobante</span><strong>${esc(invoice.source.moneda)} ${esc(invoice.source.totales.total)}</strong>`;
  table.after(totals);
  sheet.querySelector(':scope > .actions').classList.add('invoice-actions');
  sheet.querySelectorAll('.warning').forEach(el=>{if(!el.textContent.trim())el.remove();});
}

function supplierSearch(){
  const select=$('supplier'),label=select.closest('label');
  select.hidden=true;
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
