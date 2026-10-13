"""Conversión XML→JSON con el Cerecito local y reglas deterministas originales."""

from runtime_paths import DATA_DIR, CODE_DIR, APP_DIR, APP_URL, company_rut, control_value, category_provider_rut
from datetime import datetime
import hashlib
import json
from pathlib import Path

from playwright.sync_api import sync_playwright
from conciliar import BASE
from chrome_connection import websocket_endpoint
from cdp_transport import list_targets, ScopedCDP
from recibir_archivos import activate
from xml_source import read_xml_documents, read_zip_documents
from zeta_bot import exclusive_lock


def documents(paths):
    result = {}
    for path in paths:
        if path.suffix.lower() == '.zip':
            records = [(s['archivo'], raw, s) for raw,s in read_zip_documents(path)]
        else:
            raw = path.read_bytes()
            records = [(s['archivo'], text, s) for text,s in read_xml_documents(raw,path.name)]
        for name, raw, source in records:
            previous = result.get(source['clave'])
            if previous and previous['fiscal']['sha256CFE'] != source['sha256CFE']:
                raise ValueError('Dos XML distintos comparten identidad: ' + source['clave'])
            if previous:continue
            result[source['clave']] = {'text': raw.decode('utf-8-sig'), 'source': path.name + ' / ' + name, 'fiscal': source}
    if not result:
        raise ValueError('No hay XML de facturas para convertir.')
    return list(result.values())


def local_page(pw, endpoint):
    app = APP_DIR / 'index.html'
    if not app.is_file():
        raise ValueError('No se encuentra Cerecito local.')
    url = APP_URL
    endpoint = websocket_endpoint(endpoint)
    targets, ws = list_targets(endpoint, keep_connection=True)
    bridge = None
    try:
        candidates = [t for t in targets if t['type'] == 'page' and t['url'] == url]
        if len(candidates) > 1:
            raise ValueError('Hay más de una pestaña del Cerecito local; no se elige una arbitrariamente.')
        if candidates:
            target_id = candidates[0]['targetId']
        else:
            ws.send_json({'id': 2, 'method': 'Target.createTarget', 'params': {'url': url}})
            while True:
                answer = ws.receive()
                if answer.get('id') == 2:
                    if 'error' in answer:
                        raise ValueError('Chrome no pudo abrir Cerecito local.')
                    target_id = answer['result']['targetId']
                    break
        bridge = ScopedCDP(endpoint, target_id, initial_websocket=ws)
        browser = pw.chromium.connect_over_cdp(bridge.url, no_defaults=True, timeout=45000)
        pages = [p for c in browser.contexts for p in c.pages]
        if len(pages) != 1:
            raise ValueError('No se identificó Cerecito local en la conexión.')
        page = pages[0]
        page.wait_for_function("typeof parseCFE === 'function' && typeof exportAgentJSON === 'function'", timeout=15000, polling=100)
        return page, bridge
    except Exception:
        if bridge:
            bridge.close()
        else:
            ws.close()
        raise


def merge_saved_correlations(payload,previous,settings):
    """Solo las ediciones explícitas de Cerecito sustituyen un JSON revisado."""
    old={i['clave']:i for i in (previous or {}).get('comprobantes',[])}
    providers=settings.get('providerOverrides',{})
    states=settings.get('invoiceStates',{})
    rules=settings.get('manualRules',{})
    edited_ruts={key.split('|',1)[0] for key in rules}
    output=[]
    for invoice in payload['comprobantes']:
        key=invoice['clave'];rut=invoice['proveedor']['rut']
        if key in old and rut not in providers and key not in states and rut not in edited_ruts:
            # Conserva correlaciones recibidas en JSON que no se editaron en la web.
            if old[key].get('cfe')!=invoice.get('cfe'):
                raise ValueError('Los datos fiscales del JSON revisado difieren del XML: '+key)
            output.append(old[key])
        else:output.append(invoice)
    payload['comprobantes']=output
    return payload


def convert(paths, *, activate_json=True, preserve_payload=None,preserve_explicit=False,memory_only=False):
    items = documents(paths)
    config = json.loads((BASE / 'configuracion_zeta.json').read_text(encoding='utf-8-sig'))
    folder = BASE / 'salida/cerecito'
    folder.mkdir(parents=True, exist_ok=True)
    relations = list((BASE / 'entrada/correlaciones').glob('*.json'))
    if len(relations) > 1:
        raise ValueError('Hay varias versiones de correlaciones activas.')
    rules = json.loads(relations[0].read_text(encoding='utf-8-sig')) if relations else None
    with sync_playwright() as pw:
        page, bridge = local_page(pw, config['chrome']['endpoint'])
        try:
            snapshot = page.evaluate("""() => ({manualRules, providerOverrides:rel.providers,
                invoiceStates})""")
            exported = page.evaluate("""({items, rules}) => {
                const parsed = items.map(x => {
                    const c = parseCFE(x.text,x.source);
                    if (!c || cfeKey(c) !== x.fiscal.clave) throw new Error('Identidad XML no coincide en Cerecito');
                    return c;
                });
                if (rules) {
                    if (rules.version !== 3 || !rules.manualRules || !rules.providerOverrides)
                        throw new Error('Formato de correlaciones no reconocido');
                    for (const [k,v] of Object.entries(rules.manualRules)) {
                        if (manualRules[k] && JSON.stringify(manualRules[k]) !== JSON.stringify(v))
                            throw new Error('Correlaciones distintas para la misma regla; revisar antes de importar');
                    }
                    for (const [k,v] of Object.entries(rules.providerOverrides)) {
                        if (rel.providers[k] && JSON.stringify(rel.providers[k]) !== JSON.stringify(v))
                            throw new Error('Valores de proveedor distintos; revisar antes de importar');
                    }
                    manualRules = {...manualRules,...rules.manualRules};
                    rel.providers = {...rel.providers,...rules.providerOverrides};
                    if (!storageSet(STORE_RULES,manualRules) || !storageSet(STORE_REL,rel))
                        throw new Error('No se pudieron conservar correlaciones');
                }
                // Cada ejecución usa solo los XML actuales. No cargar ni persistir CFE previos.
                storageRemove(STORE_LOAD);
                state.invoices = parsed;
                state.duplicates = 0;
                render();
                const original = downloadText;
                let output = null;
                try { downloadText = (name,text) => { output=text; }; exportAgentJSON(); }
                finally { downloadText=original; }
                if (!output) throw new Error('Cerecito no produjo JSON');
                return output;
            }""", {'items': items, 'rules': rules})
        finally:
            bridge.close()  # No cierra Chrome ni pestañas.
    payload = json.loads(exported)
    keyed = {d['clave']: d for d in payload['comprobantes']}
    if len(keyed) != len(payload['comprobantes']):
        raise ValueError('Identidad repetida en el JSON generado.')
    wanted = {x['fiscal']['clave'] for x in items}
    if not wanted.issubset(keyed):
        raise ValueError('Cerecito omitió comprobantes de la entrada.')
    payload['comprobantes'] = [keyed[x['fiscal']['clave']] for x in items]
    if preserve_payload and not memory_only:
        payload=merge_saved_correlations(payload,preserve_payload,{} if preserve_explicit else snapshot)
    payload['total'] = len(items)
    payload['conversion'] = {'motor': 'cerecito-local', 'xml': [x['fiscal'] for x in items],
                             'correlaciones_importadas': bool(rules), 'guardado_real': False}
    if memory_only:
        print(f'Cerecito: {len(items)} documentos leídos de nuevo; JSON solo en memoria.',flush=True)
        return payload
    raw = json.dumps(payload, ensure_ascii=False, indent=2).encode('utf-8')
    target = folder / ('cerecito_para_agente_' + hashlib.sha256(raw).hexdigest()[:16] + '.json')
    target.write_bytes(raw)
    if activate_json:
        activate(target, BASE / 'entrada', BASE / 'salida', 'cerecito', copy=True)
    print('JSON generado por Cerecito local:', str(target.resolve()), flush=True)
    for invoice in payload['comprobantes']:
        if invoice['clave'].endswith('|A|293131'):
            print('Mabipark: estado=' + invoice['estado'] + ', completo=' + str(invoice['completo']), flush=True)
    return target


def main():
    receipt_path = BASE / 'salida/recepcion/ultima.json'
    receipt = json.loads(receipt_path.read_text(encoding='utf-8')) if receipt_path.exists() else {'archivos': []}
    explicit=any(x['grupo']=='cerecito' for x in receipt['archivos'])
    if explicit:print('Se conservan las correlaciones del JSON recibido y se incorporan los XML que falten.')
    # Incluir también XML anteriores: un ZIP nuevo no debe ocultar pendientes.
    paths = sorted(p for p in (BASE / 'entrada/xml').iterdir() if p.suffix.lower() in {'.zip','.xml'})
    active=list((BASE/'entrada/cerecito').glob('*.json'))
    if len(active)>1:raise ValueError('Hay varias versiones de JSON activas.')
    current=json.loads(active[0].read_text(encoding='utf-8-sig')) if active else None
    with exclusive_lock(BASE / 'estado/conversion.lock'):
        # Leer siempre las correlaciones guardadas, incluso con el mismo lote XML.
        convert(paths,preserve_payload=current,preserve_explicit=explicit)
    return 0


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except Exception as exc:
        # Las trazas de navegador pueden contener URLs con sesiones.
        import re
        message = re.sub(r'(https?://[^\s?]+)\?\S+', r'\1?[oculto]', str(exc).splitlines()[0])[:500]
        folder = BASE / 'salida/cerecito'
        folder.mkdir(parents=True, exist_ok=True)
        (folder / 'conversion_bloqueada.json').write_text(json.dumps({'estado':'BLOQUEADO','motivo':message,'guardado_real':False}, ensure_ascii=False, indent=2), encoding='utf-8')
        print('CONVERSIÓN BLOQUEADA:', message)
        raise SystemExit(1)
