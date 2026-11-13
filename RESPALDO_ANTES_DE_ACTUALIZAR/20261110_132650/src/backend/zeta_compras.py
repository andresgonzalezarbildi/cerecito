"""Funciones de formularios de compras de Zeta."""

from runtime_paths import DATA_DIR, CODE_DIR, APP_DIR, APP_URL, company_rut, control_value, category_provider_rut
import argparse
import json
from copy import deepcopy
from datetime import date
from decimal import Decimal, ROUND_HALF_UP
from urllib.parse import urlsplit

from playwright.sync_api import sync_playwright
from conciliar import BASE, one_file, read_excel, already_registered
from chrome_connection import connect_chrome, choose_page
from xml_source import read_zip, preview_net_lines
from zeta_bot import exclusive_lock, save_state, stamp, append_ledger, unresolved_attempts
from zeta_ui_runtime import wait_value, wait_path, click_readonly, select_article, open_prompt, prompt_frame,select_prompt_result
from search_scope import invoice_search_period

LIST = 'https://www.zetasoftware.com/z.gestion.comprobantes.compras'


class DuplicateSearchError(RuntimeError):
    def __init__(self, query):
        super().__init__('Hay resultados para el número del comprobante; revisar identidad antes de cargar.')
        self.query=query


def localized(value):
    return str(value).replace('.', ',')


def money(text):
    return Decimal(str(text).replace('\xa0','').replace(' ','').replace('.', '').replace(',', '.'))


def tax_rate(line):
    rates={'1':Decimal('0'),'2':Decimal('.10'),'3':Decimal('.22')}
    if str(line.get('indicadorIVA','')) not in rates:raise RuntimeError('Indicador IVA sin tasa soportada.')
    return rates[str(line['indicadorIVA'])]


def unit_price(line):
    qty = Decimal(line['cantidadXML'])
    if qty<=0:raise RuntimeError('Cantidad XML no positiva.')
    price = (Decimal(line['netoPropuesto5Decimales']) / qty).quantize(Decimal('.00001'), rounding=ROUND_HALF_UP)
    base = (price * qty).quantize(Decimal('.01'), rounding=ROUND_HALF_UP)
    tax = (price * qty * tax_rate(line)).quantize(Decimal('.01'), rounding=ROUND_HALF_UP)
    if qty <= 0 or base != Decimal(line['baseEsperada']) or tax != Decimal(line['ivaEsperado']):
        raise RuntimeError('La cantidad y el precio unitario no reproducen el XML.')
    return price


def input_price(line,mode):
    net=unit_price(line)
    # Modalidad O observada en el formulario de visualización:
    # precio ingresado 7121,97000, cantidad 1, base 7121,97 e IVA 1566,83.
    # Mantener la modalidad del proveedor; no modificar su ficha maestra.
    if mode in {'N','O'}:return net
    if mode!='S':raise RuntimeError('Modalidad de importes del proveedor desconocida.')
    rate=tax_rate(line);qty=Decimal(line['cantidadXML'])
    gross=(net*(1+rate)).quantize(Decimal('.00001'),rounding=ROUND_HALF_UP)
    base=(gross*qty/(1+rate)).quantize(Decimal('.01'),rounding=ROUND_HALF_UP)
    tax=(base*rate).quantize(Decimal('.01'),rounding=ROUND_HALF_UP)
    total=(gross*qty).quantize(Decimal('.01'),rounding=ROUND_HALF_UP)
    tolerance=Decimal(line.get('toleranciaRedondeo','0'))
    if any(abs(actual-expected)>tolerance for actual,expected in zip((base,tax,total),(Decimal(line['baseEsperada']),Decimal(line['ivaEsperado']),Decimal(line['totalEsperado'])))):
        raise RuntimeError('El precio con IVA incluido no reproduce exactamente el XML.')
    return gross


def adapt_zeta_rounding(plan):
    """Zeta calcula IVA sobre base redondeada: partir cantidades enteras si hace falta.

    Conserva código, precio, cantidad total y desglose fiscal de cada origen XML.
    Nunca agrega un ajuste inventado ni cambia el indicador fiscal.
    """
    result=deepcopy(plan);result['lineas']=[]
    for original in plan['lineas']:
        price=unit_price(original);qty=Decimal(original['cantidadXML'])
        expected_base=Decimal(original['baseEsperada']);expected_tax=Decimal(original['ivaEsperado'])
        def calculate(q):
            base=(price*q).quantize(Decimal('.01'),rounding=ROUND_HALF_UP)
            tax=(base*tax_rate(original)).quantize(Decimal('.01'),rounding=ROUND_HALF_UP)
            return base,tax
        base,tax=calculate(qty)
        if (base,tax)==(expected_base,expected_tax):
            result['lineas'].append(deepcopy(original));continue
        if qty!=qty.to_integral_value() or not 2<=qty<=5000:
            raise RuntimeError('El redondeo de Zeta requiere una división no validada de cantidades.')
        chosen=None
        for first in range(1,int(qty)):
            quantities=[Decimal(first),qty-Decimal(first)]
            pairs=[calculate(q) for q in quantities]
            if sum(b for b,t in pairs)!=expected_base or sum(t for b,t in pairs)!=expected_tax:continue
            gross=[(Decimal(original['totalEsperado'])*q/qty).quantize(Decimal('.01'),rounding=ROUND_HALF_UP) for q in quantities]
            if any(b+t!=g for (b,t),g in zip(pairs,gross)):continue
            chosen=(quantities,pairs,gross);break
        if chosen is None:raise RuntimeError('No existe división entera que reproduzca base, IVA y total XML.')
        quantities,pairs,gross=chosen
        for part,(q,(base,tax),total) in enumerate(zip(quantities,pairs,gross),1):
            item=deepcopy(original)
            item.update(cantidadXML=str(q),netoPropuesto5Decimales=str((price*q).quantize(Decimal('.00001'))),
                        baseEsperada=str(base),ivaEsperado=str(tax),totalEsperado=str(total),
                        division_redondeo={'parte':part,'partes':2,'cantidad_original':str(qty),'monto_original':original['montoXML']})
            result['lineas'].append(item)
    result['calculo_zeta']='IVA sobre base redondeada a centavos; división de cantidades enteras sin alterar origen XML'
    return result


def set_control(page, selector, value):
    control = page.locator(selector)
    if control.count() != 1:
        raise RuntimeError('Campo ausente o ambiguo: ' + selector)
    control.evaluate('''(element, value) => {
        const field = gx.O.getValidStructFld(element);
        if (!field || typeof field.c2v !== 'function' || typeof field.v2c !== 'function') throw Error('Campo sin enlace GeneXus');
        element.value=value;
        // Usar el conversor del control: conserva gxdate, números y códigos reales.
        field.c2v(); field.v2c();
    }''', str(value))
    actual = control.input_value()
    if actual != str(value):
        # GeneXus normaliza números a su máscara local.
        if selector.startswith('#FPIMPORTE_') or selector.replace('#v','#',1) in {'#DOCTC','#LINCANTIDAD','#LINPRECIO1','#LINDTO1','#LINDTO2','#LINDTO3'}:
            if money(actual) == Decimal(str(value).replace(',','.')):
                return
        raise RuntimeError('Zeta cambió o truncó el valor de ' + selector)


def refresh_list(page, start, end, number, *, require_complete=True, stop_when=None):
    import re
    pagination=page.locator('#GRIDPAGINATIONBARContainer_DVPaginationBar')
    current=re.search(r'(\d+)\s+de\s+(\d+)',pagination.inner_text())
    if current and int(current[1])>1:
        with page.expect_response(lambda r:urlsplit(r.url).path=='/z.gestion.comprobantes.compras' and r.request.method=='POST',timeout=60000) as reset:
            page.evaluate("gx.O.GRIDPAGINATIONBARContainer.SelectedPage='1';gx.O.GRIDPAGINATIONBARContainer.ChangePage()")
        reset.value.finished()
        wait_value(lambda:pagination.inner_text(),lambda text:bool(re.search(r'\b1\s+de\s+\d+',text)),'No se pudo volver a la primera pagina de consulta.')
    for selector, value in [('#vDOCFECHA1',start),('#vDOCFECHA1_TO',end),('#vFILTERFULLTEXT',number)]:
        set_control(page, selector, value)
    # Wait for the response body and GeneXus rendering before reading the grid.
    with page.expect_response(lambda r: urlsplit(r.url).path == '/z.gestion.comprobantes.compras'
                              and r.request.method == 'POST', timeout=60000) as response:
        page.evaluate("gx.O.executeServerEvent('REFRESH', true, null, false, false)")
    response.value.finished()
    wait_value(lambda:page.evaluate('!gx.evt.processing && !gx.spa.isNavigating()'),bool,'La consulta sigue procesando.')
    values = {s:page.locator(s).input_value() for s in ['#vDOCFECHA1','#vDOCFECHA1_TO','#vFILTERFULLTEXT']}
    if list(values.values()) != [start,end,number]:
        raise RuntimeError('Zeta alteró el rango o el filtro de duplicados.')
    def read_rows():
        return page.locator('#GridContainerTbl').evaluate('''table => [...table.querySelectorAll('tbody tr')].map(row=>({
        text:row.innerText,
        number:row.querySelector('[id^="span_DOCNUMERO_"]')?.textContent?.trim(),
        series:row.querySelector('[id^="span_DOCSERIE_"]')?.textContent?.trim(),
        supplier:row.querySelector('[id^="span_PRVIDDOC_"]')?.textContent?.trim(),
        docid:row.querySelector('[id^="span_DOCID_"]')?.textContent?.trim(),
        edit:row.querySelector('[id^="span_vEDITAR_"]')?.id
    }))''')
    rows=read_rows()
    pagination=page.locator('#GRIDPAGINATIONBARContainer_DVPaginationBar')
    pages = pagination.inner_text()
    if not rows and 'No se encontraron registros' in pages:
        return {'fecha':stamp(),'desde':start,'hasta':end,'numero':number,'filas':[],'consulta_completa':True,'paginas':0}
    import re
    match=re.search(r'(\d+)\s+de\s+(\d+)',pages)
    if not match or int(match[1])!=1 or not 1<=int(match[2])<=10000:
        raise RuntimeError('No se pudo comprobar la primera página de la búsqueda. Reintentá la consulta.')
    total_pages=int(match[2])
    def presence_found():
        return stop_when is not None and any(stop_when(row) for row in rows)
    if presence_found():
        return {'fecha':stamp(),'desde':start,'hasta':end,'numero':number,'filas':rows,'consulta_completa':total_pages==1,'paginas':total_pages}
    seen={r.get('docid') for r in rows if r.get('docid')}
    for number_page in range(2,total_pages+1):
        next_page=pagination.locator('li.next:not(.disabled) a')
        if next_page.count()!=1:raise RuntimeError('Falta la siguiente página; búsqueda incompleta.')
        with page.expect_response(lambda r:urlsplit(r.url).path=='/z.gestion.comprobantes.compras' and r.request.method=='POST',timeout=60000) as next_response:
            click_readonly(next_page)
        next_response.value.finished()
        wait_value(lambda:page.evaluate('!gx.evt.processing && !gx.spa.isNavigating()'),bool,'La pagina sigue procesando.')
        wait_value(lambda:pagination.inner_text(),lambda text:bool(re.search(r'\b'+str(number_page)+r'\s+de\s+'+str(total_pages)+r'\b',text)),'La paginación cambió durante la búsqueda.')
        if [page.locator(s).input_value() for s in ['#vDOCFECHA1','#vDOCFECHA1_TO','#vFILTERFULLTEXT']]!=[start,end,number]:
            raise RuntimeError('Los filtros cambiaron al avanzar de página.')
        following=read_rows()
        ids={r.get('docid') for r in following if r.get('docid')}
        if not following or seen.intersection(ids):raise RuntimeError('Página vacía o repetida; la búsqueda no está completa.')
        seen.update(ids);rows.extend(following)
        if presence_found():
            return {'fecha':stamp(),'desde':start,'hasta':end,'numero':number,'filas':rows,'consulta_completa':number_page==total_pages,'paginas':total_pages}
    return {'fecha':stamp(),'desde':start,'hasta':end,'numero':number,'filas':rows,'consulta_completa':True,'paginas':total_pages}


def check_duplicates(page, invoice, positive_proof=None):
    today=date.today()
    start,end=invoice_search_period(invoice['cfe']['fecha'],today=today)
    page.goto(LIST, wait_until='load'); wait_path(page, '/z.gestion.comprobantes.compras')
    # Control positivo de la misma empresa, observado en la lista y el Excel.
    control=refresh_list(page,'01/01/26',today.strftime('%d/%m/%y'),control_value('numero'))
    if not any(r['number']==control_value('numero') and control_value('nombre') in r['text'] for r in control['filas']):
        raise RuntimeError('El control positivo de búsqueda falló; ausencia no demostrada.')
    if positive_proof is not None:
        positive_proof.update(control)
    result=refresh_list(page,start,end,str(invoice['cfe']['numero']))
    if result['filas']:
        raise DuplicateSearchError(result)
    return [result]


class SupplierApprovalRequired(RuntimeError):
    def __init__(self, supplier, actual):
        self.supplier=deepcopy(supplier)
        self.actual=deepcopy(actual)
        super().__init__('RUT vacío en Zeta: confirmar la correlación de proveedor antes de cargar.')


def validate_supplier_catalog(supplier, actual, approvals):
    if actual['codigoZeta']!=supplier['codigoZeta']:raise RuntimeError('Código del proveedor diferente del correlacionado.')
    if supplier.get('generico') is True:
        # Solo el proveedor genérico elegido manualmente para esta factura.
        # El RUT del CFE permanece intacto; no guardar una relación global.
        if not ('genérico' in actual.get('nombre', '').casefold() or 'generico' in actual.get('nombre', '').casefold()):
            raise RuntimeError('El código ya no identifica al proveedor genérico en Zeta.')
        return
    if supplier.get('seleccionManual') is True:return
    approval=approvals.get(supplier['rut'],{})
    if (approval.get('codigoZeta')==actual['codigoZeta']
            and approval.get('nombreZeta')==actual['nombre']
            and approval.get('confirmado_por_usuario') is True):
        return
    if actual['rut']:
        if actual['rut']!=supplier['rut']:raise RuntimeError('RUT del proveedor contradictorio: no se carga.')
    else:
        raise SupplierApprovalRequired(supplier,actual)


def supplier_catalog_match(page,supplier,*,open_new=True):
    if open_new:open_prompt(page, '[data-gx-for="PRVIDDOC"] a', 'z.gestion.configuracion.contactosproveedorprompt')
    frame = prompt_frame(page)
    set_control(frame, '#vFILTERFULLTEXT', supplier['codigoZeta'])
    frame.evaluate("gx.O.executeServerEvent('REFRESH', true, null, false, false)")
    row=frame.locator('#GridContainerTbl tbody tr').filter(has=frame.get_by_text(supplier['codigoZeta'],exact=True))
    wait_value(lambda:row.count(),lambda n:n==1,'Proveedor ausente o ambiguo en el buscador.')
    actual={'codigoZeta':row.locator('[id^="span_CTOID_"]').inner_text().strip(),
            'rut':row.locator('[id^="span_CTORUT_"]').inner_text().strip(),
            'nombre':row.locator('[id^="span_CTONOM_"]').inner_text().strip()}
    return row,actual


def select_supplier_exact(page, supplier, *, confirm_supplier=None):
    row,actual=supplier_catalog_match(page,supplier)
    config=json.loads((BASE/'configuracion_zeta.json').read_text(encoding='utf-8'))
    try:
        validate_supplier_catalog(supplier,actual,config.get('correlaciones_proveedor_confirmadas',{}))
    except SupplierApprovalRequired:
        if confirm_supplier is None:raise
        approvals=confirm_supplier(supplier,actual)
        validate_supplier_catalog(supplier,actual,approvals)
        # Una respuesta humana puede tardar: volver a adquirir el iframe real.
        if page.evaluate('location.pathname')!='/z.gestion.comprobantes.facturaproveedor' or page.locator('#DOCID').input_value()!='0':
            raise RuntimeError('El formulario cambió durante la confirmación del proveedor.')
        page.evaluate('document.querySelectorAll("iframe").length')
        existing=[f for f in page.frames if not f.is_detached() and '/z.gestion.configuracion.contactosproveedorprompt'==urlsplit(f.url).path]
        row,reacquired=supplier_catalog_match(page,supplier,open_new=not existing)
        if reacquired!=actual:raise RuntimeError('El proveedor cambió mientras se confirmaba su identidad.')
    if row.count()!=1:raise RuntimeError('El proveedor cambió durante la selección.')
    fresh={'codigoZeta':row.locator('[id^="span_CTOID_"]').inner_text().strip(),
           'rut':row.locator('[id^="span_CTORUT_"]').inner_text().strip(),
           'nombre':row.locator('[id^="span_CTONOM_"]').inner_text().strip()}
    if fresh!=actual:raise RuntimeError('El catálogo de proveedores cambió antes de seleccionar.')
    expected=supplier['codigoZeta']+' - '
    select_prompt_result(row.locator('img[title="Seleccionar"]'),
               lambda:page.locator('#PRVIDDOC').input_value(),lambda value:value.startswith(expected) and len(value)>len(expected),
               'Proveedor seleccionado distinto al correlacionado.')


def prepare_header(page, invoice, form='Compra Crédito', *, confirm_supplier=None):
    codes={'Compra Crédito':'21','Compra Crédito Gastos':'26','Compra Contado':'23','Compra Contado Gastos':'28',
           'Nota de Crédito Compra':'22','Nota de Crédito Compra Gastos':'27'}
    if form not in codes or invoice['zeta']['comprobante']!=form:
        raise RuntimeError('Formulario fuera de los tipos capturados.')
    click_readonly(page.locator('#DDO_AGAGREGARContainer_btnGroupDrop'))
    note=form.startswith('Nota de Crédito Compra')
    menu_form=('Nota de Crédito' if note else ('Compra Contado' if form.startswith('Compra Contado') else 'Compra Crédito'))
    menu=page.locator('ul[role="menu"][aria-labelledby="DDO_AGAGREGARContainer_btnGroupDrop"]')
    links=menu.locator('li > a[href^="z.gestion.comprobantes.facturaproveedor?"]')
    def match_menu(title):
        text=' '.join(title.split()).lower().replace('é','e').replace('á','a')
        return (('nota' in text and 'credito' in text) or 'n/c' in text) if note else text==menu_form.lower().replace('é','e').replace('á','a')
    matches=[link for link in links.all() if match_menu(link.inner_text())]
    if len(matches)!=1:
        raise RuntimeError(f'El menú Agregar devolvió {len(matches)} enlaces para {menu_form} ({form}).')
    href=matches[0].get_attribute('href')
    page.goto('https://www.zetasoftware.com/'+href,wait_until='load')
    wait_path(page,'/z.gestion.comprobantes.facturaproveedor')
    if note:
        # 22/27 come from the purchase-document families. Validate that Zeta's live
        # labels actually identify a *purchase credit note*, not an ordinary bill.
        choices=page.locator('#CMPIDDOC option').evaluate_all('options=>options.map(o=>({value:o.value,text:o.textContent.trim()}))')
        matched=[v for v in choices if v['value']==codes[form]]
        if len(matched)!=1:
            raise RuntimeError('La nota de crédito no existe en el selector de Zeta.')
        label=matched[0]['text'].lower().replace('é','e').replace('á','a')
        expected_kind='gasto' if form.endswith(' Gastos') else None
        if ('n/c' not in label and not ('nota' in label and 'credito' in label)) or (expected_kind and expected_kind not in label):
            raise RuntimeError('El código de nota de crédito no corresponde a la descripción real de Zeta.')
    if page.locator('#CMPIDDOC').input_value()!=codes[form]:
        options=page.locator('#CMPIDDOC option').evaluate_all('options=>options.map(o=>({value:o.value,text:o.textContent.trim()}))')
        labels={'21':'21-C.Crédito','26':'26-Créd.Gasto','23':'23-C.Contado','28':'28-Cont.Gasto'}
        if not note and not any(o=={'value':codes[form],'text':labels[codes[form]]} for o in options):
            raise RuntimeError('Tipo diferente del catálogo capturado.')
        page.locator('#CMPIDDOC').select_option(value=codes[form])
        page.locator('#CMPIDDOC').press('Tab')
        wait_value(lambda:page.evaluate('!gx.evt.processing && !gx.spa.isNavigating()'),bool,'El formulario de gastos sigue cambiando.')
        set_control(page,'#CMPIDDOC',codes[form])
    if page.locator('#CMPIDDOC').input_value()!=codes[form]: raise RuntimeError('Formulario diferente del esperado: '+form)
    if page.locator('#DOCID').input_value()!='0':raise RuntimeError('El formulario no es un comprobante nuevo.')
    c=invoice['cfe']
    currency_code={'UYU':'1','USD':'2'}.get(c['moneda'])
    if not currency_code:raise RuntimeError('Moneda no soportada.')
    currency_options=page.locator('#MNDIDDOC option').evaluate_all('els=>els.map(e=>({value:e.value,text:e.textContent.trim()}))')
    expected_currency={'1':'1 - $','2':'2 - U$S'}[currency_code]
    if {'value':currency_code,'text':expected_currency} not in currency_options:raise RuntimeError('La moneda del formulario cambio.')
    center=invoice['zeta'].get('centroCosto') or ''
    options=page.locator('#CENIDDOC option').evaluate_all('options=>options.map(o=>({value:o.value,text:o.textContent.trim()}))')
    matches=[o['value'] for o in options if o['value']==center]
    if not matches:matches=[o['value'] for o in options if o['text']==center]
    if len(matches)!=1:raise RuntimeError('Centro de costos ausente o ambiguo en el catálogo real.')
    center_code=matches[0]
    fields=[('#DOCSERIE',c['serie']),('#DOCNUMERO',c['numero']),
                           ('#DOCFECHA1',date.fromisoformat(c['fecha']).strftime('%d/%m/%y')),
                           ('#MNDIDDOC',currency_code),('#CENIDDOC',center_code)]
    if form.startswith('Compra Contado'):
        cashbox=page.locator('#CAJAIDDOC').input_value()
        if cashbox in {'','0'}:raise RuntimeError('Zeta no tiene una caja seleccionada para contado.')
        fields.append(('#CAJAIDDOC',cashbox))
    if currency_code=='2':
        rate=Decimal(c.get('tipoCambio') or '0')
        if not rate.is_finite() or rate<=0:raise RuntimeError('Falta cotizacion original del XML.')
        fields.append(('#DOCTC',localized(rate)))
    for selector,value in fields:
        set_control(page,selector,value)
    if confirm_supplier is None:select_supplier_exact(page, invoice['proveedor'])
    else:select_supplier_exact(page, invoice['proveedor'],confirm_supplier=confirm_supplier)
    # Elegir proveedor puede traer defaults: prevalecen los datos correlacionados.
    for selector,value in fields:set_control(page,selector,value)
    set_control(page,'#CMPIDDOC',codes[form])
    if form=='Compra Crédito' and (page.locator('#DEPIDDOCORIGEN').input_value()!='0' or page.locator('#DEPIDDOCDESTINO').input_value()!='1'):
        raise RuntimeError('Depósitos distintos de los observados: no se inventan valores.')
    if page.locator('#DOCNUMERO').input_value()!=c['numero'] or page.locator('#DOCSERIE').input_value()!=c['serie']:
        raise RuntimeError('La cabecera cambió después de elegir proveedor.')
    if page.locator('#MNDIDDOC').input_value()!=currency_code:raise RuntimeError('La moneda cambio al elegir el proveedor.')
    if currency_code=='2' and money(page.locator('#DOCTC').input_value())!=rate:raise RuntimeError('La cotizacion no coincide con el XML.')
    if page.locator('#CENIDDOC').input_value()!=center_code:
        raise RuntimeError('Centro de costos distinto del indicado.')


def read_totals(page):
    return {key:str(money(page.locator(selector).inner_text())) for key,selector in
            [('neto','#span_vDOCSUBTOTAL'),('iva','#span_vDOCIVA'),('total','#span_vDOCTOTAL')]}


def read_payment_rows(page):
    return page.locator('#GridContainerTbl').evaluate("""table=>[...table.querySelectorAll('tbody tr')].map(row=>({
        docid:row.querySelector('[id^="span_DOCID_"]')?.textContent.trim(),
        name:row.querySelector('[id^="span_FORMAPAGONOM_"]')?.textContent.trim(),
        currency:row.querySelector('[id^="span_MNDIDFP_"]')?.textContent.trim(),
        amount:row.querySelector('[id^="span_FPIMPORTE_"]')?.textContent.trim()
    })).filter(row=>row.docid)""")


def finish_cash_payment(page,invoice,state,state_path):
    if not invoice['zeta']['comprobante'].startswith('Compra Contado'):return
    total=Decimal(state['totales']['total'])
    currency={'UYU':'1','USD':'2'}[invoice['cfe']['moneda']]
    code=invoice['zeta'].get('formaPago')
    name=invoice['zeta'].get('formaPagoNombre','').strip().casefold()
    if not code or not name:raise ValueError('Falta la forma de pago del catalogo.')
    def verified(rows):
        return len(rows)==1 and rows[0]['docid']==state['docid'] and rows[0]['name'].casefold()==name and rows[0]['currency']==currency and money(rows[0]['amount'])==total
    click_readonly(page.locator('#BTNFORMAPAGO'))
    wait_path(page,'/z.gestion.comprobantes.egresoscajafp')
    if money(page.locator('#vDOCTOTAL').input_value())!=total:raise ValueError('La forma de pago pertenece a otro total.')
    rows=read_payment_rows(page)
    if not verified(rows):
        if rows and (len(rows)!=1 or rows[0]['docid']!=state['docid'] or money(rows[0]['amount'])!=total):
            raise ValueError('Hay pagos distribuidos que requieren revisar antes de reemplazar.')
        page.locator('#BTNENTER').click()
        wait_path(page,'/z.gestion.comprobantes.egresocajafp')
        if page.locator('#DOCID').input_value()!=state['docid'] or money(page.locator('#DOCTOTAL').input_value())!=total:
            raise ValueError('El editor de pago pertenece a otro comprobante.')
        options=page.locator('#FORMAPAGOID_0001 option').evaluate_all('els=>els.map(o=>({value:o.value,text:o.textContent.trim()}))')
        if not any(o['value']==code and o['text'].casefold()==name for o in options):raise ValueError('La forma de pago difiere del catalogo.')
        for index in range(2,page.locator('[id^="FPIMPORTE_"]').count()+1):
            if money(page.locator('#FPIMPORTE_'+str(index).zfill(4)).input_value())!=0:raise ValueError('El pago tiene otras filas con importe.')
        page.locator('#FORMAPAGOID_0001').select_option(value=code)
        page.locator('#FORMAPAGOID_0001').press('Tab')
        wait_value(lambda:page.evaluate('!gx.evt.processing && !gx.spa.isNavigating()'),bool,'Zeta sigue validando el medio de pago.')
        page.locator('#MNDIDFP_0001').focus()
        set_control(page,'#MNDIDFP_0001',currency)
        page.locator('#FPIMPORTE_0001').focus()
        set_control(page,'#FPIMPORTE_0001',localized(total))
        if currency=='2':set_control(page,'#DOCTC',localized(invoice['cfe']['tipoCambio']))
        confirm_once(page,state,'forma_pago',state_path=state_path)
        wait_path(page,'/z.gestion.comprobantes.egresoscajafp')
        page.reload(wait_until='load');wait_path(page,'/z.gestion.comprobantes.egresoscajafp')
        if not verified(read_payment_rows(page)):raise ValueError('Zeta no conserva la forma de pago y el importe elegidos.')
    state['pago_verificado']={'codigo':code,'nombre':invoice['zeta']['formaPagoNombre'],'moneda':invoice['cfe']['moneda'],'importe':str(total)}


def confirm_once(page, state, phase, selector='#BTNTRN_ENTER', *, state_path=None):
    if not state_path or not state.get('clave'):raise ValueError('Falta identidad o archivo de estado para confirmar.')
    wait_value(lambda:page.evaluate("gx.isInputEnabled() && !gx.evt.processing && !gx.spa.isNavigating() && gx.evt.canExecuteEvent('EENTER.')"),
               bool, 'GeneXus aún está validando campos; no se confirma.')
    state.update(estado='INICIANDO_GUARDADO', etapa=phase, fecha=stamp())
    persisted={key:value for key,value in state.items() if key!='plan'} if state_path else state
    save_state(state_path,persisted)
    append_ledger(BASE/'estado/intentos.jsonl',{'clave':state['clave'],'estado':'INICIANDO_GUARDADO','etapa':phase,'fecha':stamp()})
    # Una sola ejecución: el llamador verifica resultado o deja INCIERTO.
    control=page.locator(selector)
    if control.count()!=1: raise RuntimeError('Confirmar ausente o ambiguo.')
    if not control.is_visible() or not control.is_enabled():raise RuntimeError('Confirmar no está habilitado: no se fuerza un control oculto.')
    control.evaluate('''element => {
        const field=gx.O.getValidStructFld(element);
        if (!field || field.std!=='ENTER' || !field.evt || typeof gx.O[field.evt]!=='function') throw Error('Confirmar sin evento verificado');
        gx.O[field.evt]();
    }''')

