"""Exportación y aislamiento del lote sin entrar a Zeta ni guardar facturas."""
from datetime import date
from http.server import ThreadingHTTPServer
import io
import json
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, build_opener, ProxyHandler
from zipfile import ZipFile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src/backend'))
sys.path.insert(0, str(ROOT))
from workspace_store import Store, batch_revision
from workspace_jobs import Jobs
from zeta_export import period, download_excel, validate_export, apply_export_filters
from test_workflow import xml, COMPANY
import launcher


def workbook(path, count=2):
    from openpyxl import Workbook
    book = Workbook()
    sheet = book.active
    sheet.append(['01/10/2026', 'al', '13/11/2026'])
    sheet.append(['Fecha', 'Tipo', 'Nº', 'Proveedor', 'Moneda', 'Total'])
    for index in range(count):
        sheet.append(['01/10/26', 'Compra Crédito', index+1, 'Proveedor', '$', 100])
    book.save(path)


class ExportTests(unittest.TestCase):
    def test_earliest_emission_not_zip_filename_and_future_dates_covered(self):
        invoices = [{'source': {'fecha': value}} for value in ['2026-11-09', '2026-10-31', '2026-11-15']]
        self.assertEqual(period(invoices, date(2026, 11, 13)), ('2026-10-31', '2026-11-15'))
        with self.assertRaises(ValueError):period([])

    def test_export_reads_workbook_without_grid_row_count(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'Compras.xlsx'
            workbook(path, 125)
            self.assertEqual(validate_export(path), 125)
            workbook(path, 0)
            self.assertEqual(validate_export(path), 0)
            path.write_bytes(b'not an Excel file')
            with self.assertRaises(Exception):validate_export(path)

    def test_filters_refresh_once_without_reading_grid_or_pagination(self):
        from unittest.mock import MagicMock
        page = MagicMock()
        values = {'#vDOCFECHA1':'31/10/26', '#vDOCFECHA1_TO':'13/11/26', '#vFILTERFULLTEXT':''}
        def locator(selector):
            self.assertIn(selector, values)  # Grid and pagination must never be read.
            field = MagicMock();field.input_value.return_value = values[selector]
            return field
        page.locator.side_effect = locator
        with patch('zeta_compras.set_control') as set_control:
            apply_export_filters(page, '31/10/26', '13/11/26')
            self.assertEqual([call.args[1:] for call in set_control.call_args_list], list(values.items()))
        self.assertEqual(page.expect_response.call_count, 1)
        self.assertEqual(page.evaluate.call_args_list[0].args, ("gx.O.executeServerEvent('REFRESH', true, null, false, false)",))
        page.expect_response.return_value.__enter__.return_value.value.finished.assert_called_once()

    def test_click_once_and_read_completed_file_without_download_events(self):
        from unittest.mock import MagicMock
        with tempfile.TemporaryDirectory() as folder:
            directory=Path(folder)
            (directory/'anterior.xlsx').write_bytes(b'old')
            page=MagicMock()
            session=page.context.browser.new_browser_cdp_session.return_value
            target=directory/'ComprasExport-123.xlsx'
            partial=directory/'ComprasExport-123.xlsx.crdownload'
            clicks=[]
            def click(**kwargs):
                clicks.append(kwargs);partial.write_bytes(b'partial')
            def finish(ms):
                partial.rename(target)
            page.locator.return_value.click.side_effect=click
            page.wait_for_timeout.side_effect=finish
            messages=[]
            self.assertEqual(download_excel(page,folder,lambda:False,progress=messages.append),target)
            page.locator.assert_called_once_with('#BTNEXPORT')
            self.assertEqual(clicks,[{'timeout':10000,'no_wait_after':True}])
            self.assertEqual(len(messages),2)
            self.assertEqual(session.send.call_args_list[-1].args,
                             ('Browser.setDownloadBehavior',{'behavior':'default','eventsEnabled':False}))
            session.detach.assert_called_once()
            # No downloadWillBegin/Progress was emitted; the completed file suffices.

    def test_download_cancel_timeout_and_click_error_restore_native_downloads(self):
        from unittest.mock import MagicMock
        with tempfile.TemporaryDirectory() as folder:
            for scenario in ('cancel','timeout','click_error'):
                page=MagicMock()
                session=page.context.browser.new_browser_cdp_session.return_value
                if scenario=='click_error':page.locator.return_value.click.side_effect=ValueError('click failed')
                with self.subTest(scenario=scenario),self.assertRaises(Exception):
                    download_excel(page,folder,lambda:scenario=='cancel',timeout=0)
                self.assertEqual(session.send.call_args_list[-1].args,
                                 ('Browser.setDownloadBehavior',{'behavior':'default','eventsEnabled':False}))
                session.detach.assert_called_once()

    def test_export_downloads_directly_without_duplicate_control_or_page_scan(self):
        from contextlib import nullcontext
        from unittest.mock import MagicMock
        from zeta_export import export
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'Compras.xlsx';workbook(path,125)
            cfg={'empresa_rut':COMPANY,'chrome':{'endpoint':'http://127.0.0.1:9223'}}
            calls=[]
            with patch('runtime_paths.config',return_value=cfg),patch('zeta_bot.exclusive_lock',return_value=nullcontext()), \
                 patch('playwright.sync_api.sync_playwright'),patch('chrome_connection.connect_chrome'), \
                 patch('chrome_connection.choose_page'),patch('zeta_session.prepare_session',side_effect=lambda *args:calls.append('session')), \
                 patch('zeta_compras.refresh_list',side_effect=AssertionError('Must not scan purchases')), \
                 patch('cargar_lote.verify_company',side_effect=AssertionError('Must not search control document')), \
                 patch('zeta_export.apply_export_filters',side_effect=lambda p,s,e:calls.append(('filters',s,e))), \
                 patch('cdp_transport.WebSocket.connect',side_effect=AssertionError('Must reuse active browser connection')), \
                 patch('zeta_export.download_excel',side_effect=lambda *args,**kwargs:(calls.append('download') or path)):
                result=export(folder,'2026-10-31','2026-11-13',COMPANY,lambda message:None,lambda:False)
            self.assertEqual(calls,['session',('filters','31/10/26','13/11/26'),'download'])
            self.assertEqual(result['rows'],125)


class ComparisonTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = Store(self.temp.name)
        self.store.configure({'rut': COMPANY, 'name': 'Prueba', 'tolerance': '1'})
        content = io.BytesIO()
        with ZipFile(content, 'w') as archive:archive.writestr('factura.xml', xml())
        self.original = content.getvalue()
        self.store.import_documents('cfes_2026-11-07_2026-11-10.zip', self.original)
        self.entered, self.release = threading.Event(), threading.Event()
        self.addCleanup(self.release.set)
        def exporter(folder, start, end, company, progress, cancelled):
            self.entered.set();self.release.wait(3)
            path = folder / 'ComprasExport.xlsx';workbook(path, 1)
            return {'path': str(path), 'filename': path.name, 'rows': 1, 'from': start, 'to': end, 'company': company}
        self.jobs = Jobs(self.store, exporter=exporter)

    def start(self):
        result = self.jobs.start_comparison('compare', batch_revision(self.store.settings()['batch']))
        self.assertTrue(self.entered.wait(2))
        return result

    def finish(self):
        self.release.set()
        deadline = time.monotonic()+4
        while self.jobs.list()[0]['status'] in {'queued','running','cancelling'} and time.monotonic()<deadline:time.sleep(.01)
        self.assertEqual(self.jobs.list()[0]['status'], 'finished')

    def test_serializes_browser_work_without_invoice_attempts_and_keeps_snapshot(self):
        revision = batch_revision(self.store.settings()['batch'])
        with self.assertRaisesRegex(ValueError, 'lote cambió'):
            self.jobs.start_comparison('stale', 'previous')
        self.start()
        self.assertEqual(self.jobs.start_comparison('retry', revision), 'compare')
        with self.assertRaisesRegex(ValueError, 'trabajo en curso'):
            self.jobs.start('load', save=True)
        with self.assertRaises(ValueError):self.store.import_documents('next.xml', xml(88))
        self.finish()
        self.assertEqual(self.jobs.comparison_file('compare', 'zip'), self.original)
        self.assertTrue(self.jobs.comparison_file('compare', 'excel').startswith(b'PK'))
        with self.store.connect() as db:self.assertEqual(db.execute('SELECT COUNT(*) FROM attempts').fetchone()[0], 0)
        data = self.jobs.list()[0]['data']
        Path(data['export']['path']).write_bytes(b'altered')
        with self.assertRaisesRegex(ValueError, 'cambió después'):
            self.jobs.comparison_file('compare','excel')

    def test_reimport_hides_old_comparison_and_cancel_returns_no_report(self):
        self.start();self.finish()
        self.store.import_documents('next.xml', xml(88))
        with self.assertRaisesRegex(ValueError, 'lote anterior'):
            self.jobs.comparison_file('compare','zip')
        self.release.clear();self.entered.clear()
        self.jobs.start_comparison('cancel', batch_revision(self.store.settings()['batch']))
        self.assertTrue(self.entered.wait(2))
        self.jobs.cancel('cancel');self.release.set()
        deadline = time.monotonic()+3
        while self.jobs.list()[0]['status'] in {'queued','running','cancelling'} and time.monotonic()<deadline:time.sleep(.01)
        self.assertEqual(self.jobs.list()[0]['status'], 'cancelled')
        with self.assertRaisesRegex(ValueError,'no está lista'):
            self.jobs.comparison_file('cancel','excel')

    def test_file_endpoint_requires_token_and_serves_only_current_job(self):
        self.start();self.finish()
        with patch.object(launcher,'SERVICE',self.store), patch.object(launcher,'JOB_SERVICE',self.jobs):
            server = ThreadingHTTPServer(('127.0.0.1',0),launcher.Handler)
            thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
            base=f'http://127.0.0.1:{server.server_port}/api/comparison/file?job=compare&part='
            opener=build_opener(ProxyHandler({}))
            try:
                with self.assertRaises(HTTPError) as error:opener.open(base+'zip')
                self.assertEqual(error.exception.code,403)
                with opener.open(Request(base+'zip',headers={'X-Cerecito-Token':launcher.SESSION_TOKEN})) as response:
                    self.assertEqual(response.read(),self.original)
                with self.assertRaises(HTTPError):
                    opener.open(Request(base+'../../configuracion_zeta.json',headers={'X-Cerecito-Token':launcher.SESSION_TOKEN}))
            finally:server.shutdown();server.server_close();thread.join(2)


if __name__=='__main__':unittest.main()
