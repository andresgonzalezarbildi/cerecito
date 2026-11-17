'use strict';
if(new URLSearchParams(location.search).get('embedded')==='1'){
  document.documentElement.classList.add('embedded');
  const selector=$$('run').closest('section'),status=$$('status');
  selector.before(status);selector.hidden=true;
  document.querySelector('.header').hidden=true;
  setProgress('Preparando la comparación del lote…');
  let started=false;
  window.addEventListener('message',async event=>{
    if(event.origin!==location.origin||event.source!==parent||event.data?.type!=='cerecito-comparison'||started)return;
    started=true;
    const job=event.data;
    async function input(part){
      const response=await fetch(`/api/comparison/file?job=${encodeURIComponent(job.id)}&part=${part}`,{headers:{'X-Cerecito-Token':job.token}});
      if(!response.ok)throw Error((await response.json()).error);
      return response.arrayBuffer();
    }
    try{
      setProgress('Leyendo el Excel descargado de Zeta…');
      const purchases=await readXlsx(await input('excel'));
      if(purchases.results.length!==job.expectedRows)throw Error('No se pudieron leer todos los comprobantes del Excel. No se muestra una comparación incompleta.');
      setProgress('Comparando con los XML del lote actual…');
      const docs=await readZip(await input('zip'));
      const report=compare(purchases.results,docs,job.filename);
      report.stats.inicio=job.from;report.stats.fin=job.to;
      report.sheets[0][2].push(['Archivo Excel',job.excelName],['Exportación desde',job.from],['Exportación hasta',job.to]);
      latest=report;showReport(report);
      setProgress('Comparación completada. Revisá los faltantes y las diferencias.');
    }catch(error){setProgress('No se pudo completar la comparación: '+error.message);}
  });
}
