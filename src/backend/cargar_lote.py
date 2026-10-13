"""Recorre todos los CFE; carga únicamente ausencias verificadas y formularios validados."""

from runtime_paths import DATA_DIR, CODE_DIR, APP_DIR, APP_URL, company_rut, control_value, category_provider_rut
import argparse
import csv
from collections import Counter
from copy import deepcopy
from datetime import date
from decimal import Decimal, ROUND_HALF_UP
import hashlib
import json
from pathlib import Path

from playwright.sync_api import sync_playwright
from conciliar import BASE,one_file,already_registered,invoice_issues,read_excel,match_zeta
from revisar_lote import source_index
from xml_source import preview_net_lines,ignored_line_indices
from chrome_connection import connect_chrome,choose_page
from zeta_compras import LIST,refresh_list,prepare_header,select_supplier_exact,set_control,localized,unit_price,input_price,adapt_zeta_rounding,read_totals,confirm_once,SupplierApprovalRequired,validate_supplier_catalog
from zeta_ui_runtime import wait_path,wait_value,select_article,prompt_frame,open_prompt,click_readonly,select_prompt_result
from informe_pendientes import write_report
from search_scope import invoice_search_period
from zeta_bot import exclusive_lock,save_state,append_ledger,unresolved_attempts,stamp

REPORT=BASE/'salida/lote/carga.json'
PROFILES={'Compra Crédito':{'code':'21','destination':'article','editor':'lineacompras'},
          'Compra Crédito Gastos':{'code':'26','destination':'concept','editor':'lineagastos'}}
ROUNDING_TOLERANCE=Decimal('1.00')


def rounding_differences(actual,expected):
    differences={key:Decimal(str(actual[key]))-Decimal(str(value)) for key,value in expected.items()}
    if any(not value.is_finite() or abs(value)>ROUNDING_TOLERANCE for value in differences.values()):
        raise ValueError('La diferencia de Zeta supera la tolerancia de redondeo de 1,00 por comprobante.')
    return {key:str(value) for key,value in differences.items() if value}


def allow_rounding(plan):
    plan=deepcopy(plan)
    actual={key:Decimal(0) for key in ('neto','iva','total')}
    expected={key:Decimal(0) for key in actual}
    for line in plan['lineas']:
        price=unit_price(line);quantity=Decimal(line['cantidadXML'])
        base=(price*quantity).quantize(Decimal('.01'),rounding=ROUND_HALF_UP)
        rate={'1':Decimal('0'),'2':Decimal('.10'),'3':Decimal('.22')}[line['indicadorIVA']]
        tax=(base*rate).quantize(Decimal('.01'),rounding=ROUND_HALF_UP)
        for key,value,field in [('neto',base,'baseEsperada'),('iva',tax,'ivaEsperado'),('total',base+tax,'totalEsperado')]:
            actual[key]+=value;expected[key]+=Decimal(line[field])
        rounding_differences(actual,expected)
        line['toleranciaRedondeo']=str(ROUNDING_TOLERANCE)
    plan['toleranciaRedondeo']=str(ROUNDING_TOLERANCE)
    plan['diferenciasRedondeoPrevistas']=rounding_differences(actual,plan.get('totalesXML',expected))
    return plan


def checksum(path):return hashlib.sha256(path.read_bytes()).hexdigest()


def save_operation_state(path,state):
    # Journal de operaciones; no conservar el XML ni el JSON de conversión.
    save_state(path,{key:value for key,value in state.items() if key!='plan'})


def public_result(item):
    if item['estado']=='CONFIRMADO':return 'CARGADA'
    if item['estado'] in {'YA_CONFIRMADO_LOCAL','RESULTADOS_EN_ZETA_NO_CARGAR'}:return 'YA CARGADA'
    if item['estado']=='APTO_PENDIENTE_GUARDADO':return 'NO CARGADA (PRUEBA SIN GUARDAR)'
    return 'NO CARGADA'


def incomplete_operation(key):
    path=BASE/'estado/facturas'/(hashlib.sha256(key.encode()).hexdigest()+'.json')
    if not path.exists():return None
    state=json.loads(path.read_text(encoding='utf-8'))
    if state.get('estado')!='CONFIRMADO' and state.get('guardado_iniciado'):
        return state
    return None


def reconcile_absent_attempt(page, invoice, source, previous, expected_company):
    """Liberar un intento solo después de dos consultas vacías en la misma empresa."""
    key=invoice['clave']
    if not source or source.get('receptor')!=expected_company['rut']:
        raise ValueError('No se puede verificar el receptor del intento interrumpido.')
    if previous and (previous.get('clave')!=key or previous.get('sha256XML')!=source.get('sha256XML')):
        raise ValueError('El XML no coincide con el intento interrumpido.')
    company=recover_list(page,expected_company)
    absent,proof=absence(page,invoice)
    if not absent or not proof or any(q.get('filas')!=[] for q in proof):
        raise ValueError('No se confirmó la ausencia del intento; revisar su estado en Zeta.')
    path=BASE/'estado/facturas'/(hashlib.sha256(key.encode()).hexdigest()+'.json')
    event={'clave':key,'estado':'AUSENCIA_VERIFICADA','fecha':stamp(),
           'empresa':company,'consultas_actuales':proof,'docid_anterior':(previous or {}).get('docid','')}
    if previous:
        save_operation_state(BASE/'estado/reconciliaciones'/(hashlib.sha256(key.encode()).hexdigest()+'_'+stamp().replace(':','-')+'.json'),previous)
        save_operation_state(path,{**previous,'estado':'AUSENCIA_VERIFICADA',
            'guardado_iniciado':False,'guardado_real':False,'docid':'','lineas_confirmadas':[],
            'reconciliacion':event})
    append_ledger(BASE/'estado/intentos.jsonl',event)
    return event


def recover_list(page,expected_company):
    # Solo navegación/consulta. Nunca reintentar la factura que falló.
    page.goto(LIST,wait_until='load');wait_path(page,'/z.gestion.comprobantes.compras')
    control=refresh_list(page,'01/01/26',date.today().strftime('%d/%m/%y'),control_value('numero'))
    proof=verify_company(page,control)
    if proof['id']!=expected_company['id'] or proof['rut']!=expected_company['rut']:
        raise ValueError('No se pudo restablecer la empresa del lote.')
    return proof


def confirm_supplier_locally(supplier,actual,fingerprints,*,ask=input):
    """Confirmación de identidad local, antes de guardar, sin modificar Zeta."""
    # Un código o RUT contradictorio nunca llega a esta consulta.
    try:validate_supplier_catalog(supplier,actual,{})
    except SupplierApprovalRequired:pass
    else:raise ValueError('Esta confirmación solo corresponde a proveedores sin RUT.')
    print('\nPROVEEDOR SIN RUT EN ZETA',flush=True)
    print(f"XML: {supplier.get('nombre','')} — RUT {supplier['rut']}",flush=True)
    print(f"Zeta: {actual['codigoZeta']} — {actual['nombre']} — RUT vacío",flush=True)
    answer='CONFIRMAR '+actual['codigoZeta']
    try:response=ask(f'Si son el mismo proveedor, escribí {answer}. Enter lo deja pendiente: ')
    except (EOFError,OSError):response=''
    if response.strip()!=answer:raise SupplierApprovalRequired(supplier,actual)
    path=BASE/'configuracion_zeta.json'
    # No aceptar una edición simultánea de las entradas durante la consulta.
    if any(checksum(Path(file))!=digest for file,digest in fingerprints.items()):
        raise ValueError('Una entrada cambió durante la confirmación del proveedor.')
    config=json.loads(path.read_text(encoding='utf-8'))
    approvals=config.setdefault('correlaciones_proveedor_confirmadas',{})
    approvals[supplier['rut']]={'codigoZeta':actual['codigoZeta'],'nombreZeta':actual['nombre'],
        'confirmado_por_usuario':True,'fecha':stamp(),'fuente':'Confirmación escrita en la consola del bot'}
    validate_supplier_catalog(supplier,actual,approvals)
    save_state(path,config)
    fingerprints[str(path)]=checksum(path)
    return approvals


def company_id(page):
    values=page.evaluate("""() => {
        const ids=[];
        for (const [key,value] of Object.entries(gx.O)) {
            if (/EmpId$/.test(key) && typeof value==='string' && value) ids.push(value);
            if (value && typeof value==='object' && value.EmpId) ids.push(String(value.EmpId));
        }
        for (const id of ['vEMPID','EMPID']) { const e=document.getElementById(id);if(e?.value)ids.push(e.value); }
        return [...new Set(ids)];
    }""")
    if len(values)!=1:raise ValueError('Empresa activa ausente o ambigua.')
    return values[0]


def verify_company(page,control):
    rows=[r for r in control['filas'] if r['number']==control_value('numero') and r['supplier']==control_value('proveedor') and r['series']==control_value('serie')]
    if len(rows)!=1:raise ValueError('Control de empresa ambiguo.')
    click_readonly(page.locator('#'+rows[0]['edit']+' > a'));wait_path(page,'/z.gestion.comprobantes.facturacompra')
    rut=page.locator('#vEMPRUT').input_value()
    if rut!=company_rut():raise ValueError('La empresa activa no es la receptora del lote.')
    proof={'rut':rut,'id':company_id(page),'documento_control':rows[0]['docid'],'fecha':stamp()}
    page.goto(LIST,wait_until='load');wait_path(page,'/z.gestion.comprobantes.compras')
    return proof


def plan_for(invoice,source):
    # El centro vacío es una elección válida; nunca se reemplaza por un área.
    invoice=deepcopy(invoice)
    invoice.setdefault('zeta',{})['centroCosto']=invoice.get('zeta',{}).get('centroCosto') or ''
    ignored=ignored_line_indices(source,invoice)
    unsupported=[line for index,line in enumerate(source.get('lineas',[])) if index not in ignored and str(line.get('indicadorIVA','')) not in {'1','2','3'}]
    if unsupported:
        line=unsupported[0]
        raise ValueError('La línea XML «'+line.get('descripcion','')+'» tiene indicador '+str(line.get('indicadorIVA',''))+
            ' sin plan validado. Requiere corregir el bot; no se resuelve cambiando el formulario, el artículo ni el IVA del XML.')
    issues=invoice_issues(invoice)
    if issues:raise ValueError('; '.join(issues))
    profile=PROFILES.get(invoice['zeta']['comprobante'])
    if not profile:
        exempt=any(l.get('indicadorIVA')=='1' for l in invoice['lineas'])
        raise ValueError(('IVA exento (0%) reconocido en el XML. ' if exempt else '')+
                         'Formulario todavía no validado: '+invoice['zeta']['comprobante']+'. No requiere cambiar el IVA del XML.')
    if invoice['cfe']['moneda']!='UYU':raise ValueError('Moneda sin flujo validado.')
    if any(l['tipoDestino']!=profile['destination'] for l in invoice['lineas']):
        raise ValueError('Destino contable distinto del formulario específico.')
    plan=preview_net_lines(source,invoice,company_rut(),rounding_tolerance=ROUNDING_TOLERANCE)
    if profile['destination']=='article':
        try:exact_plan=adapt_zeta_rounding(plan)
        except RuntimeError:exact_plan=plan
        plan=allow_rounding(exact_plan)
    else:plan=allow_rounding(plan)
    for line in plan['lineas']:unit_price(line)
    return invoice,plan,profile


def absence(page,invoice):
    start,end=invoice_search_period(invoice['cfe']['fecha'],today=date.today())
    query=refresh_list(page,start,end,str(invoice['cfe']['numero']),require_complete=False)
    return not bool(query['filas']),[query]


def select_expense_concept(page,line):
    # Enlace y catálogo reales; no asignar IDs de conceptos directamente.
    selector='a[href*="z.gestion.configuracion.conceptosgastosprompt"]'
    link=page.locator(selector)
    if link.count()!=1:raise ValueError('Buscador de conceptos ausente o ambiguo.')
    href=link.get_attribute('href') or ''
    if not href.startswith("javascript:gx.popup.openPrompt('z.gestion.configuracion.conceptosgastosprompt'"):
        raise ValueError('Enlace de conceptos diferente al capturado.')
    link.evaluate("element => { const href=element.getAttribute('href'); new Function(href.slice(11)).call(element); }")
    frame=prompt_frame(page)
    set_control(frame,'#vFILTERFULLTEXT',line['descripcionZeta'])
    frame.evaluate("gx.O.executeServerEvent('REFRESH',true,null,false,false)")
    row=frame.locator('#GridContainerTbl tbody tr').filter(has=frame.get_by_text(line['codigoZeta'],exact=True)).filter(has=frame.get_by_text(line['descripcionZeta'],exact=True))
    wait_value(lambda:row.count(),lambda count:count==1,'Concepto ausente o ambiguo en el catálogo.')
    expected=line['codigoZeta']+' - '+line['descripcionZeta']
    select_prompt_result(row.locator('[id^=span_vSELECT] > a'),
        lambda:page.locator('#CONCEPTOIDLIN').input_value(),lambda text:text==expected,'Concepto seleccionado distinto de Cerecito.')


def save_invoice(page,invoice,plan,profile,state,state_path):
    # Ningún reintento: una pérdida de respuesta bloquea solo esta factura.
    state['guardado_iniciado']=True
    confirm_once(page,state,'cabecera',state_path=state_path)
    wait_path(page,'/z.gestion.comprobantes.'+profile['editor'])
    docid=page.locator('#DOCID').input_value()
    if not docid.isdigit() or int(docid)==0:raise ValueError('Cabecera sin ID persistido.')
    state.update(docid=docid,guardado_real=True,estado='CABECERA_CONFIRMADA');save_operation_state(state_path,state)
    finish_lines(page,invoice,plan,profile,state,state_path)


def apply_line_tax(page,line,profile):
    indicator=line['indicadorIVA']
    if indicator=='1':
        options=page.locator('#IVAIDLIN option').evaluate_all("options=>options.map(o=>({value:o.value,text:o.textContent.trim()}))")
        if not any(o['value']=='0' and o['text'] in {'-','0 -','0 - Exento','Exento'} for o in options):
            raise ValueError('La opción de IVA sin impuesto cambió en Zeta; no se confirma la línea exenta.')
        set_control(page,'#IVAIDLIN','0')
        if page.locator('#IVAIDLIN').input_value()!='0':raise ValueError('Zeta no seleccionó IVA cero para la línea exenta.')
    elif profile['destination']=='concept':
        value,label={'2':('3','3 - IVA 10% Gastos'),'3':('4','4 - IVA 22% Gastos')}[indicator]
        options=page.locator('#IVAIDLIN option').evaluate_all("options=>options.map(o=>({value:o.value,text:o.textContent.trim()}))")
        if not any(o=={'value':value,'text':label} for o in options):raise ValueError('IVA de gastos diferente del catálogo verificado.')
        set_control(page,'#IVAIDLIN',value)
    elif page.locator('#IVAIDLIN').input_value()!= {'2':'1','3':'2'}[indicator]:
        raise ValueError('IVA del artículo '+line['codigoZeta']+' diferente del XML: no se confirma la línea.')


def finish_lines(page,invoice,plan,profile,state,state_path):
    docid=state['docid']
    for index,line in enumerate(plan['lineas']):
        if index:
            click_readonly(page.locator('#BTNENTER'));wait_path(page,'/z.gestion.comprobantes.'+profile['editor'])
        if page.locator('#DOCID').input_value()!=docid:raise ValueError('El editor pertenece a otro comprobante.')
        if profile['destination']=='article':select_article(page,line['codigoZeta'],line['descripcionZeta'])
        else:select_expense_concept(page,line)
        mode=page.locator('#PRVIVAINCDOC').input_value() if profile['destination']=='article' else 'N'
        for selector,value in [('#LINCONCEPTO',line['descripcionXML'][:50]),('#LINCANTIDAD',localized(line['cantidadXML'])),
                               ('#LINPRECIO1',localized(input_price(line,mode))),('#LINDTO1','0'),('#LINDTO2','0'),('#LINDTO3','0')]:
            if page.locator(selector).count():set_control(page,selector,value)
            elif selector not in {'#LINDTO1','#LINDTO2','#LINDTO3'}:raise ValueError('Campo ausente en el formulario: '+selector)
        apply_line_tax(page,line,profile)
        if profile['destination']=='article' and page.locator('#PRVIVAINCDOC').input_value()!=mode:raise ValueError('La modalidad de importes cambió antes de confirmar.')
        confirm_once(page,state,'linea_'+str(index+1),state_path=state_path)
        wait_path(page,'/z.gestion.comprobantes.facturacompra')
        page.reload(wait_until='load');wait_path(page,'/z.gestion.comprobantes.facturacompra')
        totals=read_totals(page);prefix=plan['lineas'][:index+1]
        expected={key:sum((Decimal(l[field]) for l in prefix),Decimal(0)) for key,field in [('neto','baseEsperada'),('iva','ivaEsperado'),('total','totalEsperado')]}
        if index==len(plan['lineas'])-1:expected=plan.get('totalesXML',expected)
        differences=rounding_differences(totals,expected)
        state['diferencias_redondeo']=differences
        state['lineas_confirmadas'].append({'indice':index,'totales':totals})
        state.update(estado='LINEA_CONFIRMADA',totales=totals);save_operation_state(state_path,state)
    state.pop('motivo',None)
    state.update(estado='CONFIRMADO',fin=stamp());save_operation_state(state_path,state)
    append_ledger(BASE/'estado/intentos.jsonl',{'clave':state['clave'],'estado':'CONFIRMADO','docid':docid,'fecha':stamp()})


def run(save=False,interactive=False,payload=None):
    report={'inicio':stamp(),'guardado_real':False,'modo':'GUARDAR' if save else 'SOLO_REVISAR','comprobantes':[]}
    invoices=[]
    try:
        with exclusive_lock(BASE/'estado/bot.lock'):
            if payload is None:
                input_path=one_file(BASE/'entrada/cerecito','.json');input_hash=checksum(input_path)
                payload=json.loads(input_path.read_text(encoding='utf-8-sig'))
            else:
                input_path=None;input_hash=hashlib.sha256(json.dumps(payload,sort_keys=True).encode()).hexdigest()
            sources=source_index()
            invoices=deepcopy(payload['comprobantes'])
            keys=[i['clave'] for i in invoices]
            if not keys:raise ValueError('Lote vacío.')
            repeated={key for key,count in Counter(keys).items() if count>1}
            history,_=read_excel(one_file(BASE/'entrada/zeta','.xlsx'))
            input_files=([input_path] if input_path else [])+[one_file(BASE/'entrada/zeta','.xlsx'),BASE/'configuracion_zeta.json']+[p for p in (BASE/'entrada/xml').iterdir() if p.suffix.lower() in {'.xml','.zip'}]
            fingerprints={str(p):checksum(p) for p in input_files}
            # Confirmados locales no deciden duplicados: consultar Zeta siempre.
            uncertain=unresolved_attempts(BASE/'estado/intentos.jsonl')
            config=json.loads((BASE/'configuracion_zeta.json').read_text(encoding='utf-8'))
            if save and not config.get('carga_lote',{}).get('habilitada'):raise ValueError('Carga del lote deshabilitada en configuración.')
            report.update(total=len(invoices),sha256Cerecito=input_hash)
            with sync_playwright() as pw:
                browser=connect_chrome(pw,config);page=choose_page(browser,config)
                page.goto(LIST,wait_until='load');wait_path(page,'/z.gestion.comprobantes.compras')
                control=refresh_list(page,'01/01/26',date.today().strftime('%d/%m/%y'),control_value('numero'))
                if not any(r['number']==control_value('numero') and control_value('nombre') in r['text'] for r in control['filas']):raise ValueError('El buscador no pasa el control positivo; no se demuestra ausencia.')
                report['control_positivo']=control
                report['empresa_verificada']=verify_company(page,control)
                for number,invoice in enumerate(invoices,1):
                    key=invoice['clave'];item={'clave':key,'proveedor':invoice['proveedor']['nombre'],'numero':invoice['cfe']['numero'],
                        'totalXML':invoice['cfe']['total'],'moneda':invoice['cfe']['moneda'],'formulario':invoice['zeta'].get('comprobante',''),
                        'centroCosto':invoice['zeta'].get('centroCosto') or '','estado':'REVISANDO','guardado_real':False}
                    report['comprobantes'].append(item);save_state(REPORT,report)
                    print(f'[{number}/{len(invoices)}] '+item['proveedor']+' '+key,flush=True)
                    try:
                        if key in repeated:
                            item.update(estado='BLOQUEADO_IDENTIDAD',motivo='Identidad repetida en el JSON.');continue
                        raw=str(invoice['cfe']['numero'])
                        if not raw.isdigit() or int(raw)==0:
                            item.update(estado='BLOQUEADO_IDENTIDAD',motivo='Número fiscal ausente, cero o inválido.');continue
                        invoice['cfe']['numero']=str(int(raw))
                        if invoice.get('excluido'):
                            item.update(estado='EXCLUIDO',motivo='Comprobante excluido en Cerecito.');continue
                        if str(invoice['cfe']['tipo']) in {'181','182','183'}:
                            item.update(estado='DOCUMENTO_NO_COMPRA',motivo='El XML es un remito o resguardo; no corresponde cargarlo como factura de compra.');continue
                        absent,proof=absence(page,invoice);item['consultas_zeta']=proof
                        pending_state=incomplete_operation(key)
                        if not absent:
                            exact=[r for p in proof for r in p['filas'] if r['number']==str(invoice['cfe']['numero']) and r['series']==invoice['cfe']['serie'] and r['supplier']==invoice['proveedor'].get('codigoZeta')]
                            if exact and (pending_state or key in uncertain):
                                item.update(estado='BLOQUEADO_INCIERTO',guardado_parcial=True,
                                    docid=(pending_state or {}).get('docid') or exact[0].get('docid',''),
                                    motivo='Existe un registro de una carga interrumpida; puede estar incompleto. No se anuncia como cargado ni se repite el guardado.');continue
                            item['estado']='RESULTADOS_EN_ZETA_NO_CARGAR' if exact else 'BLOQUEADO_COINCIDENCIAS'
                            if not exact:item['motivo']='La búsqueda devuelve registros que no identifican unívocamente este emisor, serie y número; revisar antes de cargar.'
                            continue
                        if key in uncertain or pending_state:
                            item['recuperacion_intento']=reconcile_absent_attempt(
                                page,invoice,sources.get(key),pending_state,report['empresa_verificada'])
                            uncertain.discard(key)
                        if any(match_zeta(invoice,row)[0] in {'seguro','revisar'} for row in history):
                            item.update(estado='BLOQUEADO_HISTORICO',motivo='Posible registro en el Excel aunque no aparece en la consulta actual: revisar antes de recrear.');continue
                        try:
                            source=sources.get(key)
                            if not source:raise ValueError('Falta el XML original.')
                            prepared,plan,profile=plan_for(invoice,source)
                        except ValueError as error:
                            item.update(estado='BLOQUEADO_DATOS_O_FORMULARIO',motivo=str(error));continue
                        item.update(estado='APTO_PENDIENTE_GUARDADO',centroCosto=prepared['zeta']['centroCosto'])
                        if not save:continue
                        state_path=BASE/'estado/facturas'/(hashlib.sha256(key.encode()).hexdigest()+'.json')
                        if state_path.exists() and json.loads(state_path.read_text(encoding='utf-8')).get('estado')!='CONFIRMADO' and json.loads(state_path.read_text(encoding='utf-8')).get('guardado_iniciado'):
                            item.update(estado='BLOQUEADO_INCIERTO',motivo='Estado persistido con guardado iniciado.');continue
                        state={'clave':key,'sha256XML':plan['sha256XML'],'estado':'PREPARANDO','guardado_iniciado':False,'guardado_real':False,'lineas_confirmadas':[],'lineas_ignoradas':plan.get('lineasIgnoradas',[]),'plan':plan,'inicio':stamp()}
                        try:
                            # La ausencia se consulta de nuevo justo antes de abrir el alta.
                            absent,proof=absence(page,prepared)
                            if not absent:raise ValueError('El comprobante apareció antes del guardado.')
                            if any(checksum(Path(path))!=digest for path,digest in fingerprints.items()):raise ValueError('Una entrada cambió durante el lote; no se guarda con datos mezclados.')
                            save_operation_state(state_path,state)
                            if interactive:
                                prepare_header(page,prepared,prepared['zeta']['comprobante'],
                                    confirm_supplier=lambda supplier,actual:confirm_supplier_locally(supplier,actual,fingerprints))
                            else:prepare_header(page,prepared,prepared['zeta']['comprobante'])
                            if any(checksum(Path(path))!=digest for path,digest in fingerprints.items()):
                                raise ValueError('Una entrada cambió antes de confirmar la cabecera; no se guarda.')
                            if company_id(page)!=report['empresa_verificada']['id']:raise ValueError('La empresa cambió antes de confirmar.')
                            save_invoice(page,prepared,plan,profile,state,state_path)
                            item.update(estado='CONFIRMADO',docid=state['docid'],guardado_real=True,totales=state['totales'],diferencias_redondeo=state.get('diferencias_redondeo',{}))
                            report['guardado_real']=True
                            page.goto(LIST,wait_until='load');wait_path(page,'/z.gestion.comprobantes.compras')
                        except Exception as error:
                            if state.get('estado')=='CONFIRMADO':
                                item.update(estado='CONFIRMADO',docid=state['docid'],guardado_real=True,totales=state['totales'],revision_posterior=str(error).splitlines()[0][:300])
                                report['guardado_real']=True
                                raise
                            state.update(estado='INCIERTO' if state['guardado_iniciado'] else 'BLOQUEADO',motivo=str(error).splitlines()[0][:300])
                            save_operation_state(state_path,state);item.update(estado=state['estado'],motivo=state['motivo'])
                            if state.get('guardado_real') or state.get('docid'):
                                item.update(guardado_parcial=True,docid=state.get('docid',''))
                            if state['guardado_iniciado']:
                                append_ledger(BASE/'estado/intentos.jsonl',{'clave':key,'estado':'INCIERTO','fecha':stamp()})
                                raise
                            item['estado']='BLOQUEADO_PREPARACION'
                            if isinstance(error,SupplierApprovalRequired):
                                item.update(estado='BLOQUEADO_CONFIRMACION_PROVEEDOR',proveedor_xml=error.supplier,proveedor_zeta=error.actual,
                                    motivo='Proveedor con RUT vacío en Zeta. Ejecutar INICIAR_BOT.bat y confirmar la identidad en la ventana del bot; Enter deja la factura pendiente.')
                            raise
                    except Exception as error:
                        if item['estado'] not in {'CONFIRMADO','INCIERTO','BLOQUEADO_PREPARACION','BLOQUEADO_CONFIRMACION_PROVEEDOR'}:
                            item.update(estado='BLOQUEADO_ERROR',motivo=str(error).splitlines()[0][:300])
                        try:
                            item['recuperacion']=recover_list(page,report['empresa_verificada'])
                        except Exception as recovery_error:
                            raise RuntimeError('La conexión o empresa no permite continuar: '+str(recovery_error).splitlines()[0][:200]) from recovery_error
                    finally:
                        item['resultado']=public_result(item)
                        print(item['resultado']+': '+item['proveedor']+' '+key,flush=True)
                        save_state(REPORT,report)
            report['resumen']=dict(Counter(i['estado'] for i in report['comprobantes']))
            blocked=sum(v for k,v in report['resumen'].items() if k.startswith('BLOQUEADO') or k=='INCIERTO')
            pending=report['resumen'].get('APTO_PENDIENTE_GUARDADO',0)
            report['estado']='LOTE_CON_BLOQUEADOS_O_PENDIENTES' if blocked or pending else 'LOTE_PROCESADO'
            report['resumen_resultados']=dict(Counter(i['resultado'] for i in report['comprobantes']))
            print('Resultado del lote: '+json.dumps(report['resumen_resultados'],ensure_ascii=False),flush=True)
            print('Detalle de pendientes: '+str((REPORT.parent/'carga.csv').resolve()),flush=True)
            return 0
    except Exception as error:
        present={i['clave'] for i in report['comprobantes']}
        for invoice in invoices:
            if invoice['clave'] not in present:
                report['comprobantes'].append({'clave':invoice['clave'],'proveedor':invoice.get('proveedor',{}).get('nombre',''),
                    'estado':'BLOQUEADO_SIN_CONEXION','resultado':'NO CARGADA','guardado_real':False,'motivo':'No se pudo continuar la conexión segura con Zeta.'})
        report.update(estado='DETENIDO',motivo=str(error).splitlines()[0][:300]);print('LOTE DETENIDO: '+report['motivo'],flush=True)
        return 1
    finally:
        report['fin']=stamp()
        report['resumen_resultados']=dict(Counter(i.get('resultado',public_result(i)) for i in report['comprobantes']))
        save_state(REPORT,report)
        with (REPORT.parent/'carga.csv').open('w',encoding='utf-8-sig',newline='') as file:
            fields=['resultado','estado','proveedor','clave','numero','totalXML','moneda','formulario','centroCosto','docid','motivo']
            writer=csv.DictWriter(file,fieldnames=fields,delimiter=';',extrasaction='ignore');writer.writeheader();writer.writerows(report['comprobantes'])
        print('Informe: '+str(REPORT.resolve()),flush=True)
        print('Resultado: '+str(write_report(report,invoices,REPORT.parent).resolve()),flush=True)


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--guardar',action='store_true')
    parser.add_argument('--confirmar-correlaciones',action='store_true',help='Permitir confirmación escrita de proveedores sin RUT antes de guardar.')
    args=parser.parse_args();raise SystemExit(run(args.guardar,args.confirmar_correlaciones))

