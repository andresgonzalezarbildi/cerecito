"""Abre la interfaz local. El motor de carga conserva su entrada CLI en esta base."""
from http.server import ThreadingHTTPServer,SimpleHTTPRequestHandler
from pathlib import Path
import json
import mimetypes
import os
import sys
from urllib.parse import urlsplit,unquote
import webbrowser

sys.path.insert(0,str(Path(__file__).resolve().parent/'src/backend'))
from runtime_paths import APP_DIR,DATA_DIR,APP_URL,initialize

class Handler(SimpleHTTPRequestHandler):
    def __init__(self,*args,**kwargs):super().__init__(*args,directory=str(APP_DIR),**kwargs)
    def do_GET(self):
        path=unquote(urlsplit(self.path).path)
        if path=='/api/health':
            body=json.dumps({'app':'cerecito','ui':'legacy','bot_desde_interfaz':False}).encode()
            self.send_response(200);self.send_header('Content-Type','application/json');self.send_header('Content-Length',str(len(body)));self.end_headers();self.wfile.write(body);return
        files={'/js/data.js':DATA_DIR/'base_historica.js','/js/ultimo_resultado.js':DATA_DIR/'web/ultimo_resultado.js'}
        target=files.get(path)
        if target and target.is_file():
            body=target.read_bytes();self.send_response(200);self.send_header('Content-Type','text/javascript; charset=utf-8');self.send_header('Cache-Control','no-store');self.send_header('Content-Length',str(len(body)));self.end_headers();self.wfile.write(body);return
        super().do_GET()
    def end_headers(self):
        self.send_header('Cache-Control','no-cache');super().end_headers()
    def log_message(self,*args):pass

def main():
    initialize()
    port=int(os.environ.get('CERECITO_UI_PORT','8765'))
    server=ThreadingHTTPServer(('127.0.0.1',port),Handler)
    webbrowser.open(APP_URL)
    server.serve_forever()

if __name__=='__main__':
    try:main()
    except OSError as error:
        import ctypes
        if os.name=='nt':ctypes.windll.user32.MessageBoxW(0,str(error),'Cerecito',16)
        raise
