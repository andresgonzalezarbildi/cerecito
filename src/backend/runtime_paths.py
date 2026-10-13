"""Rutas de código y datos; actualizar el repositorio no modifica los datos."""
import json
import os
from pathlib import Path

ROOT=Path(__file__).resolve().parents[2]
CODE_DIR=ROOT/'src/backend'
APP_DIR=ROOT/'src/web'
DATA_DIR=Path(os.environ.get('CERECITO_DATA_DIR',str(ROOT.parent/'cerecito-datos-locales'))).expanduser().resolve()
APP_URL='http://127.0.0.1:'+os.environ.get('CERECITO_UI_PORT','8765')+'/'

def initialize():
    if DATA_DIR==ROOT or ROOT in DATA_DIR.parents:
        raise ValueError('La carpeta de datos debe estar fuera del repositorio. Configurá CERECITO_DATA_DIR.')
    for folder in ('importar','web','estado','salida','perfil_chrome_bot','entrada/xml','entrada/zeta','entrada/cerecito','entrada/correlaciones','entrada/catalogos','entrada/referencia_articulos'):
        (DATA_DIR/folder).mkdir(parents=True,exist_ok=True)
    config=DATA_DIR/'configuracion_zeta.json'
    if not config.exists():config.write_bytes((ROOT/'config/configuracion_zeta.example.json').read_bytes())
    return DATA_DIR

def config():
    return json.loads((DATA_DIR/'configuracion_zeta.json').read_text(encoding='utf-8-sig'))

def company_rut():
    value=str(config().get('empresa_rut',''))
    if not value.isdigit() or len(value)!=12:raise ValueError('Configurá el RUT de la empresa en la carpeta de datos antes de cargar en Zeta.')
    return value

def control_value(field):
    value=str(config().get('control_duplicados',{}).get(field,''))
    if not value:raise ValueError('Falta configurar el comprobante de control de duplicados: '+field)
    return value

def category_provider_rut():
    return str(config().get('proveedor_destinos_por_producto') or '__no_configurado__')
