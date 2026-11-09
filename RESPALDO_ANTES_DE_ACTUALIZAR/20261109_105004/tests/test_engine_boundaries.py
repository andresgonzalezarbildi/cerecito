"""Exercise guarded Zeta reads and fiscal selectors against simulated pages."""
from pathlib import Path
import sys
import unittest
from unittest.mock import MagicMock, patch
from contextlib import nullcontext

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src/backend'))
import cargar_lote
from decimal import Decimal


class EngineBoundaryTests(unittest.TestCase):
    def setUp(self):
        period=patch.object(cargar_lote,'invoice_search_period',return_value=('01/01/26','12/10/26'))
        period.start()
        self.addCleanup(period.stop)
    def test_confirmation_records_invoice_identity_before_single_submit(self):
        import zeta_compras
        page=MagicMock()
        page.locator.return_value.count.return_value=1
        state={'clave':'supplier|111|A|1','plan':{}}
        with patch.object(zeta_compras,'wait_value'),patch.object(zeta_compras,'save_state'),patch.object(zeta_compras,'append_ledger') as ledger:
            zeta_compras.confirm_once(page,state,'cabecera',state_path=Path('synthetic.json'))
        self.assertEqual(ledger.call_args.args[1]['clave'],state['clave'])
        page.locator.return_value.evaluate.assert_called_once()

    def test_partial_header_with_existing_lines_is_never_repeated(self):
        page=MagicMock()
        invoice={'clave':'P|111|A|1','cfe':{'numero':'1','serie':'A','moneda':'UYU'},'proveedor':{'codigoZeta':'P'}}
        previous={'docid':'42','sha256XML':'hash','lineas_confirmadas':[]}
        values={'#vEMPRUT':'R','#vDOCNUMERO':'1','#vDOCSERIE':'A','#vPRVIDDOC':'P','#vCMPIDDOC':'26','#vMNDIDDOC':'1','#GridContainerDataV':'[[1]]'}
        page.locator.side_effect=lambda selector:MagicMock(input_value=MagicMock(return_value=values.get(selector,'')))
        with patch.object(cargar_lote,'plan_for',return_value=(invoice,{}, {'code':'26'})),patch.object(cargar_lote,'company_id',return_value='C'),patch.object(cargar_lote,'click_readonly'),patch.object(cargar_lote,'wait_path'),patch.object(cargar_lote,'finish_lines') as finish:
            with self.assertRaisesRegex(ValueError,'ya tiene lineas'):
                cargar_lote.resume_empty_header(page,invoice,{'sha256XML':'hash'},previous,[{'docid':'42','edit':'edit'}],{'rut':'R','id':'C'})
            finish.assert_not_called()

    def test_generic_numberless_purchase_blocks_duplicate_by_date_and_total(self):
        invoice={'cfe':{'fecha':'2026-10-14','numero':'10507','total':'366'},'proveedor':{'codigoZeta':'G','generico':True}}
        row={'number':'','supplier':'G','text':'Bordados 366,00','docid':'42'}
        with patch.object(cargar_lote,'refresh_list',side_effect=[{'filas':[]},{'filas':[row]}]):
            absent,proof=cargar_lote.absence(MagicMock(),invoice)
        self.assertFalse(absent)
        self.assertEqual(proof[-1]['coincidencias_genericas'],[row])

    def test_cash_payment_keeps_verified_default_without_resubmitting(self):
        import zeta_compras
        page=MagicMock()
        page.locator.return_value.input_value.return_value='366,00'
        invoice={'cfe':{'moneda':'UYU'},'zeta':{'comprobante':'Compra Contado','formaPago':'1','formaPagoNombre':'Efectivo'}}
        state={'docid':'42','totales':{'total':'366'}}
        rows=[{'docid':'42','name':'Efectivo','currency':'1','amount':'366,00'}]
        with patch.object(zeta_compras,'click_readonly'),patch.object(zeta_compras,'wait_path'),patch.object(zeta_compras,'read_payment_rows',return_value=rows),patch.object(zeta_compras,'confirm_once') as confirm:
            zeta_compras.finish_cash_payment(page,invoice,state,Path('synthetic.json'))
            confirm.assert_not_called()
        self.assertEqual(state['pago_verificado']['importe'],'366')

    def test_67_does_not_match_partial_number_or_amount(self):
        invoice={'cfe':{'fecha':'2026-10-01','numero':'67'},'proveedor':{'codigoZeta':'P1'}}
        rows=[{'number':'944867','supplier':'P1'}, {'number':'71267','supplier':'P1'}, {'number':'12','supplier':'P1','text':'total 67'}, {'number':'67','supplier':'P2'}]
        with patch.object(cargar_lote,'refresh_list',return_value={'filas':rows,'consulta_completa':True}) as query:
            absent,_=cargar_lote.absence(MagicMock(),invoice)
            self.assertTrue(absent)
            self.assertTrue(query.call_args.kwargs['require_complete'])

    def test_same_supplier_number_other_series_requires_verification(self):
        invoice={'cfe':{'fecha':'2026-10-01','numero':'67','serie':'A'},'proveedor':{'codigoZeta':'P1'}}
        with patch.object(cargar_lote,'refresh_list',return_value={'filas':[{'number':'67','supplier':'P1','series':'B'}]}):
            self.assertFalse(cargar_lote.absence(MagicMock(),invoice)[0])

    def test_incomplete_duplicate_query_cannot_authorize_save(self):
        invoice={'cfe':{'fecha':'2026-10-01','numero':'67'},'proveedor':{'codigoZeta':'P1'}}
        with patch.object(cargar_lote,'refresh_list',side_effect=RuntimeError('consulta paginada')):
            with self.assertRaises(RuntimeError):cargar_lote.absence(MagicMock(),invoice)

    def test_partial_search_cannot_prove_absence(self):
        invoice={'cfe':{'fecha':'2026-10-01','numero':'67'},'proveedor':{'codigoZeta':'P1'}}
        with patch.object(cargar_lote,'refresh_list',return_value={'filas':[],'consulta_completa':False}):
            with self.assertRaisesRegex(ValueError,'incompleta'):cargar_lote.absence(MagicMock(),invoice)

    def test_exempt_selector_is_checked_before_setting(self):
        page=MagicMock()
        page.locator.return_value.evaluate_all.return_value=[{'value':'0','text':'-'}]
        page.locator.return_value.input_value.return_value='0'
        with patch.object(cargar_lote,'set_control') as setter:
            cargar_lote.apply_line_tax(page,{'indicadorIVA':'1'},{'destination':'article'})
            setter.assert_called_once_with(page,'#IVAIDLIN','0')
        page.locator.return_value.evaluate_all.return_value=[{'value':'7','text':'Otro'}]
        with patch.object(cargar_lote,'set_control') as setter:
            with self.assertRaises(ValueError):cargar_lote.apply_line_tax(page,{'indicadorIVA':'1'},{'destination':'article'})
            setter.assert_not_called()

    def test_zeta_rounding_limit_applies_to_each_document_total(self):
        with patch.object(cargar_lote,'ROUNDING_TOLERANCE',Decimal('1')):
            self.assertEqual(cargar_lote.rounding_differences({'total':'101'},{'total':'100'}),{'total':'1'})
            with self.assertRaises(ValueError):cargar_lote.rounding_differences({'total':'101.01'},{'total':'100'})

    def test_actual_pagination_controls_collect_all_pages(self):
        import zeta_compras
        position=[1]
        page=MagicMock()
        pagination=MagicMock()
        pagination.inner_text.side_effect=lambda:f'Página {position[0]} de 3'
        pagination.locator.return_value.count.return_value=1
        grid=MagicMock()
        grid.evaluate.side_effect=lambda js:[{'number':['944867','71267','67'][position[0]-1],'supplier':'P1','series':'A','docid':str(position[0])}]
        filters={'#vDOCFECHA1':'01/01/26','#vDOCFECHA1_TO':'13/10/26','#vFILTERFULLTEXT':'67'}
        def locator(selector):
            if selector=='#GRIDPAGINATIONBARContainer_DVPaginationBar':return pagination
            if selector=='#GridContainerTbl':return grid
            value=MagicMock();value.input_value.return_value=filters[selector];return value
        page.locator.side_effect=locator
        page.expect_response.side_effect=lambda *args,**kwargs:nullcontext(MagicMock())
        with patch.object(zeta_compras,'set_control'),patch.object(zeta_compras,'click_readonly',side_effect=lambda target:position.__setitem__(0,position[0]+1)),patch.object(zeta_compras,'wait_value',side_effect=lambda read,valid,message:self.assertTrue(valid(read()))):
            result=zeta_compras.refresh_list(page,'01/01/26','13/10/26','67')
            position[0]=1
            early=zeta_compras.refresh_list(page,'01/01/26','13/10/26','67',stop_when=lambda row:row['number']=='944867')
        self.assertEqual([r['number'] for r in result['filas']],['944867','71267','67'])
        self.assertEqual(result['paginas'],3)
        self.assertTrue(result['consulta_completa'])
        self.assertFalse(early['consulta_completa'])
        self.assertEqual(len(early['filas']),1)


if __name__=='__main__':unittest.main()
