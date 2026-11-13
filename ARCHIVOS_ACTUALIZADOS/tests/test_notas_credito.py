"""Credit note flows: synthetic documents; never invoke the production browser."""
import sys
import tempfile
import unittest
from pathlib import Path
from decimal import Decimal

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src/backend'))
from workspace_store import Store
from xml_source import preview_net_lines, valid_credit_note_reference
from conciliar import invoice_issues
from cargar_lote import PROFILES, plan_for
from unittest.mock import patch

COMPANY='000000000001'
SUPPLIER='000000000002'


def note(number=501, *, reference=True, form_payment='2', total='100'):
    ref='''<Referencia><NroLinRef>1</NroLinRef><TpoDocRef>111</TpoDocRef><Serie>A</Serie><NroDocRef>22</NroDocRef></Referencia>''' if reference else ''
    return f'''<CFE><eFact><Encabezado><IdDoc><TipoCFE>112</TipoCFE><Serie>B</Serie><Nro>{number}</Nro><FchEmis>2026-10-21</FchEmis><FmaPago>{form_payment}</FmaPago></IdDoc><Emisor><RUCEmisor>{SUPPLIER}</RUCEmisor><RznSoc>Proveedor sintético</RznSoc></Emisor><Receptor><DocRecep>{COMPANY}</DocRecep></Receptor><Totales><TpoMoneda>UYU</TpoMoneda><MntNoGrv>100</MntNoGrv><MntTotal>{total}</MntTotal></Totales></Encabezado><Detalle><Item><NroLinDet>1</NroLinDet><NomItem>Producto sintético</NomItem><IndFact>1</IndFact><MontoItem>100</MontoItem><Cantidad>1</Cantidad></Item></Detalle>{ref}</eFact></CFE>'''.encode()


class CreditNoteTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(prefix='cerecito-credit-note-')
        self.addCleanup(self.tmp.cleanup)
        self.store=Store(self.tmp.name)
        self.store.configure({'rut':COMPANY,'name':'Empresa prueba','tolerance':'1.00'})
        self.store.import_catalog([
            {'kind':'supplier','code':'P1','name':'Proveedor sintético','rut':SUPPLIER},
            {'kind':'article','code':'A1','name':'Artículo exento','vat':'1'},
            {'kind':'concept','code':'G1','name':'Gasto exento','vat':'1'},
            {'kind':'payment','code':'E1','name':'Efectivo'},
            {'kind':'center','code':'C1','name':'Centro'}
        ])

    def load(self,content=None,kind='article'):
        self.store.import_documents('note.xml',content or note())
        invoice=self.store.invoices()[0]
        self.store.decide(invoice['key'],{'mode':'credit','lines':{'0':{'kind':kind,'code':'A1' if kind=='article' else 'G1'}}})
        return self.store.invoices()[0]

    def test_credit_note_is_routed_to_purchase_credit_note(self):
        invoice=self.load()
        self.assertEqual(invoice['form'],'Nota de Crédito Compra')
        self.assertEqual(invoice['state'],'ready')
        self.assertEqual(invoice['payload']['tipoOperacion'],'nota_credito_compra')
        self.assertEqual(invoice['payload']['referencias'][0]['numero'],'22')
        self.assertEqual(invoice['plan']['signoContable'],-1)
        self.assertEqual(Decimal(invoice['plan']['total']),Decimal('100'))
        self.assertEqual(len(invoice['plan']['lineas']),1)
        self.assertEqual(PROFILES[invoice['form']]['code'],'22')
        with patch('cargar_lote.company_rut',return_value=COMPANY):
            prepared,plan,profile=plan_for(invoice['payload'],invoice['source'])
        self.assertTrue(profile['credit_note'])
        self.assertEqual(plan['signoContable'],-1)
        self.assertEqual(invoice_issues(prepared),[])

    def test_expense_credit_note_uses_expense_note_profile(self):
        invoice=self.load(kind='concept')
        self.assertEqual(invoice['form'],'Nota de Crédito Compra Gastos')
        self.assertEqual(invoice['state'],'ready')
        self.assertEqual(PROFILES[invoice['form']]['code'],'27')
        with patch('cargar_lote.company_rut',return_value=COMPANY):
            _,plan,profile=plan_for(invoice['payload'],invoice['source'])
        self.assertEqual(profile['destination'],'concept')
        self.assertEqual(plan['signoContable'],-1)

    def test_credit_note_without_reference_is_manual(self):
        invoice=self.load(note(502,reference=False))
        self.assertEqual(invoice['state'],'manual')
        self.assertIn('sin referencia fiscal',' '.join(invoice['reasons']))
        self.assertTrue(invoice_issues(invoice['payload']))

    def test_canonical_nested_reference_reads_number_without_empty_container(self):
        raw=note().replace(b'NroDocRef',b'NroCFERef')
        start=raw.index(b'<Referencia>');end=raw.index(b'</Referencia>')+len(b'</Referencia>')
        raw=raw[:start]+b'<Referencia>'+raw[start:end]+b'</Referencia>'+raw[end:]
        invoice=self.load(raw)
        self.assertEqual(invoice['state'],'ready')
        self.assertEqual(invoice['source']['numero'],'501')
        self.assertEqual(invoice['source']['referencias'],[{'tipo':'111','serie':'A','numero':'22','global':'','motivo':''}])
        with self.store.connect() as db:
            source=dict(invoice['source'],referencias=[{'tipo':'111','serie':'A','numero':'','global':'','motivo':''}])
            import json
            db.execute('UPDATE invoices SET source=? WHERE key=?',(json.dumps(source),invoice['key']))
        self.store.import_documents('note.xml',raw)
        self.assertEqual(self.store.invoices()[0]['state'],'ready')
        self.assertEqual(self.store.invoices()[0]['payload']['referencias'][0]['numero'],'22')

    def test_contradictory_reference_numbers_are_rejected(self):
        raw=note().replace(b'</NroDocRef>',b'</NroDocRef><NroCFERef>23</NroCFERef>')
        with self.assertRaisesRegex(ValueError,'contradictorios'):
            self.store.import_documents('bad-reference.xml',raw)

    def test_note_with_canonical_reference_and_negative_payable_rounding(self):
        self.store.import_catalog([{'kind':'article','code':'A22','name':'Bebidas 22%','vat':'3'},
                                   {'kind':'article','code':'R','name':'Redondeo','vat':'1'}])
        values=[('4754.46','12'),('5732.82','2'),('3840.00','6'),('2652.06','6')]
        items=''.join(f'<Item><NroLinDet>{index+1}</NroLinDet><NomItem>Bebida</NomItem><IndFact>3</IndFact><MontoItem>{amount}</MontoItem><Cantidad>{quantity}</Cantidad></Item>' for index,(amount,quantity) in enumerate(values))
        items+='<Item><NroLinDet>5</NroLinDet><NomItem>Redondeo</NomItem><IndFact>7</IndFact><MontoItem>0.34</MontoItem><Cantidad>1</Cantidad></Item>'
        raw=note(29757,total='16979.34').replace(b'NroDocRef',b'NroCFERef')
        raw=raw.replace(b'</IdDoc>',b'<MntBruto>1</MntBruto></IdDoc>')
        raw=raw.replace(b'<MntNoGrv>100</MntNoGrv>',b'<MntNetoIVATasaBasica>13917.49</MntNetoIVATasaBasica><MntIVATasaBasica>3061.85</MntIVATasaBasica><MntPagar>16979</MntPagar>')
        start=raw.index(b'<Detalle>');end=raw.index(b'</Detalle>')+len(b'</Detalle>')
        raw=raw[:start]+b'<Detalle>'+items.encode()+b'</Detalle>'+raw[end:]
        self.store.import_documents('round-note.xml',raw)
        invoice=self.store.invoices()[0]
        self.store.decide(invoice['key'],{'groupByVat':True,'roundingDestination':{'kind':'article','code':'R'},
                                         'lines':{str(index):{'kind':'article','code':'A22'} for index in range(4)}})
        invoice=self.store.invoices()[0]
        self.assertEqual(invoice['state'],'ready',invoice['reasons'])
        with patch('cargar_lote.company_rut',return_value=COMPANY):
            _,plan,profile=plan_for(invoice['payload'],invoice['source'])
        self.assertTrue(profile['credit_note'])
        self.assertEqual(plan['totalesXML']['total'],'16979.34')
        self.assertEqual(plan['totalesZeta']['total'],'16979.00')
        self.assertEqual(plan['lineas'][-1]['totalEsperado'],'-0.34')

    def test_cash_note_stays_blocked_until_inflow_adapter_validated(self):
        self.store.import_documents('cash.xml',note(503,form_payment='1'))
        invoice=self.store.invoices()[0]
        self.store.decide(invoice['key'],{'mode':'cash','lines':{'0':{'kind':'article','code':'A1'}}})
        invoice=self.store.invoices()[0]
        self.assertEqual(invoice['form'],'Devolución Compra')
        self.assertNotEqual(invoice['state'],'ready')
        with self.assertRaisesRegex(ValueError,'reintegro a caja'):
            with patch('cargar_lote.company_rut',return_value=COMPANY):
                plan_for(invoice['payload'],invoice['source'])

    def test_note_xml_requires_fiscal_positive_total(self):
        invoice=self.load(note(504,total='0'))
        self.assertNotEqual(invoice['state'],'ready')
        self.assertTrue(any('positivo' in s for s in invoice['reasons']))

    def test_reference_validation_requires_real_reference_or_global_reason(self):
        self.assertFalse(valid_credit_note_reference([{'tipo':'111'}]))
        self.assertTrue(valid_credit_note_reference([{'tipo':'111','serie':'A','numero':'6'}]))
        self.assertFalse(valid_credit_note_reference([{'global':'1','motivo':''}]))
        self.assertTrue(valid_credit_note_reference([{'global':'1','motivo':'ajuste general'}]))


if __name__=='__main__':
    unittest.main()
