'use strict';
let catalogKind='supplier',catalogDraft=null,catalogFilename='',catalogFile=null;
const catalogNames={supplier:'Proveedores',article:'Artículos',concept:'Conceptos'};
const catalogButton=document.createElement('button');catalogButton.id='catalogs';catalogButton.textContent='Catálogos';
document.querySelector('.toolbar').append(catalogButton);
catalogButton.onclick=()=>{page='catalogs';draw();};
function catalogPage(){
  $('content').innerHTML=`<section class="card"><h2>Catálogos de Cerecito</h2><p>Actualizá las listas de Zeta que usás al revisar facturas. Los códigos existentes se actualizan; tus elecciones y los demás registros se conservan.</p><div class="actions">${Object.entries(catalogNames).map(([kind,name])=>`<button data-catalog-kind="${kind}" class="${kind===catalogKind?'primary':''}">${name} · ${state.catalog.filter(c=>c.kind===kind).length}</button>`).join('')}</div><h3>${catalogNames[catalogKind]}</h3><div class="actions"><label class="button">Cargar catálogos de Zeta<input id="catalogUpload" type="file" accept=".csv,.xlsx,.json" hidden></label><button id="catalogTemplate">Descargar plantilla CSV</button></div><p class="muted">Elegí el Excel de proveedores, conceptos o artículos exportado por Zeta. Cerecito identifica el catálogo por el nombre del archivo y sus columnas, y muestra una vista previa antes de importarlo.</p><div id="catalogDraft"></div><label>Buscar en el catálogo<input type="search" id="catalogSearch" placeholder="Nombre, código o RUT"></label><div id="catalogRows"></div></section>`;
  document.querySelectorAll('[data-catalog-kind]').forEach(button=>button.onclick=()=>{catalogKind=button.dataset.catalogKind;catalogDraft=null;draw();});
  $('catalogUpload').onchange=()=>{
    catalogFile=$('catalogUpload').files[0];
    if(catalogFile)previewCatalogFile();
  };
  $('catalogTemplate').onclick=()=>{
    const text=catalogKind==='supplier'?'Código;Nombre;RUT\r\n':'Código;Nombre;IVA\r\n';
    const url=URL.createObjectURL(new Blob(['\ufeff'+text],{type:'text/csv;charset=utf-8'}));
    const a=document.createElement('a');a.href=url;a.download=catalogNames[catalogKind]+'.csv';a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);
  };
  $('catalogSearch').oninput=showCatalogRows;showCatalogRows();showCatalogDraft();
}
async function previewCatalogFile(kind){
  await action(async()=>{
    catalogDraft=null;$('catalogDraft').textContent='Reconociendo el catálogo…';
    try{
      const data=await fileData(catalogFile);
      catalogDraft=(await api('catalog/preview',{...data,...(kind?{kind}:{})})).rows;
      catalogFilename=catalogFile.name;
      const kinds=[...new Set(catalogDraft.map(row=>row.kind))];
      if(kinds.length===1&&catalogNames[kinds[0]])catalogKind=kinds[0];
      draw();
    }catch(error){
      $('catalogDraft').innerHTML=`<p class="warning">${esc(error.message)}</p><label>Tipo de catálogo para este archivo<select id="catalogFallback"><option value="">Elegir…</option>${Object.entries(catalogNames).map(([value,label])=>`<option value="${value}">${label}</option>`).join('')}</select></label>`;
      $('catalogFallback').onchange=()=>{if($('catalogFallback').value)previewCatalogFile($('catalogFallback').value);};
    }
  });
}
function catalogTable(rows,kind=catalogKind){
  return `<div class="catalog-table"><table><thead><tr><th>Código</th><th>Nombre</th><th>${kind==='supplier'?'RUT':'IVA admitido'}</th></tr></thead><tbody>${rows.map(c=>`<tr><td>${esc(c.code)}</td><td>${esc(c.name)}</td><td>${esc(kind==='supplier'?c.rut||'Sin RUT':(Array.isArray(c.vat)?c.vat:String(c.vat||'').split('|')).map(v=>({'1':'Exento','2':'10%','3':'22%','12':'IVA en suspenso'}[v]||'Sin definir')).join(', '))}</td></tr>`).join('')}</tbody></table></div>`;
}
function showCatalogRows(){
  const normalize=t=>String(t||'').normalize('NFD').replace(/[\u0300-\u036f]/g,'').toLowerCase();
  const query=normalize($('catalogSearch').value);
  const rows=state.catalog.filter(c=>c.kind===catalogKind&&normalize(c.code+' '+c.name+' '+(c.rut||'')).includes(query));
  $('catalogRows').innerHTML=`<p class="muted">${rows.length} registros${rows.length>100?' · Mostrando los primeros 100; escribí para filtrar.':''}</p>`+catalogTable(rows.slice(0,100));
}
function showCatalogDraft(){
  if(!catalogDraft){$('catalogDraft').innerHTML='';return;}
  const existing=new Set(state.catalog.map(c=>c.kind+'|'+c.code));
  const updates=catalogDraft.filter(c=>existing.has(c.kind+'|'+c.code)).length;
  const tables=[...new Set(catalogDraft.map(row=>row.kind))].map(kind=>`<h4>${esc(catalogNames[kind]||kind)}</h4>${catalogTable(catalogDraft.filter(row=>row.kind===kind).slice(0,20),kind)}`).join('');
  $('catalogDraft').innerHTML=`<section class="card"><h3>Vista previa · ${esc(catalogFilename)}</h3><p>${catalogDraft.length-updates} nuevos · ${updates} actualizaciones. Todavía no se guardó nada.</p>${tables}<p class="muted">Vista de las primeras 20 filas.</p><div class="actions"><button id="saveCatalog" class="primary">Confirmar importación</button><button id="discardCatalog">Descartar</button></div></section>`;
  $('discardCatalog').onclick=()=>{catalogDraft=null;showCatalogDraft();};
  $('saveCatalog').onclick=()=>action(async()=>{
    $('saveCatalog').disabled=true;
    try{const result=await api('catalog/import',{rows:catalogDraft});catalogDraft=null;await refresh();$('notice').textContent=`Catálogo actualizado: ${result.count} registros guardados.`;}
    finally{if($('saveCatalog'))$('saveCatalog').disabled=false;}
  });
}
