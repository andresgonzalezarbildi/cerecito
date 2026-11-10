"""Interfaz y API local de trabajos persistentes de Cerecito."""
from http.server import ThreadingHTTPServer,SimpleHTTPRequestHandler
from pathlib import Path
import json
import mimetypes
import os
import sys
from urllib.parse import urlsplit,unquote
import shutil
import subprocess
import time
import base64
import secrets
import threading

sys.path.insert(0,str(Path(__file__).resolve().parent/'src/backend'))
from runtime_paths import APP_DIR,DATA_DIR,APP_URL,PREFERENCES,initialize
from workspace_store import Store
from workspace_jobs import Jobs

SESSION_TOKEN=secrets.token_urlsafe(32)
SERVICE=None
JOB_SERVICE=None
SERVICE_LOCK=threading.Lock()

def services():
    global SERVICE,JOB_SERVICE
    with SERVICE_LOCK:
        if SERVICE is None:
            SERVICE=Store(DATA_DIR)
            JOB_SERVICE=Jobs(SERVICE)
    return SERVICE,JOB_SERVICE

class Handler(SimpleHTTPRequestHandler):
    def __init__(self,*args,**kwargs):super().__init__(*args,directory=str(APP_DIR),**kwargs)
    def do_GET(self):
        if urlsplit('http://'+self.headers.get('Host','')).hostname not in {'localhost','127.0.0.1'}:
            self.reply({'error':'Host local requerido.'},403);return
        path=unquote(urlsplit(self.path).path)
        if path=='/api/state':
            try:
                store,jobs=services()
                settings=store.settings()
                invoices=store.invoices() if settings.get('company') else []
                history=jobs.list() if settings.get('company') else []
                self.reply({'token':SESSION_TOKEN,'settings':settings,'invoices':invoices,'catalog':store.catalog() if settings.get('company') else [],'jobs':history,'data_dir':str(DATA_DIR),'sectors':store.sectors() if settings.get('company') else []})
            except Exception as error:self.reply({'error':str(error)},400)
            return
        if path=='/api/health':
            body=json.dumps({'app':'cerecito','ui':'workflow','bot_desde_interfaz':True}).encode()
            self.send_response(200);self.send_header('Content-Type','application/json');self.send_header('Content-Length',str(len(body)));self.end_headers();self.wfile.write(body);return
        files={'/js/data.js':DATA_DIR/'base_historica.js','/js/ultimo_resultado.js':DATA_DIR/'web/ultimo_resultado.js'}
        target=files.get(path)
        if target and target.is_file():
            body=target.read_bytes();self.send_response(200);self.send_header('Content-Type','text/javascript; charset=utf-8');self.send_header('Cache-Control','no-store');self.send_header('Content-Length',str(len(body)));self.end_headers();self.wfile.write(body);return
        super().do_GET()
    def reply(self,data,status=200):
        body=json.dumps(data,ensure_ascii=False).encode('utf-8')
        self.send_response(status);self.send_header('Content-Type','application/json; charset=utf-8');self.send_header('Content-Length',str(len(body)));self.send_header('Cache-Control','no-store');self.end_headers();self.wfile.write(body)
    def do_POST(self):
        # Session token prevents another website from driving the local bot.
        if urlsplit('http://'+self.headers.get('Host','')).hostname not in {'localhost','127.0.0.1'} or self.headers.get('X-Cerecito-Token')!=SESSION_TOKEN:
            self.reply({'error':'Sesión inválida. Recargá Cerecito.'},403);return
        try:
            size=int(self.headers.get('Content-Length','0'))
            if not 0<size<=90*1024*1024:raise ValueError('Archivo demasiado grande o pedido vacío.')
            data=json.loads(self.rfile.read(size))
            store,jobs=services();path=urlsplit(self.path).path
            result={}
            if path=='/api/data-dir':
                global DATA_DIR,SERVICE,JOB_SERVICE
                store.assert_idle()
                if store.settings().get('company'):raise ValueError('La carpeta se elige antes de configurar la empresa. Para mover una instalación existente, consultá el README; no se moverán datos activos.')
                import runtime_paths
                with SERVICE_LOCK:
                    DATA_DIR=runtime_paths.select_initial_data_directory(data['directory'])
                    SERVICE=Store(DATA_DIR);JOB_SERVICE=Jobs(SERVICE)
                result={'data_dir':str(DATA_DIR)}
            elif path=='/api/configure':store.configure(data)
            elif path=='/api/import':
                result=store.import_documents(data['filename'],base64.b64decode(data['content'],validate=True))
                if any(i['state'] not in {'skipped','non_invoice','loaded','manual'} for i in store.invoices()):
                    result['job']=jobs.start(secrets.token_urlsafe(20),precheck=True)
            elif path=='/api/catalog/preview':result={'rows':store.preview_catalog(data['filename'],base64.b64decode(data['content'],validate=True),data.get('kind'))}
            elif path=='/api/catalog/import':result={'count':store.import_catalog(data['rows'])}
            elif path=='/api/history':store.import_history(base64.b64decode(data['content'],validate=True))
            elif path=='/api/legacy/preview':result=store.import_legacy(base64.b64decode(data['content'],validate=True))
            elif path=='/api/legacy/import':result=store.import_legacy(base64.b64decode(data['content'],validate=True),commit=True)
            elif path=='/api/decision':
                store.decide(data['key'],data['patch'],learn_lines=bool(data.get('learn_lines')))
                if data['patch'].get('supplier'):
                    supplier=next((c for c in store.catalog() if c['kind']=='supplier' and c['code']==data['patch']['supplier']),None)
                    if supplier and not supplier.get('rut') and 'generico' not in supplier['name'].casefold().replace('é','e'):
                        store.approve_supplier(data['key'],supplier['code'],only_if_unassigned=True)
                    result['job']=jobs.start(secrets.token_urlsafe(20),precheck=True,invoice_keys=[data['key']])
            elif path=='/api/sector':store.apply_sector(data['key'],data['center'],data['mapping'])
            elif path=='/api/rule':store.rule(data['key'],int(data['index']))
            elif path=='/api/supplier/confirm':store.approve_supplier(data['key'],data['code'])
            elif path=='/api/jobs':result={'id':jobs.start(data['id'],bool(data.get('save')),bool(data.get('precheck')))}
            elif path=='/api/jobs/cancel':jobs.cancel(data['id'])
            elif path=='/api/chrome':
                store.company()
                from abrir_chrome_bot import main as open_chrome
                open_chrome()
            else:self.reply({'error':'Acción desconocida.'},404);return
            self.reply(result)
        except Exception as error:self.reply({'error':str(error)},400)
    def end_headers(self):
        self.send_header('Cache-Control','no-cache');super().end_headers()
    def log_message(self,*args):pass

def find_window_browser():
    """Usar Edge o Chrome; una pestaña normal no permite detectar un cierre fiable."""
    candidates=[]
    for name in ('msedge.exe', 'chrome.exe', 'microsoft-edge', 'google-chrome', 'chromium'):
        executable=shutil.which(name)
        if executable:candidates.append(Path(executable))
    program_roots=[os.environ.get('PROGRAMFILES'),os.environ.get('PROGRAMFILES(X86)'),
                   os.environ.get('LOCALAPPDATA')]
    for root in filter(None,program_roots):
        candidates.extend([Path(root)/'Microsoft/Edge/Application/msedge.exe',
                           Path(root)/'Google/Chrome/Application/chrome.exe'])
    for candidate in candidates:
        if candidate.is_file():return candidate
    raise RuntimeError('No se encontró Microsoft Edge ni Google Chrome. Cerecito necesita uno de ellos para abrir su propia ventana y cerrarse junto con ella.')


def open_cerecito_window(url):
    """Proceso de navegador exclusivo; no comparte perfil con Chrome del bot de Zeta."""
    browser=find_window_browser()
    profile=PREFERENCES.parent/'perfil_ventana'/browser.stem.lower()
    profile.mkdir(parents=True,exist_ok=True)
    args=[str(browser), '--app='+url, '--user-data-dir='+str(profile),
          '--no-first-run', '--no-default-browser-check',
          '--disable-background-mode', '--disable-extensions']
    return subprocess.Popen(args,stdin=subprocess.DEVNULL,
                            stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)


def cancel_pending_jobs_before_exit():
    """Al cerrar la ventana, no abortar un guardado en curso a mitad de factura."""
    if SERVICE is None or JOB_SERVICE is None:return
    try:
        with SERVICE.connect() as db:
            active=[row['id'] for row in db.execute(
                "SELECT id FROM jobs WHERE status IN ('queued','running','cancelling')")]
    except Exception:
        return
    for job in active:
        JOB_SERVICE.cancel(job)  # El motor interrumpe antes de la próxima factura.
    while active:
        time.sleep(0.25)
        try:
            with SERVICE.connect() as db:
                active=[row['id'] for row in db.execute(
                    "SELECT id FROM jobs WHERE status IN ('queued','running','cancelling')")]
        except Exception:
            break


def main():
    initialize()
    services()
    port=int(os.environ.get('CERECITO_UI_PORT','8765'))
    server=ThreadingHTTPServer(('127.0.0.1',port),Handler)
    server.daemon_threads=True
    server_thread=threading.Thread(target=server.serve_forever,daemon=True)
    server_thread.start()
    try:
        window=open_cerecito_window(APP_URL)
        window.wait()  # La X de la ventana cierra el navegador exclusivo.
    finally:
        cancel_pending_jobs_before_exit()
        server.shutdown()
        server.server_close()
        server_thread.join(timeout=5)

if __name__=='__main__':
    try:main()
    except Exception as error:
        import ctypes
        if os.name=='nt':ctypes.windll.user32.MessageBoxW(0,str(error),'Cerecito',16)
        raise
