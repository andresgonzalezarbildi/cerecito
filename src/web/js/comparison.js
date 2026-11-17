'use strict';
function currentComparison(){
  return state.jobs.find(job=>job.data.kind==='comparison'&&job.data.batch_date===batchRevision(state.settings.batch));
}
function comparisonPage(){
  const batch=state.settings.batch,job=currentComparison();
  const active=state.jobs.find(j=>['queued','running','cancelling'].includes(j.status));
  const finished=job?.status==='finished';
  // El sondeo no debe reiniciar el iframe ni borrar la pestaña que está mirando el usuario.
  const current=$('comparisonFrame');
  if(finished&&current?.dataset.job===job.id)return;
  const fmt=value=>new Date(value+'T12:00:00').toLocaleDateString('es-UY');
  $('content').innerHTML=`<section class="card comparison-intro"><div><p class="eyebrow">CONTROL DEL LOTE ACTUAL</p><h2>¿Qué falta o tiene diferencias?</h2>
    <p class="muted">${batch?esc(batch.filename):'Importá un ZIP de FacturaLista para comenzar.'}</p>
    <details><summary>Qué se compara</summary><p>Se descarga el resumen de compras desde la fecha de emisión más antigua del ZIP hasta hoy y se compara aquí mismo.</p>
    <p class="muted">Se omiten cobranzas, importes fiscales en cero, Scotia, Compañía Uruguaya de Medios de Procesamiento y repeticiones dentro del ZIP.</p></details>
    ${job?`<p class="muted">Consulta: ${fmt(job.data.from)} a ${fmt(job.data.to)} · ${esc(new Date(job.created).toLocaleString('es-UY'))}</p>`:''}</div>
    <div class="actions"><button class="primary" id="exportCompare" ${!batch||active||busy?'disabled':''}>${active?.data.kind==='comparison'?'Exportando…':finished?'Actualizar comparación':'Exportar de Zeta y comparar'}</button>
    ${finished?'<button id="downloadZetaExcel">Descargar Excel de Zeta</button>':''}</div>
    ${active?.data.kind==='comparison'?`<p role="status">${esc(active.data.message)}</p><progress aria-label="Exportación en curso"></progress><button id="cancelComparison">Cancelar comparación</button>`:active?'<p class="muted">Hay otro trabajo en curso. Podés comparar cuando termine.</p>':''}
    ${job&&['failed','interrupted','cancelled'].includes(job.status)?`<p class="warning">${esc(job.data.error||'La exportación se interrumpió. Volvé a comparar.')}</p>`:''}
    </section>${finished?`<iframe id="comparisonFrame" class="comparison-frame" title="Faltantes y diferencias del ZIP con Zeta" src="comparacion.html?embedded=1" data-job="${esc(job.id)}"></iframe>`:''}`;
  $('exportCompare').onclick=()=>action(async()=>{
    if(busy)return;
    busy=true;$('exportCompare').disabled=true;
    try{await api('comparison',{id:crypto.randomUUID(),revision:batchRevision(batch)});await refresh();}
    finally{busy=false;if(page==='comparison')draw();}
  });
  if($('cancelComparison'))$('cancelComparison').onclick=()=>action(async()=>{await api('jobs/cancel',{id:active.id});await refresh();});
  if(!finished)return;
  const frame=$('comparisonFrame');
  frame.onload=()=>frame.contentWindow.postMessage({type:'cerecito-comparison',id:job.id,token:state.token,
    filename:job.data.filename,excelName:job.data.export.filename,expectedRows:job.data.export.rows,
    from:job.data.from,to:job.data.to},location.origin);
  $('downloadZetaExcel').onclick=()=>action(async()=>{
    const response=await fetch(`/api/comparison/file?job=${encodeURIComponent(job.id)}&part=excel`,{headers:{'X-Cerecito-Token':state.token}});
    if(!response.ok)throw Error((await response.json()).error);
    const url=URL.createObjectURL(await response.blob()),link=document.createElement('a');
    link.href=url;link.download=job.data.export.filename;document.body.appendChild(link);link.click();link.remove();
    setTimeout(()=>URL.revokeObjectURL(url),30000);
  });
}
$('comparison').onclick=()=>action(async()=>{page='comparison';await refresh();});
