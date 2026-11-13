"""Persistent local workflow. All accounting codes come from imported masters."""
import csv
import hashlib
import io
import json
import sqlite3
import re
from contextlib import contextmanager
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from xml_source import (read_zip_documents, read_xml_documents, preview_net_lines, non_monetary_line_indices,
                        rounding_line_indices, valid_credit_note_reference, can_ignore_line, tax_breakdown,
                        validate_zeta_vat_choice, VAT_LABELS)


FINESA_RUT = '216450470015'


def finesa_sector(address):
    address = ' '.join(address.casefold().split())
    if re.match(r'^lavadero\b', address):
        return 'housekeeping'
    if address == 'ruta interbalnearia km 115.5 (pde)':
        return 'a&b'
    return ''


def now():
    return datetime.now(timezone.utc).isoformat()


def batch_revision(batch):
    return batch.get('reset_at') or batch.get('date')


class Store:
    def __init__(self, directory):
        self.directory = Path(directory)
        (self.directory / 'estado').mkdir(parents=True, exist_ok=True)
        self.path = self.directory / 'estado/cerecito.sqlite'
        with self.connect() as db:
            db.execute('CREATE TABLE IF NOT EXISTS migrations(version INTEGER PRIMARY KEY, applied TEXT)')
            applied={r[0] for r in db.execute('SELECT version FROM migrations')}
            for migration in sorted((Path(__file__).parent / 'migrations').glob('*.sql')):
                version=int(migration.name.split('_')[0])
                if version not in applied:
                    db.executescript('BEGIN IMMEDIATE;\n'+migration.read_text(encoding='utf-8')+'\nINSERT OR IGNORE INTO migrations VALUES('+str(version)+",datetime('now'));\nCOMMIT;")
            db.execute("UPDATE jobs SET status='interrupted' WHERE status IN ('queued','running','cancelling')")

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=15)
        db.row_factory = sqlite3.Row
        db.execute('PRAGMA journal_mode=WAL')
        try:
            with db:
                yield db
        finally:
            db.close()

    def settings(self):
        with self.connect() as db:
            return {r['key']: json.loads(r['value']) for r in db.execute('SELECT * FROM settings')}

    def configure(self, data):
        self.assert_idle()
        rut = str(data.get('rut', ''))
        if len(rut) != 12 or not rut.isdigit():
            raise ValueError('Ingresá el RUT de la empresa (12 dígitos).')
        tolerance = Decimal(str(data.get('tolerance', '1.00')))
        if not tolerance.is_finite() or not 0 <= tolerance <= 100:
            raise ValueError('Tolerancia inválida.')
        data['tolerance'] = str(tolerance)
        with self.connect() as db:
            db.execute('INSERT OR REPLACE INTO companies VALUES(?,?)', (rut, data.get('name', '')))
            db.execute('INSERT OR REPLACE INTO settings VALUES(?,?)', ('company', json.dumps(data)))
        path = self.directory / 'configuracion_zeta.json'
        config = json.loads(path.read_text(encoding='utf-8-sig')) if path.exists() else {}
        config.update(empresa_rut=rut, tolerancia_total=str(tolerance))
        config['chrome'] = {'endpoint': 'http://127.0.0.1:9223'}
        config['control_duplicados'] = data.get('control', config.get('control_duplicados', {}))
        path.write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding='utf-8')

    def company(self):
        company = self.settings().get('company', {})
        if not company.get('rut'):
            raise ValueError('Completá el asistente de empresa primero.')
        return company['rut']

    def assert_idle(self):
        with self.connect() as db:
            if db.execute("SELECT 1 FROM jobs WHERE status IN ('queued','running','cancelling')").fetchone():
                raise ValueError('Esperá a que termine el trabajo antes de cambiar el lote, los maestros o la configuración.')

    def catalog(self):
        with self.connect() as db:
            return [dict(json.loads(r['data']), kind=r['kind'], code=r['code'], version=r['version'])
                    for r in db.execute('SELECT * FROM catalog WHERE company=?', (self.company(),))]

    def preview_catalog(self, filename, content, kind=None):
        if filename.lower().endswith('.json'):
            rows = json.loads(content.decode('utf-8-sig'))
            rows = rows.get('catalog', rows.get('rows', [])) if isinstance(rows, dict) else rows
        elif filename.lower().endswith('.xlsx'):
            from openpyxl import load_workbook
            import unicodedata
            def heading(value):
                return ''.join(c for c in unicodedata.normalize('NFD',str(value or '').strip().casefold()) if not unicodedata.combining(c))
            workbook=load_workbook(io.BytesIO(content),read_only=True,data_only=True)
            try:
                sheet=workbook.active
                values=iter(sheet.iter_rows())
                headers=None
                for position,cells in enumerate(values,1):
                    names=[heading(c.value) for c in cells]
                    if ({'code','codigo','codigo zeta'} & set(names)) and ({'name','nombre','descripcion','razon social'} & set(names)):
                        headers=[c.value for c in cells]
                        break
                    if position>=40:break
                if headers is None:raise ValueError('No se encontraron las columnas Codigo y Nombre en el Excel.')
                rows=[]
                for cells in values:
                    if all(c.value is None or str(c.value).strip()=='' for c in cells):continue
                    record={}
                    for header,cell in zip(headers,cells):
                        if header is None:continue
                        value=cell.value
                        if heading(header) in {'code','codigo','codigo zeta','rut','ruc'}:
                            if isinstance(value,(int,float)) and not isinstance(value,bool) and float(value).is_integer():
                                value=str(int(value))
                                mask=cell.number_format or ''
                                if mask and set(mask)=={'0'}:value=value.zfill(len(mask))
                            elif value is not None:value=str(value).strip()
                        record[str(header).strip()]=value
                    rows.append(record)
            finally:
                workbook.close()
        else:
            text = content.decode('utf-8-sig')
            rows = list(csv.DictReader(io.StringIO(text), dialect=csv.Sniffer().sniff(text[:4096])))
        allowed = {'supplier', 'article', 'concept', 'vat', 'center', 'payment', 'form'}
        if kind is not None and kind not in {'supplier','article','concept'}:
            raise ValueError('Tipo de catálogo inválido.')
        if not isinstance(rows, list) or not rows:
            raise ValueError('El catálogo está vacío.')
        seen = set()
        for row in rows:
            if kind:
                import unicodedata
                def normalized(value):
                    return ''.join(c for c in unicodedata.normalize('NFD',str(value or '').strip().casefold()) if not unicodedata.combining(c))
                aliases={'codigo':'code','codigo zeta':'code','nombre':'name','descripcion':'name','razon social':'name','ruc':'rut','sinonimos':'synonyms'}
                row_keys={normalized(k):v for k,v in row.items()}
                row.clear()
                row.update({aliases.get(k,k):v for k,v in row_keys.items()})
                if row.get('kind') and row['kind']!=kind:raise ValueError('El archivo contiene registros de otro tipo de catálogo.')
                row['kind']=kind
                if 'iva' in row and 'vat' not in row:
                    rate=normalized(row.pop('iva')).replace('%','').strip()
                    rates={'exento':'1','0':'1','10':'2','22':'3','iva en suspenso':'12','en suspenso':'12','suspenso':'12'}
                    if rate not in rates:raise ValueError('La columna IVA admite Exento, 0, 10, 22 o IVA en suspenso.')
                    row['vat']=rates[rate]
            if row.get('kind') not in allowed or not str(row.get('code', '')).strip() or not row.get('name'):
                raise ValueError('Cada fila requiere kind, code y name. Ver formato en Ayuda.')
            row['code'] = str(row['code']).strip()
            row['name'] = str(row['name']).strip()
            if 'rut' in row and row['rut'] is not None:row['rut']=str(row['rut']).strip()
            if 'vat' in row:
                vat=row['vat']
                if isinstance(vat,list):
                    if any(str(value) not in {'1','2','3','12'} for value in vat):raise ValueError('IVA de catálogo inválido.')
                    row['vat']=[str(value) for value in vat]
                elif vat is not None:
                    values=str(vat).split('|')
                    if any(value not in {'1','2','3','12'} for value in values if value):raise ValueError('IVA de catálogo inválido.')
                    row['vat']='|'.join(value for value in values if value)
            synonyms=row.get('synonyms') or []
            if isinstance(synonyms,str):
                synonyms=json.loads(synonyms) if synonyms.strip().startswith('[') else synonyms.split('|')
            if not isinstance(synonyms,list):raise ValueError('Los sinónimos deben ser una lista o textos separados por |.')
            row['synonyms']=[str(s).strip() for s in synonyms if str(s).strip()]
            key = (row['kind'], row['code'])
            if key in seen:
                raise ValueError('Código repetido en el mismo catálogo: ' + row['code'])
            seen.add(key)
        return rows

    def import_catalog(self, rows, provenance='importación manual'):
        self.assert_idle()
        # Revalidate on commit; a preview never authorizes arbitrary data.
        rows = self.preview_catalog('catalog.json', json.dumps(rows).encode())
        company = self.company()
        with self.connect() as db:
            for row in rows:
                previous=db.execute('SELECT data FROM catalog WHERE company=? AND kind=? AND code=?',(company,row['kind'],row['code'])).fetchone()
                if previous:
                    old=json.loads(previous['data'])
                    # Native contact exports omit RUT; preserve fields not supplied.
                    row={**old,**row}
                row['provenance'] = provenance
                db.execute('INSERT OR REPLACE INTO catalog VALUES(?,?,?,?,?)',
                           (company, row['kind'], row['code'], json.dumps(row), now()))
        return len(rows)

    def import_documents(self, filename, content):
        self.assert_idle()
        company = self.company()
        digest = hashlib.sha256(content).hexdigest()
        path = self.directory / 'entrada/xml' / (digest + ('.zip' if filename.lower().endswith('.zip') else '.xml'))
        path.parent.mkdir(parents=True, exist_ok=True)
        if filename.lower().endswith('.zip'):
            # Validate before retaining the new batch.
            temporary = self.directory / 'estado' / (digest + '.zip')
            temporary.write_bytes(content)
            try:
                documents = read_zip_documents(temporary)
            finally:
                temporary.unlink(missing_ok=True)
        else:
            documents = read_xml_documents(content, filename)
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            if db.execute("SELECT 1 FROM jobs WHERE status IN ('queued','running','cancelling')").fetchone():
                raise ValueError('Esperá a que termine el trabajo antes de importar otro lote.')
            for raw, source in documents:
                if source['receptor'] != company:
                    raise ValueError('El receptor del XML no coincide con la empresa.')
                existing = db.execute('SELECT source FROM invoices WHERE company=? AND key=?', (company, source['clave'])).fetchone()
                if existing:
                    previous = json.loads(existing['source'])
                    if previous['sha256CFE'] != source['sha256CFE']:
                        raise ValueError('XML diferente con la misma identidad fiscal: ' + source['clave'])
                    extra_totals={k:source['totales'][k] for k in ('suspenso','pagar')
                                  if k in source['totales'] and k not in previous['totales']}
                    if 'dirRecep' not in previous or extra_totals:
                        previous.setdefault('dirRecep',source['dirRecep'])
                        previous['totales'].update(extra_totals)
                        db.execute('UPDATE invoices SET source=? WHERE company=? AND key=?',
                                   (json.dumps(previous), company, source['clave']))
                else:
                    db.execute('INSERT OR IGNORE INTO invoices(company,key,source) VALUES(?,?,?)', (company, source['clave'], json.dumps(source)))
                for index, line in enumerate(source['lineas']):
                    db.execute('INSERT OR IGNORE INTO original_lines VALUES(?,?,?,?)', (company, source['clave'], index, json.dumps(line)))
            path.write_bytes(content)
            batch = {'filename': filename, 'path': str(path), 'keys': [s['clave'] for _, s in documents], 'date': now(), 'fresh_review':filename.lower().endswith('.zip')}
            db.execute('INSERT OR REPLACE INTO settings VALUES(?,?)', ('batch', json.dumps(batch)))
        return batch

    def reset_review(self, expected_revision):
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            if db.execute("SELECT 1 FROM jobs WHERE status IN ('queued','running','cancelling')").fetchone():
                raise ValueError('Esperá a que termine el trabajo en curso antes de reiniciar el lote.')
            row=db.execute("SELECT value FROM settings WHERE key='batch'").fetchone()
            batch=json.loads(row['value']) if row else {}
            if not batch.get('keys'):
                raise ValueError('Importá un lote antes de reiniciar la revisión.')
            if batch_revision(batch)!=expected_revision:
                raise ValueError('El lote cambió. Recargá Cerecito para revisar el lote actual.')
            company=json.loads(db.execute("SELECT value FROM settings WHERE key='company'").fetchone()['value'])['rut']
            db.executemany("UPDATE invoices SET decision='{}' WHERE company=? AND key=?",
                           ((company,key) for key in batch['keys']))
            batch['reset_at']=now()
            db.execute("UPDATE settings SET value=? WHERE key='batch'",(json.dumps(batch),))
        return batch

    def decide(self, key, patch, learn_lines=False):
        company = self.company()
        with self.connect() as db:
            row = db.execute('SELECT decision,source FROM invoices WHERE company=? AND key=?', (company, key)).fetchone()
            if not row:
                raise ValueError('Comprobante inexistente.')
            if db.execute("SELECT 1 FROM jobs WHERE company=? AND status IN ('queued','running','cancelling')", (company,)).fetchone():
                raise ValueError('Esperá a que termine el trabajo antes de cambiar decisiones.')
            allowed = {'mode', 'supplier', 'center', 'payment', 'lines', 'skip', 'reviewed', 'destinationKind', 'skipSupplier', 'groupByVat'}
            if set(patch) - allowed:
                raise ValueError('Campo de decisión desconocido.')
            if 'groupByVat' in patch and type(patch['groupByVat']) is not bool:
                raise ValueError('Opción de agrupar por IVA inválida.')
            decision = json.loads(row['decision'])
            source=json.loads(row['source'])
            if 'skipSupplier' in patch:
                setting='excluded_suppliers:'+company
                excluded=self.settings().get(setting,{})
                if patch['skipSupplier']:excluded[source['rut']]=source['nombre']
                else:excluded.pop(source['rut'],None)
                db.execute('INSERT OR REPLACE INTO settings VALUES(?,?)',(setting,json.dumps(excluded)))
                patch=dict(patch)
                patch.pop('skipSupplier')
            if 'destinationKind' in patch:
                if patch['destinationKind'] not in {'article','concept'}:raise ValueError('Tipo de destino invalido.')
                decision['lines']={k:v for k,v in decision.get('lines',{}).items() if v.get('ignore') or v.get('kind')==patch['destinationKind']}
            manual_choices=dict(patch.get('lines',{})) if learn_lines else {}
            if learn_lines:
                choices=dict(manual_choices)
                mapping=next((s['mapping'] for s in self.sectors() if s['code']==decision.get('center','')), {})
                catalog=self.catalog()
                for choice in list(choices.values()):
                    target=next((c for c in catalog if c['kind']==choice.get('kind') and c['code']==choice.get('code')),None)
                    if not target:continue
                    patch=dict(patch,destinationKind=choice['kind'])
                    allowed=target.get('vat',[])
                    if isinstance(allowed,str):allowed=allowed.split('|')
                    for n,line in enumerate(source['lineas']):
                        if str(n) in choices or decision.get('lines',{}).get(str(n)):continue
                        vat=line['indicadorIVA']
                        mapped=mapping.get(vat)
                        if vat in allowed:choices[str(n)]=dict(choice)
                        elif mapped and mapped.get('kind')==choice['kind']:choices[str(n)]=dict(mapped)
                        elif vat in {'1','2','3'}:
                            def family(name):
                                return re.sub(r'\b(?:exent[oa]s?|iva|10|22)\b|[%()\-]', '', name.casefold()).strip()
                            relatives=[c for c in catalog if c['kind']==choice['kind'] and family(c['name'])==family(target['name']) and vat in (c.get('vat',[]) if isinstance(c.get('vat',[]),list) else str(c.get('vat','')).split('|'))]
                            if len(relatives)==1:choices[str(n)]={'kind':relatives[0]['kind'],'code':relatives[0]['code']}

                patch=dict(patch,lines=choices)
            if patch:patch=dict(patch,reviewed=patch.get('reviewed',False))
            learned=[]
            if learn_lines:
                source=json.loads(row['source'])
                catalog=self.catalog()
                # Solo aprende elecciones explícitas, no otros renglones completados en bloque.
                for index,choice in manual_choices.items():
                    if choice.get('ignore'):continue
                    if not str(index).isdigit() or int(index)>=len(source['lineas']):
                        raise ValueError('Línea inexistente.')
                    raw=source['lineas'][int(index)]
                    target=next((c for c in catalog if c['kind'] in {'article','concept'} and c['kind']==choice.get('kind') and c['code']==choice.get('code')),None)
                    if target is None:raise ValueError('Destino ausente del catálogo.')
                    vat=target.get('vat',[])
                    if isinstance(vat,str):vat=vat.split('|')
                    if raw['indicadorIVA'] not in vat:
                        raise ValueError('El destino no admite el IVA original de esta línea.')
                    if source['rut'] != FINESA_RUT:
                        learned.append((company,source['rut'],raw['descripcion'].casefold().strip(),raw['indicadorIVA'],json.dumps({'kind':target['kind'],'code':target['code']})))
            if 'lines' in patch:
                lines = dict(decision.get('lines', {}))
                for index, choice in patch['lines'].items():
                    if set(choice) == {'ignore'}:
                        previous = dict(lines.get(index, {}))
                        previous.pop('ignore',None)
                        choice = dict(previous,ignore=True) if choice['ignore'] else previous
                    if choice:lines[index]=choice
                    else:lines.pop(index,None)
                patch = dict(patch, lines=lines)
            if 'center' in patch:
                center=patch['center']
                if not isinstance(center,str) or (center and not any(c['kind']=='center' and c['code']==center for c in self.catalog())):
                    raise ValueError('Sector inexistente en el catálogo.')
                if source['rut'] != FINESA_RUT:
                    setting='supplier_centers:'+company
                    row_setting=db.execute('SELECT value FROM settings WHERE key=?',(setting,)).fetchone()
                    preferences=json.loads(row_setting['value']) if row_setting else {}
                    preferences[source['rut']]=center
                    db.execute('INSERT OR REPLACE INTO settings VALUES(?,?)',(setting,json.dumps(preferences)))
            decision.update(patch)
            if decision.get('mode') not in {None, 'credit', 'cash'}:
                raise ValueError('Modalidad inválida.')
            db.execute('UPDATE invoices SET decision=? WHERE company=? AND key=?', (json.dumps(decision), company, key))
            db.executemany('INSERT OR REPLACE INTO rules VALUES(?,?,?,?,?)',learned)

    def rule(self, key, index):
        self.assert_idle()
        invoice = next(i for i in self.invoices() if i['key'] == key)
        line = invoice['source']['lineas'][index]
        choice = invoice['decision'].get('lines', {}).get(str(index))
        if not choice or choice.get('ignore'):
            raise ValueError('Elegí primero un destino del catálogo.')
        if not any(c['kind'] == choice.get('kind') and c['code'] == choice.get('code') for c in self.catalog()):
            raise ValueError('Destino ausente del catálogo.')
        with self.connect() as db:
            db.execute('INSERT OR REPLACE INTO rules VALUES(?,?,?,?,?)', (self.company(), invoice['source']['rut'], line['descripcion'].casefold().strip(), line['indicadorIVA'], json.dumps(choice)))

    def sectors(self):
        return self._sector_profiles(self.catalog(), self.settings().get('sectors:'+self.company(),{}))

    @staticmethod
    def _sector_profiles(catalog, configured):
        result=[{'code':'','name':'Sin sector / para distribuir','mapping':configured.get('',{}),'configured':'' in configured}]
        for center in (c for c in catalog if c['kind']=='center'):
            saved=configured.get(center['code'])
            mapping={}
            # Taxonomy suggests families, while the actual codes always come from masters.
            name=center['name'].strip().casefold()
            families = []
            if name == 'a&b':
                families = [r'\balimentos\b']
            elif 'housekeeping' in name:
                families = [
                    r'\bmat(?:erial(?:es)?)?\.?\s+(?:de\s+)?limpieza\s+rooms\b',
                    r'^mat(?:erial(?:es)?)?\.?\s+(?:de\s+)?limpieza(?:\s+(?:iva\b|exent|\d|[(-]).*)?$',
                ]
            if saved is None and families:
                for vat in ('1','2','3'):
                    for family in families:
                        matches = [c for c in catalog if c['kind']=='article'
                                   and re.search(family,c['name'].strip(),re.I)
                                   and vat in (c['vat'] if isinstance(c.get('vat'),list) else str(c.get('vat','')).split('|'))]
                        if matches:
                            if len(matches)==1:
                                mapping[vat]={'kind':'article','code':matches[0]['code']}
                            break
            result.append({'code':center['code'],'name':center['name'],'mapping':saved if saved is not None else mapping,'configured':saved is not None})
        return result

    def apply_sector(self,key,center,mapping):
        self.assert_idle()
        catalog=self.catalog()
        if center != '' and not any(c['kind']=='center' and c['code']==center for c in catalog):
            raise ValueError('Elegí un sector del catálogo.')
        if not isinstance(mapping,dict) or (not mapping and center != '') or set(mapping)-{'1','2','3'}:
            raise ValueError('Elegí los destinos del sector según IVA.')
        for vat,choice in mapping.items():
            target=next((c for c in catalog if c['kind'] in {'article','concept'} and c['kind']==choice.get('kind') and c['code']==choice.get('code')),None)
            if not target:raise ValueError('Destino ausente del catálogo.')
            allowed=target.get('vat',[])
            if isinstance(allowed,str):allowed=allowed.split('|')
            if vat not in allowed:raise ValueError('El destino '+target['name']+' no tiene ese IVA validado en el catálogo.')
        invoice=next(i for i in self.invoices() if i['key']==key)
        if invoice['state'] in {'existing','loaded'}:raise ValueError('La factura ya existe en Zeta.')
        if center == '' and not mapping:
            self.decide(key,{'center':'','reviewed':False})
            return
        choices={}
        for index,line in enumerate(invoice['source']['lineas']):
            previous=invoice['decision'].get('lines',{}).get(str(index),{})
            if previous.get('ignore'):continue
            vat=line['indicadorIVA']
            if vat in {'1','2','3'}:choices[str(index)]=mapping.get(vat,{'sector_unmapped':True})
        self.decide(key,{'center':center,'lines':choices,'reviewed':False})
        profiles=self.settings().get('sectors:'+self.company(),{})
        profiles[center]=mapping
        with self.connect() as db:
            db.execute('INSERT OR REPLACE INTO settings VALUES(?,?)',('sectors:'+self.company(),json.dumps(profiles)))

    def invoices(self):
        settings = self.settings()
        company = self.company()
        catalog = self.catalog()
        with self.connect() as db:
            records = list(db.execute('SELECT * FROM invoices WHERE company=?', (company,)))
            rules = {(r['supplier'], r['description'], r['vat']): json.loads(r['data']) for r in db.execute('SELECT * FROM rules WHERE company=?', (company,))}
            legacy = {(r['supplier'], r['description'], r['vat']): json.loads(r['data']) for r in db.execute('SELECT * FROM legacy_suggestions WHERE company=?', (company,))}
            attempt_records=list(db.execute('SELECT a.key,a.data,a.created FROM attempts a JOIN jobs j ON j.id=a.job WHERE j.company=? ORDER BY a.id',(company,)))
            results={r['key']:dict(json.loads(r['data']),checked_at=r['created']) for r in attempt_records}
            positive_results={r['key']:dict(json.loads(r['data']),checked_at=r['created']) for r in attempt_records if json.loads(r['data']).get('estado') in {'CONFIRMADO','RESULTADOS_EN_ZETA_NO_CARGAR'}}
        batch = settings.get('batch', {})
        keys = set(batch.get('keys', []))
        learned_centers=settings.get('supplier_centers:'+company,{})
        sector_profiles = self._sector_profiles(catalog, settings.get('sectors:'+company,{}))
        profiles_by_code = {s['code']:s for s in sector_profiles}
        result = []
        for record in records:
            if record['key'] not in keys:
                continue
            source, decision = json.loads(record['source']), json.loads(record['decision'])
            # La revisión nueva exige volver a comprobar duplicados, pero no descarta
            # decisiones y correlaciones aprendidas localmente.
            rules_for_invoice=rules
            legacy_for_invoice=legacy
            supplier = [c for c in catalog if c['kind'] == 'supplier' and str(c.get('rut', '')) == source['rut'] and float(c.get('confidence', 1)) >= .9]
            selected_supplier = next((c for c in catalog if c['kind'] == 'supplier' and c['code'] == decision.get('supplier')), None)
            if not selected_supplier and not decision.get('supplier'):
                confirmed = [c for c in catalog if c['kind']=='supplier' and settings.get('approvals',{}).get(c['code'])==source['rut']]
                if len(confirmed)==1:selected_supplier=confirmed[0]
            provider = selected_supplier or (supplier[0] if len(supplier) == 1 else None)
            manual_supplier = bool(selected_supplier and decision.get('supplier') == selected_supplier['code'])
            generic_override = manual_supplier and 'generico' in selected_supplier['name'].casefold().replace('é','e')
            reasons, proposals, lines = [], [], []
            is_finesa = source['rut'] == FINESA_RUT and source['tipo'] in {'111','112'}
            center = decision.get('center', learned_centers.get(source['rut'],''))
            sector_mapping = {}
            if is_finesa:
                # A previous Finesa invoice's sector does not identify this delivery.
                center = decision.get('center','')
                if 'center' not in decision:
                    sector_name = finesa_sector(source.get('dirRecep',''))
                    matching = [s for s in sector_profiles if s['code'] and sector_name
                                and (s['name'].strip().casefold() == sector_name
                                     or (sector_name == 'housekeeping' and 'housekeeping' in s['name'].casefold()))]
                    if len(matching) == 1:
                        center = matching[0]['code']
                    elif 'dirRecep' not in source:
                        reasons.append('Finesa: volvé a importar el XML para leer DirRecep o elegí el sector.')
                    elif not sector_name:
                        reasons.append('Finesa: DirRecep no identifica el sector. Elegilo manualmente.')
                    else:
                        reasons.append('Finesa: revisá el sector '+('A&B' if sector_name=='a&b' else 'Housekeeping')+' en el catálogo.')
                if center and decision.get('destinationKind') != 'concept':
                    sector_mapping = profiles_by_code.get(center,{}).get('mapping',{})
            if not provider:
                reasons.append('Elegí el proveedor del catálogo.')
            elif (not manual_supplier and str(provider.get('rut', '')) != source['rut']
                  and settings.get('approvals', {}).get(provider['code']) != source['rut']):
                if provider.get('rut'):
                    reasons.append('El RUT del proveedor contradice el XML.')
                else:
                    reasons.append('Confirmá la relación del proveedor sin RUT.')
            mode_locked=source['formaPagoXML']=='2'
            mode = 'credit' if mode_locked else (decision.get('mode') or ('cash' if source['formaPagoXML'] == '1' else 'credit'))
            mode_pending=source['formaPagoXML']=='1' and not decision.get('mode') and not decision.get('reviewed')
            if mode_pending:reasons.append('Corrobora la modalidad de la factura contado: contado o credito.')
            manual_ignored={index for index,raw in enumerate(source['lineas'])
                            if decision.get('lines',{}).get(str(index),{}).get('ignore') and can_ignore_line(raw)}
            non_monetary=set(non_monetary_line_indices(source))
            excluded=manual_ignored | non_monetary
            grouped=decision.get('groupByVat') is True
            supported={'1','2','3','6','7'}
            if grouped:supported.add('12')
            special_indicators={raw['indicadorIVA'] for index,raw in enumerate(source['lineas'])
                                if index not in excluded and raw['indicadorIVA'] not in supported}
            auto_rounding_indices=set(rounding_line_indices(source,Decimal(settings['company'].get('tolerance','1'))))-manual_ignored
            for index, raw in enumerate(source['lineas']):
                if index in manual_ignored:
                    proposals.append({'index':index,'options':[],'rule':None,
                                      'reason':'Línea ignorada por tu elección. Se conserva en el XML.','confidence':1})
                    continue
                if index in auto_rounding_indices:
                    proposals.append({'index':index,'options':[],'rule':None,
                                      'reason':'Redondeo omitido por defecto; se conserva en el XML y se comprueba la tolerancia.',
                                      'confidence':1})
                    continue
                if index in non_monetary:
                    proposals.append({'index':index,'options':[],'rule':None,'reason':'Entrega gratuita sin importe: queda registrada en el XML y no requiere artículo.','confidence':1})
                    continue
                if Decimal(raw['monto'])==0:
                    reasons.append('Marcá Ignorar línea en el renglón '+str(index+1)+' de importe cero.')
                    proposals.append({'index':index,'options':[],'rule':None,
                                      'reason':'Importe cero. Podés ignorar la línea.','confidence':0})
                    continue
                if raw['indicadorIVA'] not in supported:
                    proposals.append({'index':index,'options':[],'rule':None,'reason':'Indicador fiscal especial: no es una tasa de IVA.','confidence':0})
                    continue
                choice = decision.get('lines', {}).get(str(index))
                if is_finesa:
                    mapped = sector_mapping.get(raw['indicadorIVA'])
                    rule = mapped if mapped and mapped.get('kind')=='article' else None
                    reason = ('Artículo del sector según el IVA original'+
                              (' y DirRecep.' if 'center' not in decision else '.')) if rule else 'Elegí un artículo para el IVA de esta línea.'
                    proposals.append({'index':index,'options':[],'rule':rule,'reason':reason,'confidence':int(bool(rule))})
                else:
                    rule_key = (source['rut'], raw['descripcion'].casefold().strip(), raw['indicadorIVA'])
                    historic = legacy_for_invoice.get(rule_key)
                    rule = rules_for_invoice.get(rule_key) or (historic if float((historic or {}).get('confidence', 0)) >= .95 else None)
                    candidates = [c for c in catalog if c['kind'] in {'article', 'concept'} and (raw['descripcion'].casefold() in str(c.get('name', '')).casefold() or raw['descripcion'].casefold() in [str(s).casefold() for s in c.get('synonyms', [])])]
                    confidence = 1 if rule_key in rules_for_invoice else float((historic or {}).get('confidence', .5))
                    proposals.append({'index': index, 'options': candidates[:8], 'rule': rule, 'reason': 'Regla confirmada para proveedor, descripción e IVA' if rule_key in rules_for_invoice else ('Sugerencia del histórico; revisar antes de confirmar' if historic else 'Coincidencia de descripción; requiere elección'), 'confidence': confidence})
                if rule and decision.get('destinationKind') and rule.get('kind')!=decision['destinationKind']:rule=None
                chosen = choice or rule
                if chosen and chosen.get('ignore'):
                    if raw['indicadorIVA'] not in {'6', '7'}:
                        reasons.append('No se puede ignorar un producto gravado o exento.')
                    continue
                target = next((c for c in catalog if chosen and c['kind'] == chosen.get('kind') and c['code'] == chosen.get('code')), None)
                if not target:
                    vat=raw['indicadorIVA']
                    reason=('Elegí el destino para '+VAT_LABELS[vat]+'.' if grouped and vat in {'1','2','3','12'}
                            else 'Elegí el destino de la línea '+str(index+1))
                    if reason not in reasons:reasons.append(reason)
                    continue
                allowed_vat=target.get('vat', [])
                if isinstance(allowed_vat,str):allowed_vat=allowed_vat.split('|') if allowed_vat else []
                line=dict(raw, origenes=[index], codigoZeta=target['code'], descripcionZeta=target['name'], tipoDestino=target['kind'])
                vat=raw['indicadorIVA']
                reason=None
                if 'ivaZeta' in chosen:
                    line['ivaZeta']=chosen['ivaZeta']
                    try:validate_zeta_vat_choice(vat,line['ivaZeta'])
                    except ValueError as error:reason=str(error)
                elif vat not in allowed_vat:
                    reason=('Elegí el IVA en Zeta para el grupo '+VAT_LABELS.get(vat,vat)+'.' if grouped
                            else 'Elegí el IVA en Zeta para la línea '+str(index+1)+'.')
                if reason and reason not in reasons:reasons.append(reason)
                lines.append(line)
            kinds = {l['tipoDestino'] for l in lines}
            is_credit_note = source['tipo'] == '112'
            if is_credit_note:
                form = ('Nota de Crédito Compra' if mode == 'credit' else 'Devolución Compra') + (' Gastos' if kinds == {'concept'} else '')
            else:
                form = ('Compra Crédito' if mode == 'credit' else 'Compra Contado') + (' Gastos' if kinds == {'concept'} else '')
            if len(kinds) > 1:
                reasons.append('Destinos mixtos: falta verificar una alternativa de Zeta para una única factura.')
            effective_payment=decision.get('payment') or next((c['code'] for c in catalog if c['kind']=='payment' and c['name'].strip().casefold()=='efectivo'),'')
            payment = next((c for c in catalog if c['kind'] == 'payment' and c['code'] == effective_payment), None)
            if mode == 'cash' and not payment:
                reasons.append('Elegí una forma de pago real.')
            if center and not any(c['kind'] == 'center' and c['code'] == center for c in catalog):
                reasons.append('El centro elegido no está en el catálogo.')
            payload = {'clave': source['clave'], 'cfe': dict(source['totales'], **{k: source[k] for k in ('tipo','serie','numero','fecha','moneda')}), 'referencias': source.get('referencias', []), 'tipoOperacion':'nota_credito_compra' if is_credit_note else 'compra', 'proveedor': {'rut': source['rut'], 'nombre': source['nombre'], 'codigoZeta': provider['code'] if provider else ''}, 'lineas': lines, 'lineasIgnoradas': [int(i) for i, c in decision.get('lines', {}).items() if c.get('ignore')], 'zeta': {'comprobante': form, 'centroCosto': center, 'formaPago':payment['code'] if mode=='cash' and payment else '', 'formaPagoNombre':payment['name'] if mode=='cash' and payment else ''}, 'completo': True, 'estado': 'reviewed' if decision.get('reviewed') else 'auto'}
            if generic_override:payload['proveedor']['generico']=True
            if manual_supplier:payload['proveedor']['seleccionManual']=True
            if grouped:payload['agruparPorIVA']=True
            plan = None
            if not reasons:
                try:
                    plan = preview_net_lines(source, payload, company, Decimal(settings['company'].get('tolerance', '1')))
                except ValueError as error:
                    reasons.append(str(error))
            state = 'needs_review' if reasons else 'ready'
            last=results.get(record['key'])
            if last and (last['checked_at']<batch.get('reset_at','')
                         or (batch.get('fresh_review') and last['checked_at']<batch.get('date',''))):
                last=None
            positive=positive_results.get(record['key'])
            if positive and positive.get('source_hash')==source['sha256CFE'] and (not batch.get('fresh_review') or positive.get('checked_at','')>=batch.get('date','')):last=positive
            checked=bool(last and last.get('source_hash')==source['sha256CFE'] and (last.get('estado') in {'CONFIRMADO','RESULTADOS_EN_ZETA_NO_CARGAR'} or last.get('supplier_code')==payload['proveedor']['codigoZeta']))
            duplicate_state='unchecked'
            if checked:
                status=last.get('estado')
                if status=='CONFIRMADO':state,duplicate_state='loaded','existing'
                elif status=='RESULTADOS_EN_ZETA_NO_CARGAR':state,duplicate_state='existing','existing'
                elif status in {'AUSENTE_VERIFICADO','APTO_PENDIENTE_GUARDADO'}:duplicate_state='absent'
                else:duplicate_state='uncertain'
            if special_indicators and state not in {'existing','loaded'}:
                state='manual'
                reasons=['El XML contiene '+', '.join(VAT_LABELS.get(code,'indicador fiscal '+code) for code in sorted(special_indicators))+
                         '. Usá el resumen por IVA para completar el comprobante en Zeta.']
            elif not lines and state not in {'existing','loaded'} and all(raw['indicadorIVA']=='5' and Decimal(str(raw['monto']))==0 for raw in source['lineas']):
                state='manual'
                reasons=['Comprobante compuesto solo por entregas gratuitas de importe cero. Revisar en Zeta sin inventar una compra con importe.']
            if not lines and state not in {'existing','loaded','manual'} and source['lineas'] and all(
                    idx in auto_rounding_indices or idx in excluded
                    for idx in range(len(source['lineas']))):
                state='manual'
                reasons=['El documento solo contiene líneas sin carga monetaria o redondeos; no se crea una factura vacía.']
            if source['tipo'] in {'181','182','183'}:
                state, reasons = 'non_invoice', []
            elif state not in {'existing','loaded'} and (source['tipo'] not in {'111','112'} or source.get('ajustesGlobales')):
                state='manual'
                kind={'112':'Nota de crédito','113':'Nota de débito'}.get(source['tipo'],'Comprobante tipo '+source['tipo'])
                reasons=[('La factura contiene descuentos o recargos globales.' if source.get('ajustesGlobales') else kind+'.')+' Completar en Zeta: este caso todavía no tiene carga automática. No bloquea las otras facturas.']
            if is_credit_note and not valid_credit_note_reference(source.get('referencias', [])) and state not in {'existing','loaded','manual'}:
                state='manual'
                reasons=['Nota de crédito sin referencia fiscal identificable en el XML: revisar la factura afectada y el signo de la devolución.']
            elif is_credit_note and mode == 'cash' and state not in {'existing','loaded','manual'}:
                state='needs_review'
                reasons.append('Devolución contado: falta validar cómo Zeta registra el reintegro a caja; no se guarda automáticamente.')
            if decision.get('skip') or source['rut'] in settings.get('excluded_suppliers:'+company,{}):
                state, reasons = 'skipped', []
            can_precheck=state not in {'skipped','non_invoice','loaded'} and (
                state!='manual' or (source['tipo'] in {'111','112'} and provider is not None))
            result.append({'key': record['key'], 'source': source, 'decision': decision, 'proposals': proposals, 'mode': mode, 'modeLocked':mode_locked, 'modePending':mode_pending, 'paymentCode':payment['code'] if mode=='cash' and payment else '', 'paymentDefault':mode=='cash' and not decision.get('payment'), 'form': form, 'state': state, 'can_precheck':can_precheck, 'reasons': reasons, 'finesa':is_finesa, 'suggested_center':center if 'center' not in decision else '', 'auto_rounding_lines':sorted(auto_rounding_indices), 'non_monetary_lines':sorted(non_monetary), 'tax_groups':tax_breakdown(source,excluded),'payload': payload, 'plan': plan,'duplicate_state':duplicate_state,'last_result':last})
            with self.connect() as db:
                for proposal in proposals:
                    db.execute('INSERT OR REPLACE INTO proposals VALUES(?,?,?,?)',(company,record['key'],proposal['index'],json.dumps(dict(proposal,generated=now()))))
        return result

    def approve_supplier(self, key, code, *, only_if_unassigned=False):
        self.assert_idle()
        invoice = next(i for i in self.invoices() if i['key'] == key)
        supplier = next(c for c in self.catalog() if c['kind'] == 'supplier' and c['code'] == code)
        actual = (invoice.get('last_result') or {}).get('proveedor_zeta', {})
        if actual.get('codigoZeta')==code and not actual.get('rut'):
            supplier=dict(supplier,name=actual['nombre'])
        if supplier.get('rut'):
            raise ValueError('Solo se confirma un proveedor con RUT vacío.')
        settings = self.settings()
        approvals = settings.get('approvals', {})
        if code in approvals and approvals[code] != invoice['source']['rut']:
            if only_if_unassigned:return
            raise ValueError('Ese proveedor ya está relacionado con otro RUT.')
        approvals[code] = invoice['source']['rut']
        with self.connect() as db:
            db.execute('INSERT OR REPLACE INTO settings VALUES(?,?)', ('approvals', json.dumps(approvals)))
            db.execute('INSERT OR REPLACE INTO fiscal_identities VALUES(?,?,?,?,?)', (self.company(),code,invoice['source']['rut'],json.dumps({'xml_name':invoice['source']['nombre'],'catalog_name':supplier['name'],'catalog_version':supplier['version']}),now()))
            row=db.execute('SELECT decision FROM invoices WHERE company=? AND key=?',(self.company(),key)).fetchone()
            decision=json.loads(row['decision'])
            decision['supplier']=code
            db.execute('UPDATE invoices SET decision=? WHERE company=? AND key=?',(json.dumps(decision),self.company(),key))
        # Use the existing engine approval format, not a console prompt.
        path = self.directory / 'configuracion_zeta.json'
        config = json.loads(path.read_text(encoding='utf-8-sig'))
        config.setdefault('correlaciones_proveedor_confirmadas', {})[invoice['source']['rut']] = {'codigoZeta': code, 'nombreXML': invoice['source']['nombre'], 'nombreZeta': supplier['name'], 'rutXML': invoice['source']['rut'], 'fecha': now(), 'confirmado_por_usuario': True, 'fuente': 'Confirmación en Cerecito'}
        path.write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding='utf-8')

    def import_history(self, content):
        self.assert_idle()
        from openpyxl import load_workbook
        load_workbook(io.BytesIO(content), read_only=True, data_only=True).close()
        folder = self.directory / 'entrada/zeta'
        folder.mkdir(parents=True, exist_ok=True)
        existing = list(folder.glob('*.xlsx'))
        path = folder / (hashlib.sha256(content).hexdigest()+'.xlsx')
        if path.exists():
            archive = self.directory / 'entrada/referencia_articulos'
            archive.mkdir(parents=True, exist_ok=True)
            (archive / (hashlib.sha256(path.read_bytes()).hexdigest() + '.xlsx')).write_bytes(path.read_bytes())
        path.write_bytes(content)

    def import_legacy(self, content, commit=False):
        """Keep the complete export, merge invoice overrides only after an explicit preview."""
        self.assert_idle()
        export = json.loads(content.decode('utf-8-sig'))
        if not isinstance(export, dict) or not any(k in export for k in ('invoiceOverrides', 'comprobantes')):
            raise ValueError('Usá la exportación de correlaciones o el JSON para agente del Cerecito anterior.')
        patches, warnings = {}, []
        known = {i['key']: i for i in self.invoices()}
        for key, old in export.get('invoiceOverrides', {}).items():
            if key not in known:
                warnings.append('Importá el XML antes de migrar: ' + key)
                continue
            patch = {'lines': old.get('lines', {}), 'skip': bool(old.get('skipped')), 'reviewed': bool(old.get('reviewed'))}
            for legacy, new in [('center','center'), ('paymentForm','payment')]:
                if legacy in old:patch[new] = old[legacy]
            if old.get('mode') in {'credit','cash'}:patch['mode'] = old['mode']
            elif old.get('voucher'):patch['mode'] = 'cash' if 'Contado' in old['voucher'] else 'credit'
            for index in old.get('excludedLines', []):patch['lines'][str(index)] = {'ignore': True}
            patches[key] = patch
        for invoice in export.get('comprobantes', []):
            key = invoice['clave']
            if key not in known:
                warnings.append('Importá el XML antes de migrar: ' + key)
                continue
            patch = {'supplier': invoice.get('proveedor', {}).get('codigoZeta',''), 'center': invoice.get('zeta', {}).get('centroCosto',''), 'skip': bool(invoice.get('excluido')), 'reviewed': bool(invoice.get('revisado')), 'lines': {}}
            form = invoice.get('zeta', {}).get('comprobante', '')
            patch['mode'] = 'cash' if 'Contado' in form else 'credit'
            patch['payment'] = invoice.get('zeta', {}).get('formaPago','')
            for line in invoice.get('lineas', []):
                if len(line.get('origenes', [])) != 1:
                    warnings.append('Línea agrupada: requiere elegir sus orígenes en ' + key)
                    continue
                index = line['origenes'][0]
                raw = known[key]['source']['lineas'][index]
                if str(line.get('indicadorIVA')) != raw['indicadorIVA'] or Decimal(str(line['monto'])) != Decimal(raw['monto']):
                    raise ValueError('La exportación no coincide con el origen fiscal: ' + key)
                patch['lines'][str(index)] = {'kind': line['tipoDestino'], 'code': str(line['codigoZeta'])}
            for index in invoice.get('lineasIgnoradas', []):patch['lines'][str(index)] = {'ignore': True}
            patches[key] = patch
        if export.get('manualRules'):
            warnings.append('Las reglas anteriores carecen de alcance IVA verificable: se conservan en el archivo externo, pero deben confirmarse desde cada línea antes de usarse automáticamente.')
        if commit:
            if any(not w.startswith('Las reglas anteriores') for w in warnings):
                raise ValueError('La migración tiene advertencias. Resolvelas antes de confirmar; no se descartaron decisiones.')
            for key, patch in patches.items():self.decide(key, patch)
            archive = self.directory / 'entrada/correlaciones'
            archive.mkdir(parents=True, exist_ok=True)
            (archive / (hashlib.sha256(content).hexdigest() + '.json')).write_bytes(content)
        return {'count': len(patches), 'warnings': warnings, 'patches': patches}
