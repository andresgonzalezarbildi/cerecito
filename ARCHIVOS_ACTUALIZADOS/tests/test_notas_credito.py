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
