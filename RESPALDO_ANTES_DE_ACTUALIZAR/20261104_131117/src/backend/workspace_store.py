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
from xml_source import read_zip_documents, read_xml_documents, preview_net_lines, non_monetary_line_indices


def now():
    return datetime.now(timezone.utc).isoformat()


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
                    rates={'exento':'1','0':'1','10':'2','22':'3'}
                    if rate not in rates:raise ValueError('La columna IVA debe indicar Exento, 0, 10 o 22. Usá vat para indicadores 1, 2 o 3.')
                    row['vat']=rates[rate]
            if row.get('kind') not in allowed or not str(row.get('code', '')).strip() or not row.get('name'):
                raise ValueError('Cada fila requiere kind, code y name. Ver formato en Ayuda.')
            row['code'] = str(row['code']).strip()
            row['name'] = str(row['name']).strip()
            if 'rut' in row and row['rut'] is not None:row['rut']=str(row['rut']).strip()
            if 'vat' in row:
                vat=row['vat']
                if isinstance(vat,list):
                    if any(str(value) not in {'1','2','3'} for value in vat):raise ValueError('IVA de catálogo inválido.')
                    row['vat']=[str(value) for value in vat]
                elif vat is not None:
                    values=str(vat).split('|')
                    if any(value not in {'1','2','3'} for value in values if value):raise ValueError('IVA de catálogo inválido.')
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
            for raw, source in documents:
                if source['receptor'] != company:
                    raise ValueError('El receptor del XML no coincide con la empresa.')
                existing = db.execute('SELECT source FROM invoices WHERE company=? AND key=?', (company, source['clave'])).fetchone()
                if existing and json.loads(existing['source'])['sha256CFE'] != source['sha256CFE']:
                    raise ValueError('XML diferente con la misma identidad fiscal: ' + source['clave'])
                db.execute('INSERT OR IGNORE INTO invoices(company,key,source) VALUES(?,?,?)', (company, source['clave'], json.dumps(source)))
                for index, line in enumerate(source['lineas']):
                    db.execute('INSERT OR IGNORE INTO original_lines VALUES(?,?,?,?)', (company, source['clave'], index, json.dumps(line)))
            path.write_bytes(content)
            batch = {'filename': filename, 'path': str(path), 'keys': [s['clave'] for _, s in documents], 'date': now(), 'fresh_review':filename.lower().endswith('.zip')}
            db.execute('INSERT OR REPLACE INTO settings VALUES(?,?)', ('batch', json.dumps(batch)))
        return batch

    def decide(self, key, patch, learn_lines=False):
        company = self.company()
        with self.connect() as db:
            row = db.execute('SELECT decision,source FROM invoices WHERE company=? AND key=?', (company, key)).fetchone()
            if not row:
                raise ValueError('Comprobante inexistente.')
            if db.execute("SELECT 1 FROM jobs WHERE company=? AND status IN ('queued','running','cancelling')", (company,)).fetchone():
                raise ValueError('Esperá a que termine el trabajo antes de cambiar decisiones.')
            allowed = {'mode', 'supplier', 'center', 'payment', 'lines', 'skip', 'reviewed', 'destinationKind', 'skipSupplier'}
            if set(patch) - allowed:
                raise ValueError('Campo de decisión desconocido.')
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
                    learned.append((company,source['rut'],raw['descripcion'].casefold().strip(),raw['indicadorIVA'],json.dumps({'kind':target['kind'],'code':target['code']})))
            if 'lines' in patch:
                patch = dict(patch, lines={**decision.get('lines', {}), **patch['lines']})
            if 'center' in patch:
                center=patch['center']
                if not isinstance(center,str) or (center and not any(c['kind']=='center' and c['code']==center for c in self.catalog())):
                    raise ValueError('Sector inexistente en el catálogo.')
                setting='supplier_centers:'+company
                row_setting=db.execute('SELECT value FROM settings WHERE key=?',(setting,)).fetchone()
                preferences=json.loads(row_setting['value']) if row_setting else {}
                # Preferencia por RUT emisor, no por texto del nombre.
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
        catalog=self.catalog()
        configured=self.settings().get('sectors:'+self.company(),{})
        result=[{'code':'','name':'Sin sector / para distribuir','mapping':configured.get('',{}),'configured':'' in configured}]
        for center in (c for c in catalog if c['kind']=='center'):
            saved=configured.get(center['code'])
            mapping={}
            # Taxonomy suggests families, while the actual codes always come from masters.
            name=center['name'].casefold()
            family='alimentos' if name=='a&b' else ('material limpieza rooms' if 'housekeeping' in name else '')
            if family:
                for vat,pattern in [('1',r'exent'),('2',r'10\s*%'),('3',r'22\s*%')]:
                    matches=[c for c in catalog if c['kind']=='article' and family in c['name'].casefold() and re.search(pattern,c['name'],re.I)]
                    if len(matches)==1:mapping[vat]={'kind':'article','code':matches[0]['code']}
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
        keys = settings.get('batch', {}).get('keys', [])
        learned_centers=settings.get('supplier_centers:'+company,{})
        result = []
        for record in records:
            if record['key'] not in keys:
                continue
            source, decision = json.loads(record['source']), json.loads(record['decision'])
            batch=settings.get('batch',{})
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
            reasons, proposals, lines = [], [], []
            if not provider:
                reasons.append('Elegí el proveedor del catálogo.')
            elif str(provider.get('rut', '')) != source['rut']:
                approvals = settings.get('approvals', {})
                if provider.get('rut'):
                    reasons.append('El RUT del proveedor contradice el XML.')
                elif approvals.get(provider['code']) != source['rut']:
                    reasons.append('Confirmá la relación del proveedor sin RUT.')
            mode_locked=source['formaPagoXML']=='2'
            mode = 'credit' if mode_locked else (decision.get('mode') or ('cash' if source['formaPagoXML'] == '1' else 'credit'))
            mode_pending=source['formaPagoXML']=='1' and not decision.get('mode') and not decision.get('reviewed')
            if mode_pending:reasons.append('Corrobora la modalidad de la factura contado: contado o credito.')
            special_indicators={
                str(raw.get('indicadorIVA','')) for raw in source['lineas']
                if str(raw.get('indicadorIVA','')) not in {'1','2','3','6','7'}
                and not (str(raw.get('indicadorIVA',''))=='5' and Decimal(str(raw.get('monto','0')))==0)
            }
            non_monetary_indices = set(non_monetary_line_indices(source))
            sign_errors = []
            for index, raw in enumerate(source['lineas']):
                if index in non_monetary_indices:
                    reason = ('Entrega gratuita' if raw['indicadorIVA']=='5' else 'Línea de importe cero')
                    proposals.append({'index':index,'options':[],'rule':None,'reason':reason+': se conserva en el XML, sin cargar un artículo de valor cero.','confidence':1})
                    continue
                if raw['indicadorIVA'] in {'1','2','3'} and (Decimal(raw['monto']) < 0 or Decimal(raw['cantidad']) <= 0):
                    sign_errors.append((index, raw))
                    proposals.append({'index':index,'options':[],'rule':None,'reason':'Importe negativo o cantidad no positiva: requiere revisión contable en Zeta.','confidence':0})
                    continue
                if raw['indicadorIVA'] not in {'1','2','3','6','7'}:
                    proposals.append({'index':index,'options':[],'rule':None,'reason':'Indicador fiscal especial: no es una tasa de IVA.','confidence':0})
                    continue
                choice = decision.get('lines', {}).get(str(index))
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
                    reasons.append('Elegí el destino de la línea ' + str(index + 1))
                    continue
                allowed_vat=target.get('vat', [])
                if isinstance(allowed_vat,str):allowed_vat=allowed_vat.split('|') if allowed_vat else []
                if raw['indicadorIVA'] not in allowed_vat:
                    reasons.append('Verificá IVA del destino en la línea ' + str(index + 1))
                lines.append(dict(raw, origenes=[index], codigoZeta=target['code'], descripcionZeta=target['name'], tipoDestino=target['kind']))
            kinds = {l['tipoDestino'] for l in lines}
            form = ('Compra Crédito' if mode == 'credit' else 'Compra Contado') + (' Gastos' if kinds == {'concept'} else '')
            if len(kinds) > 1:
                reasons.append('Destinos mixtos: falta verificar una alternativa de Zeta para una única factura.')
            effective_payment=decision.get('payment') or next((c['code'] for c in catalog if c['kind']=='payment' and c['name'].strip().casefold()=='efectivo'),'')
            payment = next((c for c in catalog if c['kind'] == 'payment' and c['code'] == effective_payment), None)
            if mode == 'cash' and not payment:
                reasons.append('Elegí una forma de pago real.')
            center = decision.get('center', learned_centers.get(source['rut'],''))
            if center and not any(c['kind'] == 'center' and c['code'] == center for c in catalog):
                reasons.append('El centro elegido no está en el catálogo.')
            payload = {'clave': source['clave'], 'cfe': dict(source['totales'], **{k: source[k] for k in ('tipo','serie','numero','fecha','moneda')}), 'proveedor': {'rut': source['rut'], 'nombre': source['nombre'], 'codigoZeta': provider['code'] if provider else ''}, 'lineas': lines, 'lineasIgnoradas': [int(i) for i, c in decision.get('lines', {}).items() if c.get('ignore')], 'zeta': {'comprobante': form, 'centroCosto': center, 'formaPago':payment['code'] if mode=='cash' and payment else '', 'formaPagoNombre':payment['name'] if mode=='cash' and payment else ''}, 'completo': True, 'estado': 'reviewed' if decision.get('reviewed') else 'auto'}
            if provider and ('genérico' in provider['name'].casefold() or 'generico' in provider['name'].casefold()):payload['proveedor']['generico']=True
            plan = None
            if not reasons:
                try:
                    plan = preview_net_lines(source, payload, company, Decimal(settings['company'].get('tolerance', '1')))
                except ValueError as error:
                    reasons.append(str(error))
            state = 'needs_review' if reasons else 'ready'
            last=results.get(record['key'])
            positive=positive_results.get(record['key'])
            if positive and positive.get('source_hash')==source['sha256CFE'] and (not batch.get('fresh_review') or positive.get('checked_at','')>=batch.get('date','')):last=positive
            if last and batch.get('fresh_review') and last.get('checked_at','')<batch.get('date',''):last=None
            checked=bool(last and last.get('source_hash')==source['sha256CFE'] and (last.get('estado') in {'CONFIRMADO','RESULTADOS_EN_ZETA_NO_CARGAR'} or last.get('supplier_code')==payload['proveedor']['codigoZeta']))
            duplicate_state='unchecked'
            if checked:
                status=last.get('estado')
                if status=='CONFIRMADO':state,duplicate_state='loaded','existing'
                elif status=='RESULTADOS_EN_ZETA_NO_CARGAR':state,duplicate_state='existing','existing'
                elif status in {'AUSENTE_VERIFICADO','APTO_PENDIENTE_GUARDADO'}:duplicate_state='absent'
                else:duplicate_state='uncertain'
            if special_indicators and state not in {'existing','loaded'}:
                names={'5':'Entrega gratuita con importe distinto de cero','11':'Impuesto percibido','12':'IVA en suspenso','16':'Indicador fiscal especial 16'}
                state='manual'
                reasons=['El XML contiene '+', '.join(names.get(code,'indicador fiscal '+code) for code in sorted(special_indicators))+
                         '. No equivale a IVA 0%, 10% ni 22%. Requiere tratamiento específico en Zeta; no modificar el XML.']
            elif sign_errors and state not in {'existing','loaded'}:
                state='manual'
                reasons=['La línea '+str(index+1)+' ('+raw['descripcion']+') tiene importe '+str(raw['monto'])+
                         ' y cantidad '+str(raw['cantidad'])+'. Los descuentos/importes negativos y las cantidades no positivas requieren revisar el documento en Zeta. No se omiten automáticamente.'
                         for index,raw in sign_errors]
            elif not lines and state not in {'existing','loaded'} and source['lineas'] and len(non_monetary_indices)==len(source['lineas']):
                state='manual'
                reasons=['El comprobante solo contiene líneas de importe cero. No se creará una factura vacía en Zeta.']
            if source['tipo'] in {'181','182','183'}:
                state, reasons = 'non_invoice', []
            elif state not in {'existing','loaded'} and (source['tipo']!='111' or source.get('ajustesGlobales')):
                state='manual'
                kind={'112':'Nota de crédito','113':'Nota de débito'}.get(source['tipo'],'Comprobante tipo '+source['tipo'])
                reasons=[('La factura contiene descuentos o recargos globales.' if source.get('ajustesGlobales') else kind+'.')+' Completar en Zeta: este caso todavía no tiene carga automática. No bloquea las otras facturas.']
            if decision.get('skip') or source['rut'] in settings.get('excluded_suppliers:'+company,{}):
                state, reasons = 'skipped', []
            result.append({'key': record['key'], 'source': source, 'decision': decision, 'proposals': proposals, 'mode': mode, 'modeLocked':mode_locked, 'modePending':mode_pending, 'paymentCode':payment['code'] if mode=='cash' and payment else '', 'paymentDefault':mode=='cash' and not decision.get('payment'), 'form': form, 'state': state, 'reasons': reasons, 'suggested_center':center if 'center' not in decision and source['rut'] in learned_centers else '', 'non_monetary_lines':sorted(non_monetary_indices), 'payload': payload, 'plan': plan,'duplicate_state':duplicate_state,'last_result':last})
            with self.connect() as db:
                for proposal in proposals:
                    db.execute('INSERT OR REPLACE INTO proposals VALUES(?,?,?,?)',(company,record['key'],proposal['index'],json.dumps(dict(proposal,generated=now()))))
        return result

    def approve_supplier(self, key, code):
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
