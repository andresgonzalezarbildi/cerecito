"""Inicializa Cerecito nuevo desde la carpeta de datos anterior sin modificarla."""
import argparse
import json
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / 'src/backend'))
from runtime_paths import DATA_DIR, initialize
from workspace_store import Store, now


def parse_database(path):
    text = path.read_text(encoding='utf-8-sig')
    return json.JSONDecoder().raw_decode(text[text.index('{'):])[0]


def folded(value):
    return str(value).casefold().strip()


def vat_from_name(name):
    name = folded(name)
    if 'exento' in name or 'exoner' in name:
        return '1'
    if '10%' in name or '10 %' in name:
        return '2'
    if '22%' in name or '22 %' in name:
        return '3'
    return ''


def legacy_value(row, beginning):
    return next((value for key, value in row.items() if folded(key).startswith(beginning)), '')


def copy_missing(source, target):
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        return 0
    shutil.copy2(source, target)
    return 1


def historic_vat_options(database):
    values = {}
    for centers in database.get('taxCenter', {}).values():
        for taxes in centers.values():
            for vat, record in taxes.items():
                target = record.get('target', {})
                key = target.get('kind'), str(target.get('code', ''))
                if str(vat) in {'1','2','3'}:
                    values.setdefault(key, set()).add(str(vat))
    return values


def rows_from_legacy(database):
    rows, targets = [], {}
    vat_options = historic_vat_options(database)
    for entry in database.get('catalog', []):
        kind, code = entry.get('kind'), str(entry.get('code', ''))
        name = entry.get('desc', entry.get('name', ''))
        if kind in {'article', 'concept'} and code and name:
            observed = sorted(vat_options.get((kind, code), set()))
            targets[(kind, code)] = {'kind': kind, 'code': code, 'name': name, 'vat': observed or ([vat_from_name(name)] if vat_from_name(name) else []), 'provenance': 'catálogo histórico'}
    rows.extend(targets.values())
    for rut, record in database.get('providers', {}).items():
        code, name = str(record.get('providerCode', '')), record.get('providerName', '')
        if code and name:
            rows.append({'kind': 'supplier', 'code': code, 'name': name, 'rut': str(rut), 'confidence': float(record.get('confidence', 0)), 'provenance': 'histórico Zeta'})
    used_centers = set()
    for entry in database.get('centerOptions', []):
        code, name = str(entry.get('code', '')), entry.get('name', '')
        if code and name and code not in used_centers:
            rows.append({'kind': 'center', 'code': code, 'name': name, 'provenance': 'catálogo histórico'})
            used_centers.add(code)
    for entry in database.get('formasPago', []):
        code, name = str(legacy_value(entry, 'c')), legacy_value(entry, 'nombre')
        if code and name:
            rows.append({'kind': 'payment', 'code': code, 'name': name, 'provenance': 'catálogo histórico'})
    for entry in database.get('comprobantes', []):
        code, name = str(legacy_value(entry, 'c')), legacy_value(entry, 'nombre')
        if code and name:
            rows.append({'kind': 'form', 'code': code, 'name': name, 'provenance': 'catálogo histórico'})
    # Zeta exports can retain an older RUT for a reused supplier code. Keep the
    # best-supported identity and leave contradictory XML identities for review.
    unique = {}
    for row in rows:
        key = row['kind'], row['code']
        previous = unique.get(key)
        if previous is None or float(row.get('confidence', 1)) > float(previous.get('confidence', 1)):
            unique[key] = row
    return list(unique.values()), targets


def historic_suggestions(database, targets):
    for supplier, descriptions in database.get('itemAny', {}).items():
        for description, record in descriptions.items():
            target = record.get('target', {})
            kind, code = target.get('kind'), str(target.get('code', ''))
            catalog = targets.get((kind, code))
            confidence = float(record.get('confidence', 0))
            if catalog and catalog['vat'] and confidence >= .5:
                for vat in catalog['vat']:
                    yield supplier, folded(description), vat, {'kind': kind, 'code': code, 'confidence': confidence, 'source': 'histórico Zeta', 'updated': now()}


def run(source):
    source = Path(source).resolve()
    if source == DATA_DIR or DATA_DIR in source.parents:
        raise ValueError('La fuente debe ser la carpeta anterior, no la carpeta nueva de datos.')
    config_file, history_file = source / 'configuracion_zeta.json', source / 'base_historica.js'
    if not config_file.is_file() or not history_file.is_file():
        raise ValueError('No se encontró configuracion_zeta.json o base_historica.js en la fuente.')
    initialize()
    old_config = json.loads(config_file.read_text(encoding='utf-8-sig'))
    company = str(old_config.get('empresa_rut', ''))
    if not (company.isdigit() and len(company) == 12):
        raise ValueError('La configuración anterior no tiene un RUT de empresa válido.')
    # Preserve the browser profile already created in the target. Only merge operational settings.
    target_config = DATA_DIR / 'configuracion_zeta.json'
    target = json.loads(target_config.read_text(encoding='utf-8-sig')) if target_config.exists() else {}
    target.update(old_config)
    target['chrome'] = {'endpoint': 'http://127.0.0.1:9223'}
    target['carga_lote'] = {'habilitada': False}
    target_config.write_text(json.dumps(target, ensure_ascii=False, indent=2), encoding='utf-8')
    store = Store(DATA_DIR)
    store.configure({'rut': company, 'name': 'Empresa ' + company, 'tolerance': old_config.get('tolerancia_total', '1.00'), 'control': old_config.get('control_duplicados', {})})
    database = parse_database(history_file)
    rows, targets = rows_from_legacy(database)
    store.import_catalog(rows, 'migración inicial de catálogos históricos')
    suggestions = list(historic_suggestions(database, targets))
    with store.connect() as connection:
        for supplier, description, vat, record in suggestions:
            connection.execute('INSERT OR REPLACE INTO legacy_suggestions VALUES(?,?,?,?,?)', (company, supplier, description, vat, json.dumps(record)))
        connection.execute('INSERT OR REPLACE INTO settings VALUES(?,?)', ('legacy_migration', json.dumps({'source': str(source), 'date': now(), 'catalog_records': len(rows), 'suggestions': len(suggestions)})))
    copied = 0
    for folder in ('catalogos', 'referencia_articulos', 'zeta', 'xml', 'cerecito'):
        origin = source / 'entrada' / folder
        if origin.is_dir():
            for file in origin.rglob('*'):
                if file.is_file():
                    copied += copy_missing(file, DATA_DIR / 'entrada' / folder / file.relative_to(origin))
    for folder in ('estado', 'salida'):
        origin = source / folder
        if origin.is_dir():
            for file in origin.rglob('*'):
                if file.is_file() and file.suffix.lower() in {'.json', '.jsonl', '.csv'}:
                    copied += copy_missing(file, DATA_DIR / 'entrada/migracion_anterior' / folder / file.relative_to(origin))
    historical = next((file for file in (DATA_DIR / 'entrada/zeta').glob('*.xlsx') if file.name != 'historico.xlsx'), None)
    if historical:
        copied += copy_missing(historical, DATA_DIR / 'entrada/zeta/historico.xlsx')
    return {'empresa': company, 'catalogos': len(rows), 'sugerencias_historicas': len(suggestions), 'archivos_copiados': copied, 'datos': str(DATA_DIR)}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source', type=Path)
    print(json.dumps(run(parser.parse_args().source), ensure_ascii=False, indent=2))
