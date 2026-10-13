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
    def test_ui_and_external_data_are_served_without_zeta(self):
        with tempfile.TemporaryDirectory(dir=ROOT.parent,prefix='cerecito-test-') as temporary:
            data=Path(temporary).resolve()
            self.assertTrue(data.is_relative_to(ROOT.parent.resolve()))
            (data/'base_historica.js').write_text('const DB={catalog:[]};',encoding='utf-8')
            with patch.object(launcher,'DATA_DIR',data):
                server=ThreadingHTTPServer(('127.0.0.1',0),launcher.Handler)
                thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
                opener=build_opener(ProxyHandler({}));base=f'http://127.0.0.1:{server.server_port}'
                try:
                    with opener.open(base+'/api/health',timeout=2) as response:
                        self.assertFalse(json.load(response)['bot_desde_interfaz'])
                    with opener.open(base+'/',timeout=2) as response:
                        self.assertIn(b'Cerecito',response.read())
                    with opener.open(base+'/js/data.js',timeout=2) as response:
                        self.assertEqual(response.read(),b'const DB={catalog:[]};')
                    with opener.open(base+'/js/ultimo_resultado.js',timeout=2) as response:
                        self.assertIn(b'items:[]',response.read())
                finally:server.shutdown();server.server_close();thread.join(timeout=2)

if __name__=='__main__':unittest.main()
