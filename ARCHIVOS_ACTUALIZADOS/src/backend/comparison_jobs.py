"""Trabajos de comparación vinculados al ZIP y a la empresa actuales."""
import hashlib
import json
from pathlib import Path
import re
import shutil
import threading
from zipfile import ZipFile, ZIP_DEFLATED
from workspace_store import batch_revision, now
from zeta_export import period


class ComparisonJobs:
    def start_comparison(self, request_id, revision):
        if not isinstance(request_id, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,100}', request_id):
            raise ValueError('Identificador de comparación inválido.')
        company = self.store.company()
        with self.lock, self.store.connect() as db:
            batch = self.store.settings().get('batch') or {}
            if not revision or batch_revision(batch) != revision:
                raise ValueError('El lote cambió. Actualizá la pantalla antes de comparar.')
            existing = db.execute('SELECT * FROM jobs WHERE id=?', (request_id,)).fetchone()
            if existing:
                old = json.loads(existing['data'])
                if existing['company'] != company or old.get('kind') != 'comparison' or old.get('batch_date') != revision:
                    raise ValueError('El identificador corresponde a otra operación.')
                return request_id
            active = db.execute("SELECT id,data FROM jobs WHERE company=? AND status IN ('queued','running','cancelling')", (company,)).fetchone()
            if active:
                if json.loads(active['data']).get('kind') == 'comparison':
                    return active['id']
                raise ValueError('Hay una búsqueda o carga en curso. Esperá a que termine para comparar.')
            source = Path(batch.get('path', '')).resolve()
            if not source.is_relative_to((self.store.directory / 'entrada/xml').resolve()) or not source.is_file():
                raise ValueError('No está disponible el archivo original del lote. Volvé a importar el ZIP.')
            start, end = period(self.store.invoices())
            folder = self.store.directory / 'salida/comparaciones' / hashlib.sha256(request_id.encode()).hexdigest()
            folder.mkdir(parents=True, exist_ok=True)
            snapshot = folder / 'lote.zip'
            if source.suffix.lower() == '.xml':
                with ZipFile(snapshot, 'w', ZIP_DEFLATED) as archive:
                    archive.writestr('comprobante.xml', source.read_bytes())
            elif source.suffix.lower() == '.zip':
                shutil.copyfile(source, snapshot)
            else:
                raise ValueError('El lote actual no es un ZIP o XML.')
            data = {'kind': 'comparison', 'batch_date': revision, 'filename': batch['filename'],
                    'from': start, 'to': end, 'zip': str(snapshot),
                    'zip_hash': hashlib.sha256(snapshot.read_bytes()).hexdigest(),
                    'total': 1, 'done': 0, 'items': [], 'message': 'Preparando la exportación…'}
            db.execute('BEGIN IMMEDIATE')
            current = json.loads(db.execute("SELECT value FROM settings WHERE key='batch'").fetchone()['value'])
            if batch_revision(current) != revision:
                raise ValueError('El lote cambió antes de exportar. Volvé a comparar.')
            db.execute('INSERT INTO jobs VALUES(?,?,?,?,?)', (request_id, company, 'queued', json.dumps(data), now()))
            event = threading.Event()
            self.events[request_id] = event
        threading.Thread(target=self.work_comparison, args=(request_id, company, folder, data, event), daemon=True).start()
        return request_id

    def work_comparison(self, job, company, folder, data, event):
        def update(message):
            data['message'] = message
            with self.store.connect() as db:
                db.execute('UPDATE jobs SET data=? WHERE id=?', (json.dumps(data), job))
        with self.store.connect() as db:
            db.execute("UPDATE jobs SET status='running' WHERE id=?", (job,))
        try:
            from zeta_export import export
            result = (self.exporter or export)(folder, data['from'], data['to'], company, update, event.is_set)
            if event.is_set():
                raise InterruptedError('Comparación cancelada.')
            path = Path(result['path']).resolve()
            if not path.is_relative_to(folder.resolve()) or not path.is_file():
                raise ValueError('El Excel no pertenece a esta comparación.')
            data['export'] = dict(result, sha256=hashlib.sha256(path.read_bytes()).hexdigest())
            data['done'] = 1
            data['message'] = 'Excel descargado. Comparando con el ZIP…'
            status = 'finished'
        except Exception as error:
            status = 'cancelled' if event.is_set() else 'failed'
            data['error'] = str(error)
        with self.store.connect() as db:
            db.execute('UPDATE jobs SET status=?,data=? WHERE id=?', (status, json.dumps(data), job))

    def comparison_file(self, job, part):
        if part not in {'zip', 'excel'}:
            raise ValueError('Archivo de comparación inválido.')
        with self.store.connect() as db:
            row = db.execute('SELECT * FROM jobs WHERE id=? AND company=?', (job, self.store.company())).fetchone()
        if not row:
            raise ValueError('No se encontró la comparación de esta empresa.')
        data = json.loads(row['data'])
        if row['status'] != 'finished' or data.get('kind') != 'comparison':
            raise ValueError('La exportación todavía no está lista.')
        if data['batch_date'] != batch_revision(self.store.settings().get('batch', {})):
            raise ValueError('La comparación corresponde a un lote anterior. Volvé a comparar.')
        path = Path(data['zip'] if part == 'zip' else data['export']['path']).resolve()
        folder = (self.store.directory / 'salida/comparaciones' / hashlib.sha256(job.encode()).hexdigest()).resolve()
        if not path.is_relative_to(folder) or not path.is_file():
            raise ValueError('El archivo de la comparación ya no está disponible.')
        content = path.read_bytes()
        expected = data['zip_hash'] if part == 'zip' else data['export']['sha256']
        if hashlib.sha256(content).hexdigest() != expected:
            raise ValueError('El archivo cambió después de exportar. Volvé a comparar.')
        return content
