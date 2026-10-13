"""Informe local de acciones por factura; nunca escribe en Zeta."""

from runtime_paths import DATA_DIR, CODE_DIR, APP_DIR, APP_URL, company_rut, control_value, category_provider_rut
from html import escape
from pathlib import Path
import json


def actions(item,invoice):
    status=item.get('estado','')
    if status=='EXCLUIDO':return []
    reason=item.get('motivo','')
    if any(text in reason for text in ('indicador 6 sin plan','indicador 7 sin plan','fiscal XML 6 sin plan','fiscal XML 7 sin plan')):
        return ['En Comprobantes, pulsá Ignorar línea junto al redondeo o la línea no facturable que querés omitir. El bot comprobará que la base, el IVA y el total del XML sigan coincidiendo.']
    if status=='APTO_PENDIENTE_GUARDADO':return ['Datos y plan verificados. Ejecutá INICIAR_BOT.bat para cargarla; esta revisión no guardó nada.']
    if status in {'CONFIRMADO','YA_CONFIRMADO_LOCAL','RESULTADOS_EN_ZETA_NO_CARGAR'}:return []
    if status=='DOCUMENTO_NO_COMPRA':return []
    if status in {'INCIERTO','BLOQUEADO_INCIERTO'}:
        return ['Verificar en Zeta el ID y las líneas guardadas antes de repetir. No borrar el registro local para forzar una carga.']
    if status=='BLOQUEADO_CONFIRMACION_PROVEEDOR':
        code=item.get('proveedor_zeta',{}).get('codigoZeta','')
        return ['Ejecutá INICIAR_BOT.bat. Cuando muestre este proveedor, si es el mismo del XML, escribí '+('CONFIRMAR '+code if code else 'la confirmación indicada en la ventana')+'. La relación se guarda para próximas ejecuciones.']
    if status in {'BLOQUEADO_COINCIDENCIAS','BLOQUEADO_HISTORICO'}:
        return ['Comparar emisor/RUT, serie, número e importe con los registros mostrados abajo. Corregir la correlación en Cerecito o aportar un histórico actualizado; no forzar el alta.']
    if any(word in reason for word in ('Falta validar','sin plan validado','redondeo','sin captura','sin validar')):
        return ['Requiere corregir o validar el bot. Tus datos no resuelven este bloqueo; conservá el IVA y los importes del XML.']
    if 'Destino contable distinto' in reason:
        return ['En Comprobantes, elegí Compra Crédito para artículos o Compra Crédito Gastos para conceptos. El formulario y el tipo de destino de las líneas deben coincidir.']
    result=[]
    if 'Contado: falta forma de pago' in reason:
        result.append('En Comprobantes → Forma de pago, elegí cómo se pagó esta factura.')
    if 'Centro de costo variable' in reason:
        result.append('En Comprobantes → Centro, elegí el área de esta compra o dejalo vacío. Si el centro mostrado ya es correcto y los datos están completos, pulsá Confirmar revisión.')
    if 'baja confianza' in reason:
        result.append('Revisá proveedor, formulario y códigos de las líneas. Si son correctos, marcá la factura como revisada.')
    if 'Finesa:' in reason:
        result.append('Elegí Alimentos y bebidas o Housekeeping para cada producto de Finesa.')
    if not invoice.get('proveedor',{}).get('codigoZeta'):
        result.append('Cerecito → Correlaciones: asociar el RUT del XML con el código real del proveedor en Zeta.')
    voucher=invoice.get('zeta',{}).get('comprobante','')
    if not voucher:result.append('Cerecito → Carga: elegir el tipo de comprobante correspondiente a esta factura.')
    for line in invoice.get('lineas',[]):
        if not line.get('codigoZeta') or not line.get('tipoDestino'):
            result.append('Cerecito → Correlaciones: elegir artículo/concepto y código real para «'+line.get('descripcionXML','')+'».')
    if 'requieren revisión' in reason and not any('artículo/concepto' in text for text in result):
        result.append('En Comprobantes, elegí explícitamente el artículo o concepto de las líneas pendientes; una sugerencia histórica todavía no es una elección confirmada.')
    if not invoice.get('completo') or invoice.get('estado') not in {'auto','reviewed'}:
        result.append('Completar las líneas y revisar la factura en Cerecito. Guardar cambios. El centro de costos puede quedar vacío.')
    if not result:result.append('Resolver el motivo indicado. Si dice formulario/IVA no validado o muestra un error de navegador, requiere corregir el bot; no modificar los datos fiscales para evitarlo.')
    return result


def pending_group(item):
    if item.get('estado')=='EXCLUIDO':return 'Salteadas por tu elección'
    if any(text in item.get('motivo','') for text in ('indicador 6 sin plan','indicador 7 sin plan','fiscal XML 6 sin plan','fiscal XML 7 sin plan')):return 'Completar en Cerecito'
    if item.get('estado')=='APTO_PENDIENTE_GUARDADO':return 'Listas para cargar'
    if result_label(item)!='NO CARGADA':return 'Cargadas'
    if item.get('estado')=='DOCUMENTO_NO_COMPRA':return 'No son facturas'
    if item.get('estado') in {'INCIERTO','BLOQUEADO_INCIERTO','BLOQUEADO_COINCIDENCIAS','BLOQUEADO_HISTORICO'}:return 'Revisar en Zeta'
    if any(text in item.get('motivo','') for text in ('Falta validar','sin plan validado','redondeo','sin captura','sin validar')):return 'Requiere corregir el bot'
    return 'Completar en Cerecito'


def result_label(item):
    if item.get('estado')=='EXCLUIDO':return 'SALTEADA'
    if item.get('resultado'):return item['resultado']
    if item['estado']=='CONFIRMADO':return 'CARGADA'
    if item['estado'] in {'YA_CONFIRMADO_LOCAL','RESULTADOS_EN_ZETA_NO_CARGAR'}:return 'YA CARGADA'
    return 'NO CARGADA'


def write_report(report,invoices,folder):
    folder=Path(folder);folder.mkdir(parents=True,exist_ok=True)
    indexed={i['clave']:i for i in invoices};rows=[];pending=[]
    for item in report.get('comprobantes',[]):
        result=result_label(item)
        if result.startswith('NO CARGADA'):
            invoice=indexed.get(item['clave'],{})
            pending.append({'clave':item['clave'],'proveedor':item.get('proveedor',''),
                'estado':item['estado'],'resultado':result,'motivo':item.get('motivo',''),
                'acciones':actions(item,invoice),'grupo':pending_group(item),
                'consultas_zeta':item.get('consultas_zeta',[])})
        notice=''
        if item.get('guardado_parcial'):notice=' (guardado parcial; no se repite)'
        elif item['estado'] in {'INCIERTO','BLOQUEADO_INCIERTO'}:notice=' (guardado por verificar; no se repite)'
        group=pending_group(item)
        todo=actions(item,indexed.get(item['clave'],{})) if result.startswith('NO CARGADA') else []
        cells=[result+notice,item.get('proveedor',''),item['clave'],str(item.get('totalXML',''))+' '+item.get('moneda',''),str(item.get('docid','')),item.get('motivo','') if result.startswith('NO CARGADA') else '']
        if item.get('diferencias_redondeo'):
            cells[-1]='Diferencia de redondeo aceptada (Zeta menos XML): '+', '.join(key+' '+str(value) for key,value in item['diferencias_redondeo'].items())
        rows.append('<tr data-group="'+escape(group,quote=True)+'">'+''.join('<td>'+escape(str(value),quote=True)+'</td>' for value in cells)+'<td><b>'+escape(group)+'</b><ol>'+''.join('<li>'+escape(text)+'</li>' for text in todo)+'</ol></td></tr>')
    groups=['Completar en Cerecito','Listas para cargar','Revisar en Zeta','Requiere corregir el bot','Salteadas por tu elección','No son facturas','Cargadas']
    buttons=''.join('<button data-filter="'+group+'">'+group+' ('+str(sum(pending_group(i)==group for i in report.get('comprobantes',[])))+')</button>' for group in groups)
    html='''<!doctype html><html lang="es"><meta charset="utf-8"><title>Pendientes de Cerecito</title><style>body{font:16px system-ui;margin:30px;color:#182332;background:#f7f9fc}table{border-collapse:collapse;width:100%;background:white}td,th{border-bottom:1px solid #ddd;padding:12px;text-align:left;vertical-align:top}th{background:#e9eef6}button,input{font:inherit;padding:10px;margin:4px;border:1px solid #bcc9dc;border-radius:8px}button{cursor:pointer}li{margin:8px 0}td:last-child{min-width:260px}nav{margin:20px 0}</style><h1>Qué queda por resolver</h1><p>Las facturas cargadas aparecen aparte. Cada pendiente indica quién debe resolverlo y el siguiente paso.</p><nav>'''+buttons+'''<button data-filter="Todos">Todos</button></nav><input id="search" placeholder="Buscar proveedor o número" aria-label="Buscar"><table><thead><tr><th>Resultado</th><th>Proveedor</th><th>Comprobante</th><th>Total XML</th><th>ID Zeta</th><th>Motivo</th><th>Qué hacer ahora</th></tr></thead><tbody>'''+''.join(rows)+'''</tbody></table><script>
let group='Completar en Cerecito';function filter(){const term=document.getElementById('search').value.toLocaleLowerCase();document.querySelectorAll('tbody tr').forEach(row=>row.hidden=(group!=='Todos'&&row.dataset.group!==group)||!row.textContent.toLocaleLowerCase().includes(term));document.querySelectorAll('button').forEach(b=>b.style.background=b.dataset.filter===group?'#dceafe':'white')};document.querySelectorAll('button').forEach(b=>b.onclick=()=>{group=b.dataset.filter;filter()});document.getElementById('search').oninput=filter;filter();</script></html>'''
    target=folder/'pendientes.html';target.write_text(html,encoding='utf-8')
    (folder/'acciones_pendientes.json').write_text(json.dumps(pending,ensure_ascii=False,indent=2),encoding='utf-8')
    app=DATA_DIR/'web'
    if app.is_dir():
        summary={'fecha':report.get('fin') or report.get('inicio'),'items':[
            {field:item.get(field,'') for field in ('clave','proveedor','numero','resultado','estado','motivo','docid')}
            | {'grupo':pending_group(item),'acciones':actions(item,indexed.get(item['clave'],{}))}
            for item in report.get('comprobantes',[])]}
        (app/'ultimo_resultado.js').write_text('window.CERECITO_LAST_REPORT='+json.dumps(summary,ensure_ascii=False)+';',encoding='utf-8')
    return target
