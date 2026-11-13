"""Synthetic fiscal documents; no network or production writes."""
import io
import json
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
from zipfile import ZipFile

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src/backend'))
from workspace_store import Store
from workspace_jobs import Jobs

COMPANY = '000000000001'
SUPPLIER = '000000000002'


def xml(number=67, total='100', indicator='1', extra=''):
    return f'''<CFE><eFact><Encabezado><IdDoc><TipoCFE>111</TipoCFE><Serie>A</Serie><Nro>{number}</Nro><FchEmis>2026-10-01</FchEmis><FmaPago>1</FmaPago></IdDoc><Emisor><RUCEmisor>{SUPPLIER}</RUCEmisor><RznSoc>Proveedor sintético</RznSoc></Emisor><Receptor><DocRecep>{COMPANY}</DocRecep></Receptor><Totales><TpoMoneda>UYU</TpoMoneda><MntNoGrv>100</MntNoGrv><MntTotal>{total}</MntTotal></Totales></Encabezado><Detalle><Item><NroLinDet>1</NroLinDet><NomItem>Producto sintético</NomItem><IndFact>{indicator}</IndFact><MontoItem>100</MontoItem><Cantidad>1</Cantidad></Item>{extra}</Detalle></eFact></CFE>'''.encode()


class WorkflowTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='cerecito-test-')
        self.addCleanup(self.temporary.cleanup)
        self.directory = Path(self.temporary.name)
        self.store = Store(self.directory)
        self.store.configure({'rut': COMPANY, 'name': 'Empresa sintética', 'tolerance': '1.00'})
        self.store.import_catalog([
            {'kind':'supplier','code':'P1','name':'Proveedor sintético','rut':SUPPLIER},
            {'kind':'article','code':'A1','name':'Producto sintético','vat':'1'},
            {'kind':'concept','code':'G1','name':'Gasto sintético','vat':'1'},
            {'kind':'payment','code':'E1','name':'Efectivo'},
            {'kind':'center','code':'C1','name':'Centro sintético'},
        ])
        self.store.import_documents('factura.xml', xml())
        self.key = self.store.invoices()[0]['key']

    def test_supplier_exclusion_applies_to_future_invoices_and_can_be_removed(self):
        self.store.decide(self.key,{'skipSupplier':True})
        self.assertEqual(self.store.invoices()[0]['state'],'skipped')
        self.store.import_documents('next.xml',xml(69))
        key=self.store.invoices()[0]['key']
        self.assertEqual(Store(self.directory).invoices()[0]['state'],'skipped')
        self.store.decide(key,{'skipSupplier':False})
        self.assertNotEqual(self.store.invoices()[0]['state'],'skipped')

    def test_line_choice_fills_pending_compatible_lines_and_preserves_fields(self):
        extra='<Item><NroLinDet>2</NroLinDet><NomItem>Otro producto</NomItem><IndFact>1</IndFact><MontoItem>0</MontoItem><Cantidad>1</Cantidad></Item>'
        self.store.import_documents('two.xml',xml(70,extra=extra))
        key=self.store.invoices()[0]['key']
        self.store.decide(key,{'center':'C1','mode':'credit'})
        self.store.decide(key,{'lines':{'0':{'kind':'article','code':'A1'}}},learn_lines=True)
        self.store.decide(key,{'supplier':'P1'})
        d=self.store.invoices()[0]['decision']
        self.assertEqual(d['lines']['1']['code'],'A1')
        self.assertEqual(d['center'],'C1')
        self.assertEqual(d['mode'],'credit')
        self.store.decide(key,{'destinationKind':'concept'})
        self.assertEqual(self.store.invoices()[0]['decision']['lines'],{})

    def test_auto_filled_lines_do_not_create_unconfirmed_future_rules(self):
        extra='<Item><NroLinDet>2</NroLinDet><NomItem>Producto distinto</NomItem><IndFact>1</IndFact><MontoItem>0</MontoItem><Cantidad>1</Cantidad></Item>'
        self.store.import_documents('autofill.xml',xml(350,extra=extra))
        self.store.decide(self.store.invoices()[0]['key'],{'lines':{'0':{'kind':'article','code':'A1'}}},learn_lines=True)
        with self.store.connect() as db:
            descriptions={row['description'] for row in db.execute('SELECT description FROM rules')}
        self.assertIn('producto sintético',descriptions)
        self.assertNotIn('producto distinto',descriptions)

    def test_choice_uses_same_family_for_another_vat(self):
        self.store.import_catalog([
            {'kind':'article','code':'F1','name':'Familia Exento','vat':'1'},
            {'kind':'article','code':'F3','name':'Familia 22%','vat':'3'}])
        extra='<Item><NroLinDet>2</NroLinDet><NomItem>Otro producto</NomItem><IndFact>3</IndFact><MontoItem>0</MontoItem><Cantidad>1</Cantidad></Item>'
        self.store.import_documents('vat.xml',xml(71,extra=extra))
        key=self.store.invoices()[0]['key']
        self.store.decide(key,{'lines':{'0':{'kind':'article','code':'F1'}}},learn_lines=True)
        self.assertEqual(self.store.invoices()[0]['decision']['lines']['1']['code'],'F3')

    def test_multiple_histories_are_read_together(self):
        from conciliar import run
        from unittest.mock import patch
        folder=self.directory/'entrada/zeta'
        folder.mkdir(parents=True)
        (folder/'a.xlsx').write_bytes(b'a')
        (folder/'b.xlsx').write_bytes(b'b')
        exported=self.directory/'entrada/cerecito'
        exported.mkdir(parents=True)
        (exported/'batch.json').write_text(json.dumps({'formato':'cerecito-agente-v1','comprobantes':[{'clave':'000000000002|111|A|1','proveedor':{'rut':'000000000002'},'cfe':{'tipo':'111','serie':'A','numero':'1'}}]}))
        with patch('conciliar.read_excel',side_effect=[([{'numero':'1'}],[]),([{'numero':'2'}],[])]),patch('conciliar.evaluate',return_value=[]) as evaluate,patch('builtins.print'):
            run(self.directory)
        self.assertEqual(len(evaluate.call_args.args[1]),2)
        queue=json.loads((self.directory/'salida/cola.json').read_text())
        self.assertEqual(queue['origenZeta'],['a.xlsx','b.xlsx'])

    def test_zip_reimport_keeps_choices_and_rechecks_duplicates(self):
        self.choose()
        self.store.decide(self.key,{'skip':True,'center':'C1','reviewed':True})
        jobs=Jobs(self.store,lambda payload,save,progress,cancelled:0)
        source_hash=self.store.invoices()[0]['source']['sha256CFE']
        with self.store.connect() as db:
            db.execute('INSERT INTO jobs VALUES(?,?,?,?,?)',('old',COMPANY,'finished',json.dumps({'items':[]}), '2000-01-01'))
            db.execute('INSERT INTO attempts(job,key,data,created) VALUES(?,?,?,?)',('old',self.key,json.dumps({'estado':'CONFIRMADO','source_hash':source_hash,'supplier_code':'P1'}),'2000-01-01'))
        archive=io.BytesIO()
        with ZipFile(archive,'w') as z:z.writestr('invoice.xml',xml())
        self.store.import_documents('fresh.zip',archive.getvalue())
        invoice=self.store.invoices()[0]
        self.assertEqual(invoice['decision']['lines']['0'],{'kind':'article','code':'A1'})
        self.assertEqual(invoice['decision']['center'],'C1')
        self.assertTrue(invoice['decision']['skip'])
        self.assertEqual(invoice['duplicate_state'],'unchecked')
        self.assertEqual(invoice['state'],'skipped')
        self.assertIsNone(invoice['last_result'])

    def test_learned_article_applies_after_new_zip_and_restart(self):
        self.store.decide(self.key,{'lines':{'0':{'kind':'article','code':'A1'}}},learn_lines=True)
        stream=io.BytesIO()
        with ZipFile(stream,'w') as archive:archive.writestr('new.xml',xml(345))
        self.store.import_documents('next_batch.zip',stream.getvalue())
        invoice=Store(self.directory).invoices()[0]
        self.assertEqual(invoice['proposals'][0]['rule'],{'kind':'article','code':'A1'})
        self.assertEqual(invoice['payload']['lineas'][0]['codigoZeta'],'A1')
        self.assertNotIn('Elegí el destino de la línea 1',invoice['reasons'])

    def test_reset_review_reapplies_learning_and_checks_only_current_pending_documents(self):
        self.store.decide(self.key,{'skip':True})
        outside_key=self.key
        buffer=io.BytesIO()
        with ZipFile(buffer,'w') as archive:
            for number in (622,623,624):
                archive.writestr(f'{number}.xml',xml(number).replace(b'<FmaPago>1',b'<FmaPago>2'))
        batch=self.store.import_documents('reset.zip',buffer.getvalue())
        invoices={i['source']['numero']:i for i in self.store.invoices()}
        pending, skipped, loaded=[invoices[n]['key'] for n in ('622','623','624')]
        self.store.decide(pending,{'supplier':'P1','center':'C1','groupByVat':True,
                                   'lines':{'0':{'kind':'article','code':'A1'}},'reviewed':True},learn_lines=True)
        self.store.decide(skipped,{'skip':True})
        with self.store.connect() as db:
            db.execute('INSERT INTO jobs VALUES(?,?,?,?,?)',('previous',COMPANY,'finished','{}',batch['date']))
            for n,status in [('622','AUSENTE_VERIFICADO'),('624','CONFIRMADO')]:
                item=invoices[n]
                result={'estado':status,'source_hash':item['source']['sha256CFE'],'supplier_code':'P1'}
                db.execute('INSERT INTO attempts(job,key,data,created) VALUES(?,?,?,?)',
                           ('previous',item['key'],json.dumps(result),batch['date']))
        received=[]
        def runner(payload,save,progress,cancelled):
            current={i['key']:i for i in self.store.invoices()}
            self.assertEqual(current[pending]['duplicate_state'],'unchecked')
            self.assertEqual(current[loaded]['state'],'loaded')
            self.assertFalse(save)
            self.assertTrue(payload['duplicates_only'])
            for invoice in payload['comprobantes']:
                received.append(invoice['clave'])
                progress({'clave':invoice['clave'],'estado':'AUSENTE_VERIFICADO'})
            return 0
        jobs=Jobs(self.store,runner)
        self.assertEqual(jobs.start('reset',precheck=True,reset_batch=batch['date']),'reset')
        deadline=time.monotonic()+3
        while jobs.list()[0]['status'] in {'queued','running'} and time.monotonic()<deadline:time.sleep(.01)
        self.assertEqual(jobs.list()[0]['status'],'finished')
        self.assertCountEqual(received,[pending,skipped])
        self.assertEqual(jobs.list()[0]['data']['batch_date'],self.store.settings()['batch']['reset_at'])
        self.assertEqual(self.store.settings()['batch']['date'],batch['date'])
        invoice=next(i for i in self.store.invoices() if i['key']==pending)
        self.assertEqual(invoice['state'],'ready')
        self.assertEqual(invoice['decision'],{})
        self.assertEqual(invoice['suggested_center'],'C1')
        self.assertEqual(invoice['payload']['lineas'][0]['codigoZeta'],'A1')
        self.assertEqual(invoice['duplicate_state'],'absent')
        with self.store.connect() as db:
            decision=db.execute('SELECT decision FROM invoices WHERE company=? AND key=?',(COMPANY,outside_key)).fetchone()[0]
            self.assertTrue(json.loads(decision)['skip'])
        with self.assertRaisesRegex(ValueError,'lote cambió'):
            jobs.start('stale-reset',precheck=True,reset_batch=batch['date'])

    def test_sector_saved_for_supplier_and_reused(self):
        self.store.decide(self.key,{'center':'C1'})
        self.store.import_documents('new.xml',xml(346))
        invoice=Store(self.directory).invoices()[0]
        self.assertEqual(invoice['suggested_center'],'C1')
        self.assertEqual(invoice['payload']['zeta']['centroCosto'],'C1')
        self.store.decide(invoice['key'],{'center':''})
        self.store.import_documents('another.xml',xml(347))
        self.assertEqual(Store(self.directory).invoices()[0]['payload']['zeta']['centroCosto'],'')

    def test_free_line_of_zero_value_is_non_monetary(self):
        from xml_source import preview_net_lines
        extra='<Item><NroLinDet>2</NroLinDet><NomItem>Regalo</NomItem><IndFact>5</IndFact><MontoItem>0</MontoItem><Cantidad>1</Cantidad></Item>'
        self.store.import_documents('bonus.xml',xml(348,extra=extra))
        key=self.store.invoices()[0]['key']
        self.store.decide(key,{'mode':'credit','lines':{'0':{'kind':'article','code':'A1'}}},learn_lines=True)
        invoice=self.store.invoices()[0]
        self.assertEqual(len(invoice['payload']['lineas']),1)
        self.assertEqual(invoice['non_monetary_lines'],[1])
        plan=preview_net_lines(invoice['source'],invoice['payload'],COMPANY)
        self.assertEqual(plan['lineasNoMonetarias'],[1])

    def test_special_indicator_12_never_treated_as_12_percent(self):
        self.store.import_documents('suspenso.xml',xml(349,indicator='12'))
        invoice=self.store.invoices()[0]
        self.assertEqual(invoice['state'],'manual')
        self.assertIn('IVA en suspenso',' '.join(invoice['reasons']))
        self.assertEqual(invoice['payload']['lineas'],[])

    def test_manual_purchases_can_be_checked_for_duplicates_without_becoming_loadable(self):
        buffer=io.BytesIO()
        with ZipFile(buffer,'w') as archive:
            for number in (625,626):
                archive.writestr(f'{number}.xml',xml(number,indicator='12'))
        batch=self.store.import_documents('manual.zip',buffer.getvalue())
        self.assertTrue(all(i['state']=='manual' and i['can_precheck'] for i in self.store.invoices()))
        def runner(payload,save,progress,cancelled):
            self.assertFalse(save)
            self.assertTrue(payload['duplicates_only'])
            for invoice in payload['comprobantes']:
                status='RESULTADOS_EN_ZETA_NO_CARGAR' if invoice['cfe']['numero']=='625' else 'AUSENTE_VERIFICADO'
                progress({'clave':invoice['clave'],'estado':status})
            return 0
        jobs=Jobs(self.store,runner)
        jobs.start('manual-reset',precheck=True,reset_batch=batch['date'])
        deadline=time.monotonic()+3
        while jobs.list()[0]['status'] in {'queued','running'} and time.monotonic()<deadline:time.sleep(.01)
        self.assertEqual(jobs.list()[0]['status'],'finished')
        self.assertEqual(jobs.list()[0]['data']['done'],2)
        self.assertEqual({i['source']['numero']:i['state'] for i in self.store.invoices()},
                         {'625':'existing','626':'manual'})
        with self.assertRaises(ValueError):
            jobs.start('manual-save',save=True)

    def test_existing_invoice_does_not_hide_pending_load_error(self):
        self.store.import_documents('second.xml',xml(68))
        invoices=self.store.invoices()
        existing=dict(invoices[0],state='existing')
        pending=dict(invoices[0],state='needs_review')
        with patch.object(self.store,'invoices',return_value=[existing,pending]):
            with self.assertRaisesRegex(ValueError,'No hay facturas listas'):
                Jobs(self.store).start('existing-and-pending',True)

    def test_usd_plan_keeps_xml_exchange_rate_and_rejects_missing_rate(self):
        import cargar_lote
        content=xml(72).replace(b'<TpoMoneda>UYU</TpoMoneda>',b'<TpoMoneda>USD</TpoMoneda><TpoCambio>40.240</TpoCambio>')
        self.store.import_documents('usd.xml',content)
        self.key=self.store.invoices()[0]['key']
        invoice=self.choose()
        with patch.object(cargar_lote,'company_rut',return_value=COMPANY):
            prepared,plan,profile=cargar_lote.plan_for(invoice['payload'],invoice['source'])
            self.assertEqual(prepared['cfe']['tipoCambio'],'40.240')
            self.assertEqual(prepared['cfe']['moneda'],'USD')
            with self.assertRaisesRegex(ValueError,'cotizacion'):
                cargar_lote.plan_for(invoice['payload'],dict(invoice['source'],tipoCambio=''))

    def test_native_contacts_export_detects_header_and_preserves_existing_rut(self):
        from openpyxl import Workbook
        workbook=Workbook()
        sheet=workbook.active
        sheet.append([])
        sheet.append(['Activo','Marcado'])
        sheet.append([])
        sheet.append(['Codigo','Nombre','Telefono 1','Email','Activo'])
        sheet.append(['P1    ','Nombre actualizado','','','S'])
        sheet.append([9,'Proveedor nuevo','','','S'])
        sheet['A6'].number_format='00000'
        sheet.append([])
        buffer=io.BytesIO();workbook.save(buffer);workbook.close()
        rows=self.store.preview_catalog('ContactosWWExport.xlsx',buffer.getvalue(),'supplier')
        self.assertEqual([r['code'] for r in rows],['P1','00009'])
        self.assertNotIn('rut',rows[0])
        self.store.import_catalog(rows)
        provider=next(c for c in self.store.catalog() if c['kind']=='supplier' and c['code']=='P1')
        self.assertEqual(provider['rut'],SUPPLIER)
        self.assertEqual(provider['name'],'Nombre actualizado')

    def test_provider_recheck_only_checks_requested_invoice(self):
        buffer=io.BytesIO()
        with ZipFile(buffer,'w') as z:
            z.writestr('one.xml',xml())
            z.writestr('two.xml',xml(68))
        self.store.import_documents('batch.zip',buffer.getvalue())
        requested=self.store.invoices()[0]['key']
        seen=[]
        def runner(payload,save,progress,cancelled):
            seen.extend(i['clave'] for i in payload['comprobantes'])
            progress({'clave':requested,'estado':'RESULTADOS_EN_ZETA_NO_CARGAR'})
            return 0
        jobs=Jobs(self.store,runner)
        jobs.start('supplier-recheck',precheck=True,invoice_keys=[requested])
        deadline=time.monotonic()+3
        while jobs.list()[0]['status'] in {'queued','running'} and time.monotonic()<deadline:time.sleep(.01)
        self.assertEqual(seen,[requested])
        self.assertEqual(next(i for i in self.store.invoices() if i['key']==requested)['state'],'existing')
        self.assertEqual(sum(i['state']=='needs_review' for i in self.store.invoices()),1)

    def test_cash_defaults_to_real_cash_catalog_entry(self):
        self.store.decide(self.key,{'mode':'cash','lines':{'0':{'kind':'article','code':'A1'}}})
        invoice=self.store.invoices()[0]
        self.assertEqual(invoice['state'],'ready')
        self.assertEqual(invoice['paymentCode'],'E1')
        self.assertTrue(invoice['paymentDefault'])
        self.assertEqual(invoice['payload']['zeta']['formaPago'],'E1')

    def test_xml_credit_overrides_stale_cash_choice(self):
        self.store.import_documents('credit.xml',xml(73).replace(b'<FmaPago>1</FmaPago>',b'<FmaPago>2</FmaPago>'))
        self.key=self.store.invoices()[0]['key']
        invoice=self.choose('cash')
        self.assertEqual(invoice['mode'],'credit')
        self.assertTrue(invoice['modeLocked'])
        self.assertEqual(invoice['payload']['zeta']['formaPago'],'')
        self.assertEqual(invoice['form'],'Compra Cr\u00e9dito')

    def test_loaded_evidence_survives_failed_recheck_and_supplier_change(self):
        invoice=self.choose()
        source_hash=invoice['source']['sha256CFE']
        with self.store.connect() as db:
            for n,status in enumerate(['CONFIRMADO','BLOQUEADO_ERROR']):
                job='proof-'+str(n)
                db.execute('INSERT INTO jobs VALUES(?,?,?,?,?)',(job,COMPANY,'finished',json.dumps({'items':[]}),'2026-10-16'))
                db.execute('INSERT INTO attempts(job,key,data,created) VALUES(?,?,?,?)',(job,self.key,json.dumps({'estado':status,'source_hash':source_hash,'supplier_code':'P1'}),'2026-10-16'))
        self.store.decide(self.key,{'supplier':'unknown'})
        self.assertEqual(self.store.invoices()[0]['state'],'loaded')

    def test_only_cash_xml_requires_modality_confirmation(self):
        self.store.decide(self.key,{'lines':{'0':{'kind':'article','code':'A1'}}})
        self.assertTrue(self.store.invoices()[0]['modePending'])
        self.store.decide(self.key,{'mode':'cash'})
        self.assertFalse(self.store.invoices()[0]['modePending'])
        self.assertEqual(self.store.invoices()[0]['state'],'ready')

    def choose(self, mode='credit', kind='article'):
        self.store.decide(self.key, {'mode':mode,'payment':'E1','lines':{'0':{'kind':kind,'code':'A1' if kind=='article' else 'G1'}}})
        return self.store.invoices()[0]

    def test_manual_credit_survives_cash_xml_reimport_and_restart(self):
        self.choose()
        self.store.import_documents('same.xml', xml())
        restarted = Store(self.directory)
        self.assertEqual(restarted.invoices()[0]['mode'], 'credit')
        self.assertEqual(restarted.invoices()[0]['state'], 'ready')

    def test_four_forms_derive_independent_decisions(self):
        for mode,kind,expected in [('credit','article','Compra Crédito'),('credit','concept','Compra Crédito Gastos'),('cash','article','Compra Contado'),('cash','concept','Compra Contado Gastos')]:
            with self.subTest(mode=mode,kind=kind):
                invoice=self.choose(mode,kind)
                self.assertEqual(invoice['form'], expected)
                self.assertEqual(invoice['mode'], mode)
                self.assertEqual(invoice['state'], 'ready')

    def test_exempt_amount_preserved(self):
        invoice=self.choose()
        self.assertEqual(invoice['plan']['lineas'][0]['ivaEsperado'],'0.00')
        self.assertEqual(invoice['plan']['lineas'][0]['montoXML'],'100')
        self.assertEqual(invoice['plan']['lineas'][0]['descripcionXML'],invoice['source']['lineas'][0]['descripcion'])

    def test_rounding_tolerance_is_per_document(self):
        for difference,expected in [('0.01','ready'),('1.00','ready'),('1.01','needs_review')]:
            with self.subTest(difference=difference):
                from decimal import Decimal
                number=100+int(Decimal(difference)*100)
                self.store.import_documents('round.xml',xml(number,str(Decimal('100')+Decimal(difference))))
                key=self.store.invoices()[0]['key']
                self.store.decide(key,{'mode':'credit','lines':{'0':{'kind':'article','code':'A1'}}})
                self.assertEqual(self.store.invoices()[0]['state'],expected)

    def test_product_cannot_be_ignored(self):
        self.store.decide(self.key,{'lines':{'0':{'ignore':True}}})
        self.assertIn('No se puede ignorar', ' '.join(self.store.invoices()[0]['reasons']))

    def test_zero_amount_line_can_be_ignored_and_restored_for_any_indicator(self):
        for number, indicator in [(619,'3'),(620,'12')]:
            with self.subTest(indicator=indicator):
                extra=f'<Item><NroLinDet>2</NroLinDet><NomItem>Importe cero</NomItem><IndFact>{indicator}</IndFact><MontoItem>0.00</MontoItem><Cantidad>1</Cantidad></Item>'
                self.store.import_documents('zero.xml',xml(number,extra=extra))
                key=self.store.invoices()[0]['key']
                self.store.decide(key,{'mode':'credit','lines':{'0':{'kind':'article','code':'A1'}}})
                self.assertNotEqual(self.store.invoices()[0]['state'],'ready')
                self.store.decide(key,{'lines':{'1':{'ignore':True}}})
                invoice=Store(self.directory).invoices()[0]
                self.assertEqual(invoice['state'],'ready')
                self.assertEqual(invoice['plan']['lineasIgnoradas'],[1])
                self.assertEqual(invoice['source']['lineas'][1]['monto'],'0.00')
                self.store.decide(key,{'lines':{'1':{'ignore':False}}})
                self.assertNotEqual(self.store.invoices()[0]['state'],'ready')

    def test_group_plan_preserves_origins_and_totals_by_vat(self):
        from cargar_lote import allow_rounding
        self.store.import_catalog([
            {'kind':'article','code':'A10','name':'Artículo 10%','vat':'2'},
            {'kind':'article','code':'A22','name':'Artículo 22%','vat':'3'},
            {'kind':'article','code':'B22','name':'Otro artículo 22%','vat':'3'}])
        extra=''.join(f'<Item><NroLinDet>{n}</NroLinDet><NomItem>Producto {n}</NomItem><IndFact>{vat}</IndFact><MontoItem>{amount}</MontoItem><Cantidad>1</Cantidad></Item>'
                      for n, vat, amount in [(2,2,20),(3,2,30),(4,3,25),(5,3,75)])
        raw=xml(621,'277',extra=extra).replace(b'<MntTotal>',
            b'<MntNetoIvaTasaMin>50</MntNetoIvaTasaMin><MntIVATasaMin>5</MntIVATasaMin>'
            b'<MntNetoIVATasaBasica>100</MntNetoIVATasaBasica><MntIVATasaBasica>22</MntIVATasaBasica><MntTotal>')
        self.store.import_documents('grouped.xml',raw)
        key=self.store.invoices()[0]['key']
        lines={str(i):{'kind':'article','code':code} for i,code in enumerate(('A1','A10','A10','A22','B22'))}
        self.store.decide(key,{'mode':'credit','lines':lines,'groupByVat':True})
        self.assertIn('un solo destino',' '.join(self.store.invoices()[0]['reasons']))
        self.store.decide(key,{'lines':{'4':{'kind':'article','code':'A22'}}})
        invoice=Store(self.directory).invoices()[0]
        self.assertEqual(invoice['state'],'ready')
        grouped=allow_rounding(invoice['plan'])['lineas']
        self.assertEqual([(l['origenes'],l['cantidadXML'],l['totalEsperado']) for l in grouped],
                         [([0],'1','100.00'),([1,2],'1','55.00'),([3,4],'1','122.00')])
        self.assertEqual(len(invoice['payload']['lineas']),5)
        self.store.decide(key,{'groupByVat':False})
        self.assertEqual(len(self.store.invoices()[0]['plan']['lineas']),5)

    def test_non_billable_rounding_can_be_ignored_with_origin(self):
        extra='<Item><NroLinDet>2</NroLinDet><NomItem>Redondeo sintético</NomItem><IndFact>6</IndFact><MontoItem>0.01</MontoItem><Cantidad>1</Cantidad></Item>'
        self.store.import_documents('round.xml',xml(68,'100.01',extra=extra))
        key=self.store.invoices()[0]['key']
        self.store.decide(key,{'mode':'credit','lines':{'0':{'kind':'article','code':'A1'},'1':{'ignore':True}}})
        invoice=self.store.invoices()[0]
        self.assertEqual(invoice['state'],'ready')
        self.assertEqual(invoice['payload']['lineasIgnoradas'],[1])
        self.assertEqual(len(invoice['source']['lineas']),2)
        self.assertEqual(invoice['payload']['lineas'][0]['origenes'],[0])

    def test_confirmed_supplier_persists_when_catalog_rut_changes(self):
        self.store.import_catalog([{'kind':'supplier','code':'P1','name':'Proveedor sintético','rut':''}])
        self.choose()
        self.store.decide(self.key,{'center':'C1','reviewed':True})
        before=self.store.invoices()[0]['decision']
        self.assertNotIn('supplier',before)
        self.assertEqual(self.store.invoices()[0]['state'],'needs_review')
        self.store.approve_supplier(self.key,'P1')
        self.assertEqual(Store(self.directory).invoices()[0]['state'],'ready')
        self.assertEqual(self.store.invoices()[0]['decision'],dict(before,supplier='P1'))
        self.store.import_catalog([{'kind':'supplier','code':'P1','name':'Proveedor sintético','rut':'000000000003'}])
        self.assertEqual(self.store.invoices()[0]['state'],'ready')
        self.assertTrue(self.store.invoices()[0]['payload']['proveedor']['seleccionManual'])

    def test_skip_has_no_missing_data_reasons(self):
        self.store.decide(self.key,{'skip':True})
        invoice=self.store.invoices()[0]
        self.assertEqual(invoice['state'],'skipped')
        self.assertEqual(invoice['reasons'],[])

    def test_supplier_relation_applies_to_new_invoice_after_restart(self):
        self.store.import_catalog([{'kind':'supplier','code':'P1','name':'Proveedor sintético','rut':''}])
        self.store.approve_supplier(self.key,'P1')
        self.store.import_documents('otra.xml',xml(number=68))
        invoice=next(i for i in Store(self.directory).invoices() if i['source']['numero']=='68')
        self.assertEqual(invoice['payload']['proveedor']['codigoZeta'],'P1')
        self.assertFalse(any('relación' in r for r in invoice['reasons']))

    def test_valid_manual_center_is_not_overridden(self):
        self.choose()
        self.store.decide(self.key,{'center':'C1','reviewed':True})
        self.assertEqual(self.store.invoices()[0]['payload']['zeta']['centroCosto'],'C1')
        self.assertEqual(self.store.invoices()[0]['state'],'ready')

    def test_name_match_is_only_a_proposal(self):
        invoice=self.store.invoices()[0]
        self.assertEqual(invoice['state'],'needs_review')
        self.assertEqual(invoice['proposals'][0]['confidence'],.5)

    def test_manual_destination_is_learned_and_survives_restart(self):
        self.store.decide(self.key,{'lines':{'0':{'kind':'article','code':'A1'}}},learn_lines=True)
        self.store.decide(self.key,{'lines':{'0':{'kind':'concept','code':'G1'}}},learn_lines=True)
        restarted=Store(self.directory)
        restarted.import_documents('next.xml',xml(68))
        invoice=restarted.invoices()[0]
        self.assertEqual(invoice['proposals'][0]['rule'],{'kind':'concept','code':'G1'})
        self.assertEqual(invoice['proposals'][0]['confidence'],1)
        self.assertEqual(invoice['payload']['lineas'][0]['codigoZeta'],'G1')
        self.assertEqual(invoice['decision'].get('lines',{}),{})
        restarted.import_documents('different.xml',xml(69).replace(SUPPLIER.encode(),b'000000000003'))
        self.assertIsNone(restarted.invoices()[0]['proposals'][0]['rule'])
        restarted.import_documents('different-vat.xml',xml(70,indicator='3'))
        self.assertIsNone(restarted.invoices()[0]['proposals'][0]['rule'])

    def test_invalid_manual_destination_is_not_learned_or_saved(self):
        with self.assertRaises(ValueError):
            self.store.decide(self.key,{'lines':{'0':{'kind':'article','code':'MISSING'}}},learn_lines=True)
        self.assertEqual(self.store.invoices()[0]['decision'].get('lines',{}),{})
        self.assertIsNone(self.store.invoices()[0]['proposals'][0]['rule'])

    def test_confirmed_rule_works_only_for_matching_supplier_and_vat(self):
        self.choose()
        self.store.rule(self.key,0)
        self.store.import_documents('next.xml',xml(68))
        self.assertEqual(self.store.invoices()[0]['payload']['lineas'][0]['codigoZeta'],'A1')

    def test_catalog_refresh_preserves_invoice_choice(self):
        self.choose()
        self.store.import_catalog([{'kind':'article','code':'A1','name':'Producto renombrado','vat':'1'}])
        self.assertEqual(self.store.invoices()[0]['decision']['lines']['0']['code'],'A1')

    def test_conflicting_xml_same_identity_rejected(self):
        with self.assertRaisesRegex(ValueError,'misma identidad'):
            self.store.import_documents('changed.xml',xml(total='101'))

    def test_last_zip_restored_without_selection(self):
        buffer=io.BytesIO()
        with ZipFile(buffer,'w') as archive:
            archive.writestr('one.xml',xml())
            archive.writestr('two.xml',xml(68))
        self.store.import_documents('FacturaLista.zip',buffer.getvalue())
        restored=Store(self.directory)
        self.assertEqual(restored.settings()['batch']['filename'],'FacturaLista.zip')
        self.assertEqual(len(restored.invoices()),2)

    def test_job_double_click_is_idempotent_and_ui_can_read(self):
        self.choose()
        entered,release=threading.Event(),threading.Event()
        def runner(payload,save,progress,cancelled):
            entered.set();release.wait(3)
            progress({'clave':self.key,'estado':'CONFIRMADO'})
            return 0
        jobs=Jobs(self.store,runner)
        self.addCleanup(release.set)
        self.assertEqual(jobs.start('first',True),'first')
        self.assertTrue(entered.wait(2))
        self.assertEqual(jobs.start('first',True),'first')
        self.assertEqual(jobs.start('second',True),'first')
        with self.assertRaisesRegex(ValueError,'otro trabajo en curso'):
            jobs.start('different-operation',precheck=True)
        self.assertEqual(len(jobs.list()),1)
        self.assertEqual(len(self.store.invoices()),1)
        with self.assertRaises(ValueError):self.store.decide(self.key,{'skip':True})
        release.set()
        deadline=time.monotonic()+3
        while jobs.list()[0]['status']=='running' and time.monotonic()<deadline:time.sleep(.01)
        self.assertEqual(jobs.list()[0]['data']['done'],1)
        with self.assertRaisesRegex(ValueError,'ya fueron cargadas'):jobs.start('third',True)

    def test_lot_cannot_start_without_any_ready_invoice(self):
        with self.assertRaisesRegex(ValueError,'No hay facturas listas'):Jobs(self.store).start('first')

    def test_ready_invoices_can_run_with_pending_and_manual_documents(self):
        buffer=io.BytesIO()
        with ZipFile(buffer,'w') as archive:
            archive.writestr('ready.xml',xml())
            archive.writestr('pending.xml',xml(68))
            archive.writestr('credit-note.xml',xml(69).replace(b'<TipoCFE>111',b'<TipoCFE>112'))
        self.store.import_documents('mixed.zip',buffer.getvalue())
        self.choose()
        states={i['source']['numero']:i['state'] for i in self.store.invoices()}
        self.assertEqual(states,{'67':'ready','68':'needs_review','69':'manual'})
        received=[]
        def runner(payload,save,progress,cancelled):
            received.extend(i['clave'] for i in payload['comprobantes'])
            return 0
        jobs=Jobs(self.store,runner)
        jobs.start('only-ready')
        deadline=time.monotonic()+3
        while jobs.list()[0]['status'] in {'queued','running'} and time.monotonic()<deadline:time.sleep(.01)
        self.assertEqual(received,[self.key])

    def test_cash_is_not_silently_converted_to_credit(self):
        self.choose('cash')
        received=[]
        def runner(payload,save,progress,cancelled):
            received.append(payload['comprobantes'][0])
            progress({'clave':self.key,'estado':'CONFIRMADO'})
            return 0
        jobs=Jobs(self.store,runner)
        jobs.start('cash',True)
        deadline=time.monotonic()+3
        while jobs.list()[0]['status'] in {'queued','running'} and time.monotonic()<deadline:time.sleep(.01)
        self.assertEqual(received[0]['zeta']['comprobante'],'Compra Contado')
        self.assertEqual(received[0]['zeta']['formaPago'],'E1')

    def test_legacy_preview_commit_preserves_mode_lines_and_center(self):
        export=json.dumps({'invoiceOverrides':{self.key:{'voucher':'Compra Contado Gastos','center':'C1','lines':{'0':{'kind':'concept','code':'G1'}},'paymentForm':'E1','reviewed':True}}}).encode()
        self.assertEqual(self.store.import_legacy(export)['count'],1)
        self.assertEqual(self.store.invoices()[0]['decision'],{})
        self.store.import_legacy(export,commit=True)
        invoice=self.store.invoices()[0]
        self.assertEqual(invoice['form'],'Compra Contado Gastos')
        self.assertEqual(invoice['decision']['center'],'C1')

    def test_interrupted_jobs_are_recoverable_history_not_finished(self):
        with self.store.connect() as db:
            db.execute("INSERT INTO jobs VALUES('interrupted',?,'running','{}','date')",(COMPANY,))
        restored=Store(self.directory)
        with restored.connect() as db:
            self.assertEqual(db.execute('SELECT status FROM jobs').fetchone()['status'],'interrupted')

    def test_cancel_is_requested_between_documents(self):
        self.choose()
        entered,release=threading.Event(),threading.Event()
        def runner(payload,save,progress,cancelled):
            entered.set();release.wait(3)
            self.assertTrue(cancelled())
            return 0
        jobs=Jobs(self.store,runner)
        self.addCleanup(release.set)
        jobs.start('cancel')
        self.assertTrue(entered.wait(2))
        jobs.cancel('cancel')
        self.assertEqual(jobs.list()[0]['status'],'cancelling')
        release.set()
        deadline=time.monotonic()+3
        while jobs.list()[0]['status']=='cancelling' and time.monotonic()<deadline:time.sleep(.01)
        self.assertEqual(jobs.list()[0]['status'],'cancelled')

    def test_migrations_are_idempotent_and_preserve_decisions(self):
        self.choose()
        Store(self.directory)
        Store(self.directory)
        with self.store.connect() as db:
            self.assertEqual(db.execute('SELECT count(*) FROM migrations').fetchone()[0],3)
            self.assertEqual(db.execute('SELECT count(*) FROM invoices').fetchone()[0],1)
        self.assertEqual(self.store.invoices()[0]['mode'],'credit')

    def test_precheck_runs_before_line_decisions_and_hides_existing(self):
        def runner(payload,save,progress,cancelled):
            self.assertFalse(save)
            self.assertTrue(payload['duplicates_only'])
            self.assertEqual(payload['comprobantes'][0]['lineas'],[])
            progress({'clave':self.key,'estado':'RESULTADOS_EN_ZETA_NO_CARGAR','docid':'123'})
            return 0
        jobs=Jobs(self.store,runner)
        jobs.start('precheck',precheck=True)
        deadline=time.monotonic()+3
        while jobs.list()[0]['status'] in {'queued','running'} and time.monotonic()<deadline:time.sleep(.01)
        self.assertEqual(jobs.list()[0]['status'],'finished')
        self.assertEqual(self.store.invoices()[0]['state'],'existing')
        self.store.import_documents('same.xml',xml())
        self.assertEqual(Store(self.directory).invoices()[0]['state'],'existing')

    def test_uncertain_duplicate_does_not_hide_invoice(self):
        invoice=self.store.invoices()[0]
        with self.store.connect() as db:
            db.execute("INSERT INTO jobs VALUES('check',?,'finished','{}','date')",(COMPANY,))
            item={'estado':'BLOQUEADO_INCIERTO','source_hash':invoice['source']['sha256CFE'],'supplier_code':'P1'}
            db.execute("INSERT INTO attempts(job,key,data,created) VALUES('check',?,?,'date')",(self.key,json.dumps(item)))
        self.assertEqual(self.store.invoices()[0]['state'],'needs_review')
        self.assertEqual(self.store.invoices()[0]['duplicate_state'],'uncertain')

    def test_sector_sets_each_original_vat_without_changing_manual_mode(self):
        self.store.import_catalog([
            {'kind':'center','code':'AB','name':'A&B'},
            {'kind':'article','code':'FOOD0','name':'Alimentos exento','vat':'1'},
            {'kind':'article','code':'FOOD22','name':'Alimentos 22%','vat':'3'}])
        extra='<Item><NroLinDet>2</NroLinDet><NomItem>Otro producto</NomItem><IndFact>3</IndFact><MontoItem>100</MontoItem><Cantidad>2</Cantidad></Item>'
        raw=xml(68,'222',extra=extra).replace(b'<MntTotal>',b'<MntNetoIVATasaBasica>100</MntNetoIVATasaBasica><MntIVATasaBasica>22</MntIVATasaBasica><MntTotal>')
        self.store.import_documents('mixed.xml',raw)
        key=self.store.invoices()[0]['key']
        self.store.decide(key,{'mode':'credit'})
        sector=next(s for s in self.store.sectors() if s['code']=='AB')
        self.store.apply_sector(key,'AB',sector['mapping'])
        invoice=self.store.invoices()[0]
        self.assertEqual(invoice['state'],'ready')
        self.assertEqual(invoice['mode'],'credit')
        self.assertEqual([l['codigoZeta'] for l in invoice['payload']['lineas']],['FOOD0','FOOD22'])
        self.assertEqual(invoice['source']['lineas'][1]['cantidad'],'2')
        self.assertEqual(invoice['payload']['zeta']['centroCosto'],'AB')
        self.assertTrue(next(s for s in Store(self.directory).sectors() if s['code']=='AB')['configured'])

    def test_sector_missing_vat_does_not_reuse_incompatible_old_choice(self):
        self.choose()
        self.store.import_catalog([{'kind':'article','code':'T22','name':'Gravado 22%','vat':'3'}])
        self.store.apply_sector(self.key,'C1',{'3':{'kind':'article','code':'T22'}})
        invoice=self.store.invoices()[0]
        self.assertEqual(invoice['state'],'needs_review')
        self.assertEqual(invoice['payload']['lineas'],[])


    def test_no_sector_clears_center_and_preserves_line_concepts(self):
        self.store.import_catalog([{'kind':'center','code':'AB','name':'A&B'},
                                   {'kind':'concept','code':'FUEL','name':'Combustible','vat':'1'}])
        self.store.decide(self.key,{'mode':'credit','center':'AB','lines':{'0':{'kind':'concept','code':'FUEL'}}})
        self.store.apply_sector(self.key,'',{})
        invoice=self.store.invoices()[0]
        self.assertEqual(invoice['payload']['zeta']['centroCosto'],'')
        self.assertEqual(invoice['decision']['lines']['0']['code'],'FUEL')
        self.assertEqual(invoice['form'],'Compra Crédito Gastos')
        self.assertEqual(invoice['state'],'ready')

    def test_no_sector_can_apply_concepts_by_vat(self):
        self.store.import_catalog([{'kind':'concept','code':'FUEL','name':'Combustible','vat':'1'}])
        mapping={'1':{'kind':'concept','code':'FUEL'}}
        self.store.apply_sector(self.key,'',mapping)
        invoice=self.store.invoices()[0]
        self.assertEqual(invoice['decision']['center'],'')
        self.assertEqual(invoice['decision']['lines']['0'],mapping['1'])
        self.assertEqual(next(s for s in self.store.sectors() if s['code']=='')['mapping'],mapping)


if __name__=='__main__':unittest.main()
