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
    return {'clave':'|'.join((rut,kind,series,str(int(number)))), 'archivo':filename,
            'sha256CFE':hashlib.sha256(ET.canonicalize(ET.tostring(nodes[0],encoding='unicode'),strip_text=True,rewrite_prefixes=True).encode('utf-8')).hexdigest(),
            'sha256XML':hashlib.sha256(content).hexdigest(),'rut':rut,'receptor':target_rut,
            'nombre':value(emitter,'RznSoc'),'tipo':kind,'serie':series,'numero':str(int(number)),
            'fecha':value(ident,'FchEmis'),'moneda':value(totals,'TpoMoneda','') if kind=='181' else value(totals,'TpoMoneda'),
            'mntBruto':value(ident,'MntBruto','0'),'formaPagoXML':value(ident,'FmaPago',''),
            'ajustesGlobales':bool(nodes[0].findall('.//{*}DscRcgGlobal')),
            'totales':fiscal,'lineas':items,
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


def ignored_line_indices(source, invoice):
    indices=invoice.get('lineasIgnoradas',[])
    if not isinstance(indices,list) or any(type(index) is not int for index in indices) or len(set(indices))!=len(indices):
        raise SourceError('Selección de líneas ignoradas inválida.')
    for index in indices:
        if index<0 or index>=len(source['lineas']):
            raise SourceError('Línea ignorada fuera del XML.')
        if source['lineas'][index]['indicadorIVA'] not in {'6','7'}:
            raise SourceError('Solo se pueden ignorar líneas XML no facturables (indicadores 6/7). No omitir productos con IVA ni exentos.')
    return indices


def preview_net_lines(source, invoice, expected_receiver, rounding_tolerance=Decimal('0')):
    """Plan de importes a cinco decimales; no habilita escrituras ni inventa códigos.

    Admite exento y bases 10/22%, sin líneas omitidas/agrupadas ni ajustes globales.
    Los totales deben coincidir exactamente al redondear cada línea a centavos.
    La regla de cálculo concreta de Zeta debe validarse aparte por formulario.
    """
    if source['receptor'] != expected_receiver:
        raise SourceError('El receptor del XML no es la empresa de Zeta.')
    if source['tipo'] != '111' or source.get('ajustesGlobales'):
        raise SourceError('Tipo fiscal o ajustes globales no validados para este plan.')
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
    assigned=list(ignored_line_indices(source,invoice))
    result=[]
    bases={'1':Decimal(0),'2':Decimal(0),'3':Decimal(0)}
    taxes={'1':Decimal(0),'2':Decimal(0),'3':Decimal(0)}
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
        if indicator not in bases:
            raise SourceError('Indicador fiscal XML '+indicator+' sin plan validado; no corresponde modificarlo en Cerecito.')
        if not str(line.get('codigoZeta','')).strip() or not str(line.get('descripcionZeta','')).strip():
            raise SourceError('Falta código o descripción del artículo/concepto Zeta. Corregir en Correlaciones → Comprobantes.')
        if cents(line['monto'])!=cents(raw['monto']):
            raise SourceError('El importe original de Cerecito difiere del XML.')
        rate={'1':Decimal('0'),'2':Decimal('0.10'),'3':Decimal('0.22')}[indicator]
        original=decimal_value(raw['monto'])
        if original <= 0 or decimal_value(raw['cantidad']) <= 0:
            raise SourceError('Línea nula/negativa sin flujo específico validado.')
        net=(original/(1+rate) if source['mntBruto']=='1' else original).quantize(PRECISION,rounding=ROUND_HALF_UP)
        base=cents(net)
        tax=cents(net*rate)
        bases[indicator]+=base
        taxes[indicator]+=tax
        entry=deepcopy(line)
        entry.update(montoXML=str(original),cantidadXML=raw['cantidad'],netoPropuesto5Decimales=str(net),
                     baseEsperada=str(base),ivaEsperado=str(tax),totalEsperado=str(base+tax))
        result.append(entry)
    if set(assigned)!=set(range(len(source['lineas']))):
        raise SourceError('Hay líneas XML omitidas; no se completa el plan.')
    expectations={'netoMin':bases['2'],'netoBas':bases['3'],'ivaMin':taxes['2'],'ivaBas':taxes['3']}
    if abs(bases['1']-cents(source['totales'].get('noGravado','0')))>rounding_tolerance or cents(source['totales'].get('exento','0')) != 0:
        raise SourceError('El importe exento/no gravado de las líneas no reproduce los totales XML.')
    if any(abs(cents(source['totales'][key])-computed)>rounding_tolerance for key,computed in expectations.items()):
        raise SourceError('El redondeo por línea no reproduce las bases/IVA del XML.')
    if abs(sum(bases.values())+sum(taxes.values())-cents(source['totales']['total']))>rounding_tolerance:
        raise SourceError('El plan no reproduce exactamente el total XML.')
    return {'clave':source['clave'],'sha256XML':source['sha256XML'],'mntBruto':source['mntBruto'],
            'lineasIgnoradas':invoice.get('lineasIgnoradas',[]),
            'totalesXML':{'neto':str(sum((cents(source['totales'].get(k,'0')) for k in ('netoMin','netoBas','noGravado')),Decimal(0))),
                          'iva':str(cents(source['totales']['ivaMin'])+cents(source['totales']['ivaBas'])),
                          'total':source['totales']['total']},
            'lineas':result,'totales':{**{k:str(v) for k,v in expectations.items()},'noGravado':str(bases['1'])},
            'total':source['totales']['total'],'guardar_habilitado':False,
            'pendiente':'Validar selección contable y redondeo de Zeta por formulario. Requiere autorización antes de guardar factura real.'}
