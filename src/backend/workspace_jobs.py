"""Jobs use the existing guarded engine; cancellation occurs between invoices."""
import json
import threading
import uuid
from workspace_store import batch_revision, now


class Jobs:
    def __init__(self, store, runner=None):
        self.store = store
        self.runner = runner or self.real_runner
        self.lock = threading.Lock()
        self.events = {}

    @staticmethod
    def real_runner(payload, save, progress, cancelled):
        from cargar_lote import run, REPORT
        code=run(save=save, payload=payload, progress=progress, cancelled=cancelled,duplicates_only=payload.get('duplicates_only',False))
        if code:
            report=json.loads(REPORT.read_text(encoding='utf-8'))
            raise ValueError(report.get('motivo','La conexión no permitió completar el lote.'))
        return code

    def list(self):
        with self.store.connect() as db:
            return [dict(r, data=json.loads(r['data'])) for r in db.execute('SELECT * FROM jobs WHERE company=? ORDER BY created DESC', (self.store.company(),))]

    def start(self, request_id, save=False, precheck=False, invoice_keys=None, *, reset_batch=None):
        if precheck and save:raise ValueError('La búsqueda previa no guarda facturas.')
        if reset_batch is not None and (not precheck or invoice_keys is not None):
            raise ValueError('El reinicio requiere una búsqueda para todo el lote.')
        if not request_id or len(request_id) > 100:
            raise ValueError('Falta identificador de operación.')
        company = self.store.company()
        with self.lock, self.store.connect() as db:
            existing = db.execute('SELECT * FROM jobs WHERE id=?', (request_id,)).fetchone()
            if existing:
                if existing['company'] != company:
                    raise ValueError('Operación de otra empresa.')
                return request_id
            active = db.execute("SELECT id,data FROM jobs WHERE company=? AND status IN ('queued','running','cancelling')", (company,)).fetchone()
            if active:
                running=json.loads(active['data'])
                if reset_batch is not None or bool(running.get('save'))!=bool(save) or bool(running.get('precheck'))!=bool(precheck):
                    raise ValueError('Hay otro trabajo en curso. Espera a que termine; la nueva carga todavia no se inicio.')
                return active['id']
            batch = self.store.reset_review(reset_batch) if reset_batch is not None else self.store.settings().get('batch',{})
            invoices = self.store.invoices()
            ready = [i for i in invoices if i['state'] not in {'skipped','non_invoice','loaded','manual'}] if precheck else [i for i in invoices if i['state'] == 'ready']
            if invoice_keys is not None:
                if not precheck:raise ValueError('El filtro solo se admite para buscar existentes.')
                ready=[i for i in ready if i['key'] in invoice_keys]
            if not ready:
                if reset_batch is not None:return None
                if any(i['state'] in {'existing','loaded'} for i in invoices) and not any(i['state']=='needs_review' for i in invoices):raise ValueError('Las facturas ya fueron cargadas. Consultá los resultados.')
                raise ValueError('No hay facturas listas para procesar. Abrí Revisar pendientes y completá proveedor, destino y forma de pago cuando corresponda.')
            payload = {'comprobantes': [i['payload'] for i in ready], 'duplicates_only': precheck,
                       'identities':{i['key']:{'source_hash':i['source']['sha256CFE'],'supplier_code':i['payload']['proveedor']['codigoZeta']} for i in ready}}
            db.execute('BEGIN IMMEDIATE')
            row=db.execute("SELECT value FROM settings WHERE key='batch'").fetchone()
            if batch_revision(json.loads(row['value']) if row else {})!=batch_revision(batch):
                raise ValueError('El lote cambió antes de empezar. Volvé a iniciar la búsqueda.')
            event = threading.Event()
            self.events[request_id] = event
            db.execute('INSERT INTO jobs VALUES(?,?,?,?,?)', (request_id, company, 'queued', json.dumps({'total': len(ready), 'done': 0, 'save': save, 'precheck':precheck, 'batch_date':batch_revision(batch), 'items': []}), now()))
            if save:
                path=self.store.directory/'configuracion_zeta.json'
                config=json.loads(path.read_text(encoding='utf-8-sig'))
                config.setdefault('carga_lote',{})['habilitada']=True
                path.write_text(json.dumps(config,ensure_ascii=False,indent=2),encoding='utf-8')
        threading.Thread(target=self.work, args=(request_id, payload, save, event), daemon=True).start()
        return request_id

    def work(self, job, payload, save, event):
        def update(item):
            item=dict(item,**payload.get('identities',{}).get(item['clave'],{}))
            with self.store.connect() as db:
                row = db.execute('SELECT data FROM jobs WHERE id=?', (job,)).fetchone()
                data = json.loads(row['data'])
                data['items'].append(item)
                data['done'] = len(data['items'])
                db.execute('INSERT INTO attempts(job,key,data,created) VALUES(?,?,?,?)', (job, item['clave'], json.dumps(item), now()))
                db.execute('UPDATE jobs SET data=? WHERE id=?', (json.dumps(data), job))
        with self.store.connect() as db:
            db.execute("UPDATE jobs SET status='running' WHERE id=?", (job,))
        try:
            code = self.runner(payload, save, update, event.is_set)
            status = 'cancelled' if event.is_set() else ('finished' if code == 0 else 'failed')
        except Exception as error:
            status = 'failed'
            with self.store.connect() as db:
                data = json.loads(db.execute('SELECT data FROM jobs WHERE id=?', (job,)).fetchone()['data'])
                data['error'] = str(error)
                db.execute('UPDATE jobs SET data=? WHERE id=?', (json.dumps(data), job))
        with self.store.connect() as db:
            db.execute('UPDATE jobs SET status=? WHERE id=?', (status, job))

    def cancel(self, job):
        if job in self.events:
            self.events[job].set()
            with self.store.connect() as db:
                db.execute("UPDATE jobs SET status='cancelling' WHERE id=? AND status IN ('queued','running')", (job,))
