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
from zeta_export import period, download_excel, validate_export
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

    def test_export_rejects_only_one_page_and_accepts_empty_complete_export(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'Compras.xlsx'
            workbook(path)
            self.assertEqual(validate_export(path, [{}, {}]), 2)
            with self.assertRaisesRegex(ValueError, 'incompleta'):
                validate_export(path, [{}, {}, {}])
            workbook(path, 0)
            self.assertEqual(validate_export(path, []), 0)

    def test_native_name_and_restore_downloads_on_success_failure_and_cancel(self):
        class Session:
            def send(self, method):return {'frameTree': {'frame': {'id': 'chosen-frame'}}}
            def detach(self):pass
        class Context:
            def new_cdp_session(self, page):return Session()
        class Page:
            context = Context()
            def locator(self, selector):
                if selector != '#BTNEXPORT':raise AssertionError(selector)
                return self
            def click(self, **kwargs):pass
        class Connection:
            def __init__(self, events, targets=None, interleave=False):
                self.events = list(events);self.commands = [];self.reply = None
                self.targets=targets or [];self.interleave=interleave
            def send_json(self, value):
                self.commands.append(value)
                self.reply = {'id': value['id'], 'result': {'targetInfos':self.targets} if value['method']=='Target.getTargets' else {}}
            def receive(self):
                if self.reply is not None:
                    if self.reply['id']==4 and self.interleave:
                        self.interleave=False;return self.events.pop(0)
                    response = self.reply;self.reply = None;return response
                return self.events.pop(0)
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'ComprasExport-123.xlsx'
            path.write_bytes(b'Excel de prueba')
            begin = {'method': 'Browser.downloadWillBegin', 'params': {'guid': 'export', 'frameId': 'chosen-frame', 'suggestedFilename': path.name}}
            # Descargas de otra pestaña no se usan como resultado.
            other = {'method': 'Browser.downloadWillBegin', 'params': {'guid': 'other', 'frameId': 'other-frame', 'suggestedFilename': 'otro.xlsx'}}
            complete = {'method': 'Browser.downloadProgress', 'params': {'guid': 'export', 'state': 'completed'}}
            connection = Connection([other, begin, complete])
            self.assertEqual(download_excel(Page(), connection, folder, lambda:False), path)
            self.assertEqual(connection.commands[0]['params']['behavior'], 'allow')
            self.assertEqual(connection.commands[-1]['params'], {'behavior':'default', 'eventsEnabled':False})
            cancelled = dict(complete, params={'guid':'export','state':'canceled'})
            connection = Connection([begin, cancelled])
            with self.assertRaisesRegex(ValueError, 'canceló'):
                download_excel(Page(), connection, folder, lambda:False)
            self.assertEqual(connection.commands[-1]['params']['behavior'], 'default')
            connection = Connection([])
            with self.assertRaises(InterruptedError):
                download_excel(Page(), connection, folder, lambda:True)
            self.assertEqual(connection.commands[-1]['params']['behavior'], 'default')
            popup=dict(begin,params=dict(begin['params'],frameId='popup'))
            connection=Connection([popup,complete],[{'targetId':'popup','openerFrameId':'chosen-frame'}],interleave=True)
            self.assertEqual(download_excel(Page(),connection,folder,lambda:False),path)
            self.assertEqual(connection.commands[-1]['params']['behavior'],'default')

    def test_export_checks_company_then_queries_zip_dates_with_empty_search(self):
        from contextlib import nullcontext
        from unittest.mock import MagicMock
        from zeta_export import export
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'Compras.xlsx';workbook(path,1)
            cfg={'empresa_rut':COMPANY,'chrome':{'endpoint':'http://127.0.0.1:9223'},
                 'control_duplicados':{'numero':'10','serie':'A','proveedor':'P','nombre':'Control'}}
            calls=[]
            def query(page,start,end,text):
                calls.append(('query',start,end,text));return {'filas':[{}]}
            def company(page,rows):calls.append(('company',));return {'rut':COMPANY}
            connection=MagicMock()
            with patch('runtime_paths.config',return_value=cfg),patch('zeta_bot.exclusive_lock',return_value=nullcontext()),\
                 patch('playwright.sync_api.sync_playwright'),patch('chrome_connection.connect_chrome'),\
                 patch('chrome_connection.choose_page'),patch('zeta_session.prepare_session'),\
                 patch('zeta_compras.refresh_list',side_effect=query),patch('cargar_lote.verify_company',side_effect=company),\
                 patch('chrome_connection.websocket_endpoint',return_value='ws://127.0.0.1:9223/devtools/browser/test'),\
                 patch('cdp_transport.WebSocket.connect',return_value=connection),\
                 patch('zeta_export.download_excel',return_value=path):
                result=export(folder,'2026-10-31','2026-11-13',COMPANY,lambda message:None,lambda:False)
            self.assertEqual(calls[0][3],'10')
            self.assertEqual(calls[1],('company',))
            self.assertEqual(calls[2],('query','31/10/26','13/11/26',''))
            self.assertEqual(result['rows'],1)
            connection.close.assert_called_once()


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
