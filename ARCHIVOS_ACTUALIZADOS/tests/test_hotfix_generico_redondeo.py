"""Cobertura sintética sin navegador ni escrituras en Zeta."""
import sys
from pathlib import Path
import tempfile
import unittest
from decimal import Decimal
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src/backend'))
from workspace_store import Store
from xml_source import preview_net_lines
from zeta_compras import validate_supplier_catalog
from test_workflow import COMPANY, SUPPLIER, xml


class HotfixTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store=Store(self.tmp.name)
        self.store.configure({'rut':COMPANY,'name':'Empresa de prueba','tolerance':'1.00'})
        self.store.import_catalog([
            {'kind':'supplier','code':'P1','name':'Proveedor de prueba','rut':SUPPLIER},
            {'kind':'supplier','code':'G','name':'Proveedor genérico','rut':'000000000099'},
            {'kind':'article','code':'A1','name':'Artículo genérico exento','vat':'1'},
            {'kind':'payment','code':'E1','name':'Efectivo'},
        ])
        self.store.import_documents('one.xml',xml(610))

    def chosen(self):
        invoice=self.store.invoices()[0]
        self.store.decide(invoice['key'],{'supplier':'P1','mode':'credit','lines':{'0':{'kind':'article','code':'A1'}}})
        return self.store.invoices()[0]

    def test_generico_elegido_permite_rut_diferente_sin_correlacion_global(self):
        one=self.chosen()
        self.store.decide(one['key'],{'supplier':'G'})
        one=self.store.invoices()[0]
        self.assertEqual(one['state'],'ready')
        self.assertTrue(one['payload']['proveedor']['generico'])
        self.assertEqual(one['payload']['proveedor']['rut'],SUPPLIER)
        self.store.import_documents('two.xml',xml(611))
        next_invoice=Store(self.tmp.name).invoices()[0]
        self.assertEqual(next_invoice['payload']['proveedor']['codigoZeta'],'P1')
        self.assertFalse(next_invoice['payload']['proveedor'].get('generico',False))

    def test_motor_solo_acepta_generico_elegido_y_codigo_exactamente_igual(self):
        actual={'codigoZeta':'G','rut':'000000000099','nombre':'Proveedor genérico'}
        supplier={'codigoZeta':'G','rut':SUPPLIER,'generico':True}
        validate_supplier_catalog(supplier,actual,{})
        with self.assertRaisesRegex(RuntimeError,'contradictorio'):
            validate_supplier_catalog({'codigoZeta':'G','rut':SUPPLIER},actual,{})
        with self.assertRaisesRegex(RuntimeError,'Código'):
            validate_supplier_catalog(supplier,{**actual,'codigoZeta':'OTRO'}, {})

    def test_redondeo_indfact6_automatico_con_control_de_total(self):
        extra='<Item><NroLinDet>2</NroLinDet><NomItem>Redondeo</NomItem><IndFact>6</IndFact><MontoItem>0.01</MontoItem><Cantidad>1</Cantidad></Item>'
        self.store.import_documents('round.xml',xml(612,'100.01',extra=extra))
        i=self.chosen()
        self.assertEqual(i['state'],'ready')
        self.assertEqual(i['auto_rounding_lines'],[1])
        self.assertEqual(len(i['payload']['lineas']),1)
        self.assertEqual(i['plan']['lineasRedondeo'],[1])
        self.assertEqual(i['source']['lineas'][1]['monto'],'0.01')

    def test_redondeo_negativo_iva_1_es_omitido_si_es_pequeno(self):
        extra='<Item><NroLinDet>2</NroLinDet><NomItem>Ajuste por redondeo</NomItem><IndFact>1</IndFact><MontoItem>-0.01</MontoItem><Cantidad>1</Cantidad></Item>'
        data=xml(613,'99.99',extra=extra).replace(b'<MntNoGrv>100</MntNoGrv>',b'<MntNoGrv>99.99</MntNoGrv>')
        self.store.import_documents('round-negative.xml',data)
        i=self.chosen()
        self.assertEqual(i['state'],'ready')
        self.assertEqual(i['auto_rounding_lines'],[1])
        self.assertEqual(len(i['payload']['lineas']),1)

    def test_descuento_normal_no_se_omite(self):
        extra='<Item><NroLinDet>2</NroLinDet><NomItem>Descuento de campaña</NomItem><IndFact>1</IndFact><MontoItem>-0.01</MontoItem><Cantidad>1</Cantidad></Item>'
        self.store.import_documents('discount.xml',xml(614,'99.99',extra=extra))
        i=self.chosen()
        self.assertEqual(i['auto_rounding_lines'],[])
        self.assertNotEqual(i['state'],'ready')

    def test_redondeo_de_cinco_pesos_no_se_omite(self):
        extra='<Item><NroLinDet>2</NroLinDet><NomItem>Redondeo</NomItem><IndFact>1</IndFact><MontoItem>-5</MontoItem><Cantidad>1</Cantidad></Item>'
        self.store.import_documents('large.xml',xml(615,'95',extra=extra))
        i=self.chosen()
        self.assertEqual(i['auto_rounding_lines'],[])
        self.assertNotEqual(i['state'],'ready')

    def test_iva_especial_no_se_trata_como_redondeo(self):
        extra='<Item><NroLinDet>2</NroLinDet><NomItem>Redondeo</NomItem><IndFact>12</IndFact><MontoItem>0.01</MontoItem><Cantidad>1</Cantidad></Item>'
        self.store.import_documents('special.xml',xml(616,'100.01',extra=extra))
        i=self.chosen()
        self.assertEqual(i['auto_rounding_lines'],[])
        self.assertEqual(i['state'],'manual')

if __name__=='__main__':unittest.main()
