"""Copia datos a la carpeta externa sin borrar ni modificar la instalación anterior."""
import argparse
import json
from pathlib import Path
import shutil
import sys
sys.path.insert(0,str(Path(__file__).resolve().parent/'src/backend'))
from runtime_paths import DATA_DIR,initialize

def copy_missing(source,target):
    if target.exists():return 0
    target.parent.mkdir(parents=True,exist_ok=True)
    shutil.copy2(source,target)
    return 1

def migrate(original):
    original=original.expanduser().resolve();initialize()
    if not (original/'archivos').is_dir():raise ValueError('No se encuentra la carpeta archivos de la instalación original.')
    copied=0
    for source in (original/'archivos').rglob('*'):
        if source.is_file() and source.suffix.lower() in {'.xml','.zip','.xlsx','.json','.csv'}:
            copied+=copy_missing(source,DATA_DIR/'entrada'/source.relative_to(original/'archivos'))
    states=original/'bot/automatizacion/estado'
    if states.is_dir():
        for source in states.rglob('*'):
            if source.is_file() and source.suffix.lower() in {'.json','.jsonl'}:
                copied+=copy_missing(source,DATA_DIR/'estado'/source.relative_to(states))
    historical=original/'bot/aplicacion_cerecito/cerecito-main/js/data.js'
    if historical.is_file():copied+=copy_missing(historical,DATA_DIR/'base_historica.js')
    print(f'{copied} archivos copiados a {DATA_DIR}. La instalación anterior se conservó.')
    print('Exportá también Catálogos y Correlaciones desde el Cerecito anterior e importalos en la nueva interfaz: localStorage no se copia con los archivos.')

if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('original',type=Path)
    migrate(parser.parse_args().original)
