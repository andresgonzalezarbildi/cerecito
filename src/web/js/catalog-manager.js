'use strict';
let catalogKind='supplier',catalogDraft=null,catalogFilename='';
const catalogNames={supplier:'Proveedores',article:'Artículos',concept:'Conceptos'};
const catalogButton=document.createElement('button');catalogButton.id='catalogs';catalogButton.textContent='Catálogos';
document.querySelector('.toolbar').append(catalogButton);
catalogButton.onclick=()=>{page='catalogs';draw();};
function catalogPage(){
  $('content').innerHTML=`<section class="card"><h2>Catálogos de Cerecito</h2><p>Actualizá las listas de Zeta que usás al revisar facturas. Los códigos existentes se actualizan; tus elecciones y los demás registros se conservan.</p><div class="actions">${Object.entries(catalogNames).map(([kind,name])=>`<button data-catalog-kind="${kind}" class="${kind===catalogKind?'primary':''}">${name} · ${state.catalog.filter(c=>c.kind===kind).length}</button>`).join('')}</div><h3>${catalogNames[catalogKind]}</h3><div class="actions"><label class="button">Importar ${catalogNames[catalogKind].toLowerCase()}<input id="catalogUpload" type="file" accept=".csv,.xlsx,.json" hidden></label><button id="catalogTemplate">Descargar plantilla CSV</button></div><p class="muted">Acepta el Excel de contactos exportado por Zeta (.xlsx), CSV o JSON. Detecta el encabezado y conserva el RUT existente si la planilla no lo incluye. Columnas: Código, Nombre${catalogKind==='supplier'?', RUT':', IVA (Exento, 10 o 22)'}. También se acepta Descripción en lugar de Nombre. Guardá códigos y RUT como texto para conservar ceros iniciales.</p><div id="catalogDraft"></div><label>Buscar en el catálogo<input type="search" id="catalogSearch" placeholder="Nombre, código o RUT"></label><div id="catalogRows"></div></section>`;
  document.querySelectorAll('[data-catalog-kind]').forEach(button=>button.onclick=()=>{catalogKind=button.dataset.catalogKind;catalogDraft=null;draw();});
  $('catalogUpload').onchange=()=>action(async()=>{
    const file=$('catalogUpload').files[0];if(!file)return;
    catalogDraft=null;$('catalogDraft').textContent='Leyendo archivo…';
    try{catalogDraft=(await api('catalog/preview',{...await fileData(file),kind:catalogKind})).rows;catalogFilename=file.name;showCatalogDraft();}
    catch(e){$('catalogDraft').textContent='No se importó el archivo.';throw e;}
  });
  $('catalogTemplate').onclick=()=>{
    const text=catalogKind==='supplier'?'Código;Nombre;RUT\r\n':'Código;Nombre;IVA\r\n';
    const url=URL.createObjectURL(new Blob(['\ufeff'+text],{type:'text/csv;charset=utf-8'}));
    const a=document.createElement('a');a.href=url;a.download=catalogNames[catalogKind]+'.csv';a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);
  };
  $('catalogSearch').oninput=showCatalogRows;showCatalogRows();showCatalogDraft();
}
function catalogTable(rows){
  return `<div class="catalog-table"><table><thead><tr><th>Código</th><th>Nombre</th><th>${catalogKind==='supplier'?'RUT':'IVA admitido'}</th></tr></thead><tbody>${rows.map(c=>`<tr><td>${esc(c.code)}</td><td>${esc(c.name)}</td><td>${esc(catalogKind==='supplier'?c.rut||'Sin RUT':(Array.isArray(c.vat)?c.vat:String(c.vat||'').split('|')).map(v=>({'1':'Exento','2':'10%','3':'22%'}[v]||'Sin definir')).join(', '))}</td></tr>`).join('')}</tbody></table></div>`;
}
function showCatalogRows(){
  const normalize=t=>String(t||'').normalize('NFD').replace(/[\u0300-\u036f]/g,'').toLowerCase();
  const query=normalize($('catalogSearch').value);
  const rows=state.catalog.filter(c=>c.kind===catalogKind&&normalize(c.code+' '+c.name+' '+(c.rut||'')).includes(query));
  $('catalogRows').innerHTML=`<p class="muted">${rows.length} registros${rows.length>100?' · Mostrando los primeros 100; escribí para filtrar.':''}</p>`+catalogTable(rows.slice(0,100));
}
function showCatalogDraft(){
  if(!catalogDraft){$('catalogDraft').innerHTML='';return;}
  const existing=new Set(state.catalog.filter(c=>c.kind===catalogKind).map(c=>c.code));
  const updates=catalogDraft.filter(c=>existing.has(c.code)).length;
  $('catalogDraft').innerHTML=`<section class="card"><h3>Vista previa · ${esc(catalogFilename)}</h3><p>${catalogDraft.length-updates} nuevos · ${updates} actualizaciones. Todavía no se guardó nada.</p>${catalogTable(catalogDraft.slice(0,20))}<p class="muted">Vista de las primeras 20 filas.</p><div class="actions"><button id="saveCatalog" class="primary">Confirmar importación</button><button id="discardCatalog">Descartar</button></div></section>`;
  $('discardCatalog').onclick=()=>{catalogDraft=null;showCatalogDraft();};
  $('saveCatalog').onclick=()=>action(async()=>{
    $('saveCatalog').disabled=true;
    try{const result=await api('catalog/import',{rows:catalogDraft});catalogDraft=null;await refresh();$('notice').textContent=`Catálogo actualizado: ${result.count} registros guardados.`;}
    finally{if($('saveCatalog'))$('saveCatalog').disabled=false;}
  });
}
