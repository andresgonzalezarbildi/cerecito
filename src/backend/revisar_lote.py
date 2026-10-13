"""Revisa todos los comprobantes del lote; nunca confirma ni guarda en Zeta."""

from runtime_paths import DATA_DIR, CODE_DIR, APP_DIR, APP_URL, company_rut, control_value, category_provider_rut
import argparse
from collections import Counter
from datetime import date
import json

from playwright.sync_api import sync_playwright
from conciliar import BASE, run as reconcile, write_csv
from chrome_connection import connect_chrome, choose_page
from zeta_compras import check_duplicates, DuplicateSearchError
from xml_source import read_zip, read_xml_documents, preview_net_lines
from zeta_bot import save_state, stamp

REPORT=BASE/'salida/lote/informe.json'


def source_index():
    index={}
    for path in sorted((BASE/'entrada/xml').iterdir()):
        if path.suffix.lower()=='.zip':sources=read_zip(path)
        elif path.suffix.lower()=='.xml':sources=[s for raw,s in read_xml_documents(path.read_bytes(),path.name)]
        else:continue
        for source in sources:
            previous=index.get(source['clave'])
            if previous and previous['sha256CFE']!=source['sha256CFE']:
                raise ValueError('XML distintos con igual identidad: '+source['clave'])
            index.setdefault(source['clave'],source)
    return index


def fiscal_review(row,sources):
    result={'clave':row['clave'],'estado_conciliacion':row['estado'],
            'motivos':list(row['motivos']),'proveedor':row['datos']['proveedor'],
            'cfe':row['datos']['cfe'],'zeta':row['datos']['zeta'],'guardado_real':False}
    if row['estado']!='PENDIENTE':return result
    source=sources.get(row['clave'])
    try:
        if not source:raise ValueError('No se encuentra XML original con esta identidad.')
        result['plan_fiscal']=preview_net_lines(source,row['datos'],company_rut())
        result['estado_carga']='REQUIERE_VALIDAR_FORMULARIO_Y_AUTORIZAR_GUARDADO'
    except ValueError as error:
        result['estado_carga']='BLOQUEADA_FISCALMENTE'
        result['motivos'].append(str(error))
    return result


def run(live=False):
    rows=reconcile()
    sources=source_index()
    reviewed=[fiscal_review(row,sources) for row in rows]
    report={'fecha':stamp(),'guardado_real':False,'consulta_zeta':'NO_EJECUTADA',
            'total':len(rows),'resumen':dict(Counter(r['estado'] for r in rows)),
            'comprobantes':reviewed}
    queue=json.loads((BASE/'salida/cola.json').read_text(encoding='utf-8'))
    report.update(sha256Cerecito=queue['sha256Cerecito'],sha256Zeta=queue['sha256Zeta'])
    if live:
        try:
            config=json.loads((BASE/'configuracion_zeta.json').read_text(encoding='utf-8'))
            with sync_playwright() as pw:
                browser=connect_chrome(pw,config);page=choose_page(browser,config)
                for row,item in zip(rows,reviewed):
                    if row['estado']!='PENDIENTE':continue
                    print('Comprobando en Zeta: '+row['datos']['proveedor']['nombre']+' '+row['clave'],flush=True)
                    if date.fromisoformat(row['datos']['cfe']['fecha'])>date.today():
                        item['consulta_zeta']={'estado':'BLOQUEADA','motivo':'Fecha futura: ausencia no comprobable.'}
                        continue
                    try:
                        control={}
                        proof=check_duplicates(page,row['datos'],control)
                        item['consulta_zeta']={'estado':'AUSENCIA_VERIFICADA','consultas':proof,'control_positivo':control}
                    except DuplicateSearchError as error:
                        item['consulta_zeta']={'estado':'RESULTADOS_EXISTENTES_REVISAR_IDENTIDAD','consulta':error.query,'control_positivo':control}
                        item['estado_carga']='REQUIERE_REVISAR_ZETA'
                    except Exception as error:
                        item['consulta_zeta']={'estado':'BLOQUEADA','motivo':str(error).splitlines()[0][:300]}
                        item['estado_carga']='REQUIERE_REVISAR_ZETA'
            report['consulta_zeta']='EJECUTADA'
        except Exception as error:
            report['consulta_zeta']='NO_VERIFICADA'
            report['error_consulta']=str(error).splitlines()[0][:300]
    pending=[r for r in reviewed if r['estado_conciliacion']=='PENDIENTE']
    report['pendientes_locales']=len(pending)
    report['candidatos_ausentes_en_zeta']=sum(r.get('consulta_zeta',{}).get('estado')=='AUSENCIA_VERIFICADA' for r in pending)
    report['por_revisar']=sum(r['estado'] in {'REVISAR_DATOS','REVISAR_DUPLICADO','FUERA_PERIODO_ZETA'} for r in rows)
    report['estado']='LOTE_CON_PENDIENTES_O_REVISIONES' if pending or report['por_revisar'] else 'LOTE_SIN_PENDIENTES_LOCALES'
    save_state(REPORT,report)
    if live:
        save_state(REPORT.parent/('consulta_zeta_'+report['fecha'].replace(':','-')+'.json'),report)
    write_csv(REPORT.parent/'comprobantes.csv',rows)
    print('\nLOTE COMPLETO: '+str(len(rows))+' comprobantes; '+str(len(pending))+' candidatos pendientes; '+str(report['por_revisar'])+' requieren revisión.',flush=True)
    for r in pending:
        print(r['proveedor']['nombre']+' '+r['cfe']['serie']+'-'+r['cfe']['numero']+' '+str(r['cfe']['total'])+' '+r['cfe']['moneda']+': '+r['estado_carga'],flush=True)
        for reason in r['motivos']:print('  '+reason,flush=True)
        if r.get('consulta_zeta'):print('  Zeta: '+r['consulta_zeta']['estado'],flush=True)
    print('Informe de todos los comprobantes: '+str(REPORT.resolve()),flush=True)
    return report


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--consultar-zeta',action='store_true')
    args=parser.parse_args()
    run(args.consultar_zeta)

