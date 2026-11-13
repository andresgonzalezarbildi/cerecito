from http.server import ThreadingHTTPServer
from pathlib import Path
import json
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch
from urllib.request import build_opener,ProxyHandler

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
import launcher

class ServerTests(unittest.TestCase):
    def test_job_completion_during_state_read_keeps_final_invoices_current(self):
        import time
        from test_workflow import xml,COMPANY,SUPPLIER
        from workspace_store import Store
        from workspace_jobs import Jobs
        with tempfile.TemporaryDirectory() as temporary:
            store=Store(temporary)
            store.configure({'rut':COMPANY,'name':'Prueba','tolerance':'1'})
            store.import_catalog([{'kind':'supplier','code':'P','name':'Proveedor','rut':SUPPLIER},
                                  {'kind':'article','code':'A','name':'Artículo','vat':'1'}])
            store.import_documents('note.xml',xml())
            key=store.invoices()[0]['key']
            store.decide(key,{'mode':'credit','lines':{'0':{'kind':'article','code':'A'}}})
            entered,release=threading.Event(),threading.Event()
            def runner(payload,save,progress,cancelled):
                entered.set();release.wait(3)
                progress({'clave':key,'estado':'CONFIRMADO'})
                return 0
            jobs=Jobs(store,runner);jobs.start('race')
            self.assertTrue(entered.wait(2))
            original=store.invoices
            def finish_during_snapshot():
                invoices=original()
                release.set()
                deadline=time.monotonic()+2
                while jobs.list()[0]['status'] in {'queued','running'} and time.monotonic()<deadline:time.sleep(.005)
                self.assertEqual(jobs.list()[0]['status'],'finished')
                return invoices
            with patch.object(launcher,'SERVICE',store),patch.object(launcher,'JOB_SERVICE',jobs):
                server=ThreadingHTTPServer(('127.0.0.1',0),launcher.Handler)
                thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
                opener=build_opener(ProxyHandler({}));url=f'http://127.0.0.1:{server.server_port}/api/state'
                try:
                    with patch.object(store,'invoices',side_effect=finish_during_snapshot):
                        with opener.open(url,timeout=4) as response:data=json.load(response)
                    # A stale invoice snapshot must retain an active job so the client polls again.
                    if data['invoices'][0]['state']=='ready':
                        self.assertIn(data['jobs'][0]['status'],{'queued','running'})
                    with opener.open(url,timeout=4) as response:data=json.load(response)
                    self.assertEqual(data['jobs'][0]['status'],'finished')
                    self.assertEqual(data['invoices'][0]['state'],'loaded')
                finally:release.set();server.shutdown();server.server_close();thread.join(2)

    def test_ui_and_external_data_are_served_without_zeta(self):
        with tempfile.TemporaryDirectory(prefix='cerecito-test-') as temporary:
            data=Path(temporary).resolve()
            self.assertFalse(data.is_relative_to(ROOT.resolve()))
            (data/'base_historica.js').write_text('const DB={catalog:[]};',encoding='utf-8')
            with patch.object(launcher,'DATA_DIR',data):
                server=ThreadingHTTPServer(('127.0.0.1',0),launcher.Handler)
                thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
                opener=build_opener(ProxyHandler({}));base=f'http://127.0.0.1:{server.server_port}'
                try:
                    with opener.open(base+'/api/health',timeout=2) as response:
                        self.assertTrue(json.load(response)['bot_desde_interfaz'])
                    with opener.open(base+'/',timeout=2) as response:
                        self.assertIn(b'Cerecito',response.read())
                    with opener.open(base+'/js/data.js',timeout=2) as response:
                        self.assertEqual(response.read(),b'const DB={catalog:[]};')
                    with opener.open(base+'/js/ultimo_resultado.js',timeout=2) as response:
                        self.assertIn(b'items:[]',response.read())
                finally:server.shutdown();server.server_close();thread.join(timeout=2)

if __name__=='__main__':unittest.main()
