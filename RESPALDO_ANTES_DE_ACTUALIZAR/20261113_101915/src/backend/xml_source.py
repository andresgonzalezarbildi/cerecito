"""Lectura fiscal determinista de XML originales; nunca modifica Cerecito/Zeta."""

from runtime_paths import DATA_DIR, CODE_DIR, APP_DIR, APP_URL, company_rut, control_value, category_provider_rut
from copy import deepcopy
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
import hashlib
import re
from zipfile import ZipFile
import xml.etree.ElementTree as ET

CENT = Decimal('0.01')
PRECISION = Decimal('0.00001')
VAT_RATES = {'1':Decimal('0'), '2':Decimal('0.10'), '3':Decimal('0.22'), '12':Decimal('0')}
VAT_LABELS = {'1':'Exento / no gravado', '2':'IVA 10%', '3':'IVA 22%', '5':'Entrega gratuita',
              '6':'No facturable (suma)', '7':'No facturable (resta)',
              '11':'Impuesto percibido', '12':'IVA en suspenso', '16':'Indicador fiscal 16'}


def validate_zeta_vat_choice(indicator, choice):
    if not isinstance(choice,str) or choice not in {'1','2','3'}:
        raise SourceError('Elegí Sin IVA, 10% o 22% para Zeta.')
    if indicator not in VAT_RATES or VAT_RATES[indicator]!=VAT_RATES[choice]:
        raise SourceError('El IVA elegido en Zeta cambia el impuesto del XML. Revisá la tasa de '+VAT_LABELS.get(indicator,indicator)+'.')


class SourceError(ValueError):
    pass


def child(parent, name, required=True):
    nodes = [] if parent is None else parent.findall('{*}' + name)
    if len(nodes) > 1 or (required and len(nodes) != 1):
        raise SourceError('Campo XML ausente o repetido: ' + name)
    return nodes[0] if nodes else None


def value(parent, name, default=None):
    node = child(parent, name, required=default is None)
    return (node.text or '').strip() if node is not None else default


def decimal_value(text):
    try:
        number = Decimal(str(text))
    except (InvalidOperation, ValueError):
        raise SourceError('Importe XML inválido.') from None
    if not number.is_finite():
        raise SourceError('Importe XML no finito.')
    return number


def cents(number):
    return decimal_value(number).quantize(CENT, rounding=ROUND_HALF_UP)


def parse_xml(content, filename=''):
    if len(content) > 8 * 1024 * 1024 or b'<!DOCTYPE' in content.upper() or b'<!ENTITY' in content.upper():
        raise SourceError('XML demasiado grande o con declaraciones de entidades.')
    root = ET.fromstring(content)
    nodes = [root] if root.tag.split('}')[-1] == 'CFE' else root.findall('.//{*}CFE')
    if len(nodes) != 1:
        raise SourceError('Se requiere un único CFE por XML.')
    headers = nodes[0].findall('.//{*}Encabezado')
    details = nodes[0].findall('.//{*}Detalle')
    if len(headers) != 1 or len(details) != 1:
        raise SourceError('Encabezado o detalle ausente/repetido.')
    header = headers[0]
    ident, emitter, receiver, totals = [child(header, tag) for tag in ('IdDoc','Emisor','Receptor','Totales')]
    rut = value(emitter,'RUCEmisor')
    target_rut = value(receiver,'DocRecep')
    kind, series, number = [value(ident,tag) for tag in ('TipoCFE','Serie','Nro')]
    if not re.fullmatch(r'\d{12}',rut) or not number.isdigit() or int(number) == 0 or not series:
        raise SourceError('Identidad fiscal incompleta.')
    fiscal = {name:str(decimal_value(value(totals,tag,'0'))) for name,tag in {
        'netoMin':'MntNetoIvaTasaMin','netoBas':'MntNetoIVATasaBasica',
        'noGravado':'MntNoGrv','exento':'MntExe','ivaMin':'MntIVATasaMin',
        'ivaBas':'MntIVATasaBasica','total':'MntTotal'}.items()}
    for name, tag in {'suspenso':'MntIVaenSusp', 'pagar':'MntPagar'}.items():
        amount=value(totals,tag,'')
        if amount:fiscal[name]=str(decimal_value(amount))
    items=[]
    for index,item in enumerate(details[0].findall('{*}Item')):
        if kind=='181' and nodes[0].find('{*}eRem') is not None:
            continue  # Remito observado: no contiene IVA ni importe de compra.
        if kind in {'181','182','183'} and child(item,'RetencPercep',required=False) is not None:
            # Resguardos: retenciones, no líneas de compra con IndFact/MontoItem.
            # Se conservan para conversión; preview_net_lines bloquea estos tipos.
            continue
        items.append({'indice':index,'linea':value(item,'NroLinDet'),
                      'descripcion':value(item,'NomItem',''), 'indicadorIVA':value(item,'IndFact'),
                      'monto':str(decimal_value(value(item,'MontoItem'))),
                      'cantidad':str(decimal_value(value(item,'Cantidad','1')))})
    references = []
    for ref in nodes[0].findall('.//{*}Referencia'):
        references.append({
            'tipo': value(ref, 'TpoDocRef', ''),
            'serie': value(ref, 'Serie', ''),
            'numero': value(ref, 'NroDocRef', ''),
            'global': value(ref, 'IndGlobal', ''),
            'motivo': value(ref, 'RazonRef', ''),
        })
    return {'clave':'|'.join((rut,kind,series,str(int(number)))), 'archivo':filename,
            'sha256CFE':hashlib.sha256(ET.canonicalize(ET.tostring(nodes[0],encoding='unicode'),strip_text=True,rewrite_prefixes=True).encode('utf-8')).hexdigest(),
            'sha256XML':hashlib.sha256(content).hexdigest(),'rut':rut,'receptor':target_rut,
            'dirRecep':value(receiver,'DirRecep',''),
            'nombre':value(emitter,'RznSoc'),'tipo':kind,'serie':series,'numero':str(int(number)),
            'fecha':value(ident,'FchEmis'),'moneda':value(totals,'TpoMoneda','') if kind=='181' else value(totals,'TpoMoneda'),
            'tipoCambio':value(totals,'TpoCambio',''),'mntBruto':value(ident,'MntBruto','0'),'formaPagoXML':value(ident,'FmaPago',''),
            'ajustesGlobales':bool(nodes[0].findall('.//{*}DscRcgGlobal')),
            'totales':fiscal,'lineas':items,'referencias':references,
            'retencionesXML':ET.tostring(nodes[0],encoding='unicode') if kind in {'181','182','183'} else None,
            'alcance':'Identidad y consistencia fiscal; no verifica firma digital ni aceptación por DGI.'}


def read_xml_documents(content, filename=''):
    """Separa sobres; mantiene el hash y nombre del XML original para auditoría."""
    if len(content)>8*1024*1024 or b'<!DOCTYPE' in content.upper() or b'<!ENTITY' in content.upper():
        raise SourceError('XML demasiado grande o con declaraciones de entidades: '+filename)
    root=ET.fromstring(content)
    nodes=[root] if root.tag.split('}')[-1]=='CFE' else root.findall('.//{*}CFE')
    if not nodes:raise SourceError('XML sin CFE: '+filename)
    if len(nodes)>1000:raise SourceError('Demasiados CFE en el XML: '+filename)
    records=[]
    for index,node in enumerate(nodes):
        name=filename if len(nodes)==1 else filename+'#CFE-'+str(index+1)
        raw=content if len(nodes)==1 else ET.tostring(node,encoding='utf-8',xml_declaration=True)
        try:source=parse_xml(raw,name)
        except (SourceError,ET.ParseError) as error:
            raise SourceError(name+': '+str(error)) from error
        source.update(sha256XML=hashlib.sha256(content).hexdigest(),archivoXMLOriginal=filename,indiceCFE=index)
        records.append((raw,source))
    return records


def read_zip_documents(path):
    documents=[]
    seen={}
    with ZipFile(path) as archive:
        files=[i for i in archive.infolist() if i.filename.lower().endswith('.xml')]
        if len(files)>1000 or sum(i.file_size for i in files)>64*1024*1024:
            raise SourceError('ZIP excede los límites de lectura.')
        for info in files:
            if info.file_size>8*1024*1024:
                raise SourceError('XML excesivamente grande.')
            for raw,source in read_xml_documents(archive.read(info), info.filename):
                previous=seen.get(source['clave'])
                if previous:
                    if previous['sha256CFE']!=source['sha256CFE']:
                        raise SourceError('Dos CFE distintos comparten identidad: '+source['clave']+' ('+previous['archivo']+' / '+source['archivo']+')')
                    continue
                seen[source['clave']]=source
                documents.append((raw,source))
    return documents


def read_zip(path):
    return [source for raw,source in read_zip_documents(path)]


def can_ignore_line(line):
    return line['indicadorIVA'] in {'6','7'} or decimal_value(line['monto']) == 0


def ignored_line_indices(source, invoice):
    indices=invoice.get('lineasIgnoradas',[])
    if not isinstance(indices,list) or any(type(index) is not int for index in indices) or len(set(indices))!=len(indices):
        raise SourceError('Selección de líneas ignoradas inválida.')
    for index in indices:
        if index<0 or index>=len(source['lineas']):
            raise SourceError('Línea ignorada fuera del XML.')
        if not can_ignore_line(source['lineas'][index]):
            raise SourceError('Solo se pueden ignorar líneas de importe cero o líneas no facturables (indicadores 6/7).')
    return indices


def valid_credit_note_reference(references):
    """No suponer una factura original cuando el CFE no la identifica."""
    for ref in references:
        number=str(ref.get('numero','')).strip()
        if (str(ref.get('global',''))=='1' and str(ref.get('motivo','')).strip()):
            return True
        if (str(ref.get('tipo','')).strip() and str(ref.get('serie','')).strip()
                and number.isdigit() and int(number)>0):
            return True
    return False


def non_monetary_line_indices(source):
    """CFE IndFact=5: entrega gratuita sin importe, no un IVA del 5%."""
    return [i for i,line in enumerate(source['lineas'])
            if str(line.get('indicadorIVA',''))=='5' and decimal_value(line.get('monto','0'))==0]


def rounding_line_indices(source, tolerance=Decimal('1.00')):
    """Omitir solo ajustes de redondeo identificables y de importe pequeño.

    Se conserva siempre la línea en el XML. El total del documento debe cuadrar
    dentro de la tolerancia configurada; no se omiten descuentos ordinarios.
    """
    limit = min(decimal_value(tolerance), Decimal('1.00'))
    if not limit.is_finite() or limit <= 0:
        return []
    result = []
    for index, line in enumerate(source.get('lineas', [])):
        description = str(line.get('descripcion', '')).strip().casefold()
        is_rounding = re.match(r'^(?:redondeo\b|(?:ajuste|diferencia)\s+(?:(?:de|por)\s+)?redondeo\b)', description)
        if not is_rounding or str(line.get('indicadorIVA', '')) not in {'1','2','3','6','7'}:
            continue
        amount = decimal_value(line.get('monto', '0'))
        if amount.is_finite() and Decimal('0') < abs(amount) <= limit:
            result.append(index)
    return result


def payable_rounding(source, indices, tolerance):
    totals=source['totales']
    fiscal=cents(totals['total'])
    delta=cents(totals.get('pagar',fiscal))-fiscal
    if not delta or abs(delta)>min(tolerance,Decimal('1.00')):
        return None
    origins=[i for i in indices if source['lineas'][i]['indicadorIVA'] in {'6','7'}]
    amount=sum((abs(decimal_value(source['lineas'][i]['monto']))*
                (-1 if source['lineas'][i]['indicadorIVA']=='7' else 1) for i in origins),Decimal(0))
    if origins and amount==delta:
        return {'monto':str(delta),'origenes':sorted(origins)}
    return None


def tax_breakdown(source, excluded):
    """Desglose para revisión con los importes fiscales del encabezado."""
    totals = source['totales']
    bases = {'1':cents(totals.get('noGravado','0'))+cents(totals.get('exento','0')),
             '2':cents(totals['netoMin']), '3':cents(totals['netoBas'])}
    taxes = {'1':Decimal(0), '2':cents(totals['ivaMin']), '3':cents(totals['ivaBas'])}
    indices = {}
    for index, line in enumerate(source['lineas']):
        if index not in excluded and decimal_value(line['monto']) != 0:
            indices.setdefault(line['indicadorIVA'], []).append(index)
    result = []
    for vat in sorted(indices, key=lambda v:(len(v),v)):
        origins = indices.get(vat,[])
        base, tax = bases.get(vat), taxes.get(vat)
        if vat == '12':
            base = cents(totals['suspenso']) if 'suspenso' in totals else sum(
                (decimal_value(source['lineas'][index]['monto']) for index in origins), Decimal(0))
            tax = Decimal(0)
        total = (base + tax) if base is not None else sum(
            (decimal_value(source['lineas'][index]['monto']) for index in origins), Decimal(0))
        if vat == '7':total=-abs(total)
        result.append({'iva':vat,'etiqueta':VAT_LABELS.get(vat,'Indicador fiscal '+vat),
                       'base':str(cents(base)) if base is not None else None,
                       'impuesto':str(cents(tax)) if tax is not None else None,
                       'importe':str(cents(total)),'origenes':origins})
    return result


def group_plan_by_vat(lines):
    groups = {}
    for line in lines:
        groups.setdefault(line['indicadorIVA'],[]).append(line)
    result = []
    # Check the special tax first in Zeta before entering any regular line.
    for vat, members in sorted(groups.items(),key=lambda item:(item[0]!='12',item[0])):
        if len({(line['tipoDestino'],line['codigoZeta'],line.get('ivaZeta')) for line in members}) != 1:
            raise SourceError('Elegí un solo destino e IVA en Zeta para el grupo '+VAT_LABELS[vat]+'.')
        net = cents(sum((decimal_value(line['netoPropuesto5Decimales']) for line in members), Decimal(0)))
        tax = cents(net*VAT_RATES[vat])
        original = str(sum((decimal_value(line['montoXML']) for line in members), Decimal(0)))
        suffix = {'1':'exento','2':'10%','3':'22%','12':'IVA en suspenso'}[vat]
        family = re.sub(r'\s*[-–(/]?\s*(?:iva\s*)?(?:0\s*%|10\s*%|22\s*%|exent[oa]s?|(?:en\s+)?suspenso)\s*\)?$',
                        '', members[0]['descripcionZeta'], flags=re.I).strip()
        if family.isupper():family=family.capitalize()
        result.append(dict(members[0], origenes=[i for line in members for i in line['origenes']],
                           descripcionXML=' '.join(filter(None,(family,suffix))), cantidad='1', cantidadXML='1',
                           monto=original, montoXML=original,
                           netoPropuesto5Decimales=str(net.quantize(PRECISION)),
                           baseEsperada=str(net), ivaEsperado=str(tax), totalEsperado=str(net+tax)))
    return result


def preview_net_lines(source, invoice, expected_receiver, rounding_tolerance=Decimal('0')):
    """Plan de importes a cinco decimales; no habilita escrituras ni inventa códigos.

    Admite exento y bases 10/22% con orígenes fiscales verificados.
    Los totales se verifican después de aplicar la agrupación solicitada.
    La regla de cálculo concreta de Zeta debe validarse aparte por formulario.
    """
    if source['receptor'] != expected_receiver:
        raise SourceError('El receptor del XML no es la empresa de Zeta.')
    if source['tipo'] not in {'111', '112'}:
        raise SourceError('Tipo CFE sin plan de compras validado: '+source['tipo'])
    is_credit_note = source['tipo'] == '112'
    if is_credit_note and cents(source['totales']['total']) <= 0:
        raise SourceError('Nota de crédito sin monto fiscal positivo; requiere revisión antes de cargar.')
    if source.get('ajustesGlobales'):
        raise SourceError('Completar en Zeta los descuentos o recargos globales para conservar el total del comprobante.')
    if invoice.get('proveedor',{}).get('rut') != source['rut']:
        raise SourceError('El RUT correlacionado no coincide con el XML.')
    if source['clave'] != invoice.get('clave'):
        raise SourceError('El JSON y el XML identifican comprobantes diferentes.')
    for field in ('tipo','serie','numero','fecha','moneda'):
        actual=str(invoice['cfe'].get(field,''))
        if field=='numero' and actual.isdigit():
            actual=str(int(actual))
        if actual != source[field]:
            raise SourceError('Difiere '+field+' entre JSON y XML.')
    for field in ('netoMin','netoBas','ivaMin','ivaBas','total'):
        if cents(invoice['cfe'].get(field,'')) != cents(source['totales'][field]):
            raise SourceError('Difiere el total fiscal '+field+' entre JSON y XML.')
    if invoice['cfe'].get('noGravado') is not None and cents(invoice['cfe']['noGravado']) != cents(source['totales']['noGravado']):
        raise SourceError('Difiere el importe exento/no gravado entre JSON y XML.')
    if source['mntBruto'] not in {'0','1'}:
        raise SourceError('Indicador de montos brutos aún no soportado.')
    manual_ignored=ignored_line_indices(source,invoice)
    no_monetarias=[i for i in non_monetary_line_indices(source) if i not in manual_ignored]
    auto_rounding=[i for i in rounding_line_indices(source,rounding_tolerance) if i not in manual_ignored]
    assigned=list(manual_ignored)+no_monetarias+auto_rounding
    grouped=invoice.get('agruparPorIVA') is True
    result=[]
    bases=dict.fromkeys(VAT_RATES, Decimal(0))
    taxes=dict.fromkeys(VAT_RATES, Decimal(0))
    for line in invoice['lineas']:
        origins=line.get('origenes')
        if not isinstance(origins,list) or len(origins)!=1 or type(origins[0]) is not int:
            raise SourceError('Líneas agrupadas o sin origen: revisar el redondeo por línea.')
        index=origins[0]
        if index<0 or index>=len(source['lineas']) or index in assigned:
            raise SourceError('Origen de línea repetido o fuera del XML.')
        assigned.append(index)
        raw=source['lineas'][index]
        if str(line.get('indicadorIVA',''))!=raw['indicadorIVA']:
            raise SourceError('Indicador IVA distinto del XML.')
        indicator=raw['indicadorIVA']
        if indicator not in bases or (indicator=='12' and not grouped):
            raise SourceError('Indicador fiscal XML '+indicator+' sin plan validado; no corresponde modificarlo en Cerecito.')
        if 'ivaZeta' in line:
            validate_zeta_vat_choice(indicator,line['ivaZeta'])
        if not str(line.get('codigoZeta','')).strip() or not str(line.get('descripcionZeta','')).strip():
            raise SourceError('Falta código o descripción del artículo/concepto Zeta. Corregir en Correlaciones → Comprobantes.')
        if cents(line['monto'])!=cents(raw['monto']):
            raise SourceError('El importe original de Cerecito difiere del XML.')
        rate=VAT_RATES[indicator]
        original=decimal_value(raw['monto'])
        if original <= 0 or decimal_value(raw['cantidad']) <= 0:
            raise SourceError('Línea nula/negativa sin flujo específico validado.')
        net=(original/(1+rate) if source['mntBruto']=='1' else original).quantize(PRECISION,rounding=ROUND_HALF_UP)
        base=cents(net)
        tax=cents(net*rate)
        entry=deepcopy(line)
        entry.update(descripcionXML=raw['descripcion'],montoXML=str(original),cantidadXML=raw['cantidad'],netoPropuesto5Decimales=str(net),
                     baseEsperada=str(base),ivaEsperado=str(tax),totalEsperado=str(base+tax))
        result.append(entry)
    if set(assigned)!=set(range(len(source['lineas']))):
        raise SourceError('Hay líneas XML omitidas; no se completa el plan.')
    if not result:
        raise SourceError('El comprobante quedó sin líneas con importe para cargar.')
    adjustment=None
    if grouped:
        result=group_plan_by_vat(result)
        adjustment=payable_rounding(source,auto_rounding,rounding_tolerance)
        rounding_tolerance=Decimal(0)
        if 'pagar' in source['totales'] and cents(source['totales']['pagar'])!=cents(source['totales']['total']):
            if not adjustment:
                raise SourceError('El importe a pagar difiere del total fiscal. Zeta requiere registrar el ajuste por separado para conservar ambos importes.')
            target=invoice.get('destinoRedondeo')
            if not isinstance(target,dict) or not str(target.get('codigoZeta','')).strip() or not str(target.get('descripcionZeta','')).strip():
                raise SourceError('Elegí el destino del redondeo de '+adjustment['monto']+' en Agrupar por IVA.')
            if {line['tipoDestino'] for line in result}!={target.get('tipoDestino')}:
                raise SourceError('El redondeo debe usar el mismo tipo de destino que el resto de la factura.')
    for line in result:
        bases[line['indicadorIVA']]+=decimal_value(line['baseEsperada'])
        taxes[line['indicadorIVA']]+=decimal_value(line['ivaEsperado'])
    expectations={'netoMin':bases['2'],'netoBas':bases['3'],'ivaMin':taxes['2'],'ivaBas':taxes['3']}
    suspended_total=cents(source['totales'].get('suspenso','0'))
    if bases['12']!=suspended_total:
        raise SourceError('El grupo de IVA en suspenso no reproduce el importe del encabezado XML. Volvé a importar el lote si falta el dato.')
    exempt_total=cents(source['totales'].get('noGravado','0'))+cents(source['totales'].get('exento','0'))
    if abs(bases['1']-exempt_total)>rounding_tolerance:
        raise SourceError('El importe exento/no gravado de las líneas no reproduce los totales XML.')
    if any(abs(cents(source['totales'][key])-computed)>rounding_tolerance for key,computed in expectations.items()):
        raise SourceError('El redondeo por línea no reproduce las bases/IVA del XML.')
    if abs(sum(bases.values())+sum(taxes.values())-cents(source['totales']['total']))>rounding_tolerance:
        raise SourceError('El total calculado difiere '+str(sum(bases.values())+sum(taxes.values())-cents(source['totales']['total']))+'; tolerancia por comprobante '+str(rounding_tolerance)+'. Revisá las líneas y el XML.')
    differences={key:str(computed-cents(source['totales'][key])) for key,computed in expectations.items() if computed!=cents(source['totales'][key])}
    total_difference=sum(bases.values())+sum(taxes.values())-cents(source['totales']['total'])
    if total_difference:differences['total']=str(total_difference)
    if bases['1']!=exempt_total:differences['exento']=str(bases['1']-exempt_total)
    plan={'clave':source['clave'],'sha256XML':source['sha256XML'],'mntBruto':source['mntBruto'],
            'agrupadoPorIVA':grouped,
            'tipoOperacion':'nota_credito_compra' if is_credit_note else 'compra',
            'signoContable':-1 if is_credit_note else 1,
            'referencias':source.get('referencias', []),
            'diferenciasAceptadas':differences,'toleranciaRedondeo':str(rounding_tolerance),
            'lineasIgnoradas':invoice.get('lineasIgnoradas',[]),
            'lineasNoMonetarias':no_monetarias,
            'lineasRedondeo':auto_rounding,
            'totalesXML':{'neto':str(sum((cents(source['totales'].get(k,'0')) for k in ('netoMin','netoBas','noGravado','exento','suspenso')),Decimal(0))),
                          'iva':str(cents(source['totales']['ivaMin'])+cents(source['totales']['ivaBas'])),
                          'total':source['totales']['total']},
            'lineas':result,'totales':{**{k:str(v) for k,v in expectations.items()},'noGravado':str(bases['1'])},
            'total':source['totales']['total'],'guardar_habilitado':False,
            'pendiente':'Validar selección contable y redondeo de Zeta por formulario. Requiere autorización antes de guardar factura real.'}
    if adjustment:
        amount=decimal_value(adjustment['monto'])
        result.append(dict(target,esRedondeo=True,origenes=adjustment['origenes'],indicadorIVA='1',ivaZeta='1',
                           descripcionXML='Redondeo',cantidadXML='1',montoXML=str(amount),
                           netoPropuesto5Decimales=str(amount.quantize(PRECISION)),
                           baseEsperada=str(amount),ivaEsperado='0.00',totalEsperado=str(amount)))
        plan['ajusteRedondeo']=adjustment
        plan['totalesZeta']={**plan['totalesXML'],
                           'neto':str(decimal_value(plan['totalesXML']['neto'])+amount),
                           'total':str(cents(source['totales']['pagar']))}
    return plan
