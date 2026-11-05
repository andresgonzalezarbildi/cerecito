"""Instalador acumulativo de Cerecito: IVA, aprendizaje, genérico, redondeo, notas de crédito, cierre de ventana.

Ejecutar desde la carpeta principal de Cerecito. No modifica los archivos de datos.
"""
from pathlib import Path
from datetime import datetime
import hashlib
import json
import os
import shutil
import socket
import sys
import tempfile

ROOT = Path(__file__).resolve().parent
PACK = ROOT / 'ARCHIVOS_ACTUALIZADOS'
MANIFEST = json.loads((PACK / 'manifest.json').read_text(encoding='utf-8'))


def sha256(p):
    h=hashlib.sha256()
    with p.open('rb') as f:
        for b in iter(lambda:f.read(65536), b''):
            h.update(b)
    return h.hexdigest()


def main():
    if not (ROOT / 'ABRIR_CERECITO.bat').is_file() or not (ROOT / 'src/backend/workspace_store.py').is_file():
        print('ERROR: extrae el ZIP en la carpeta principal de Cerecito, donde está ABRIR_CERECITO.bat.')
        return 2
    port = int(os.environ.get('CERECITO_UI_PORT', '8765'))
    try:
        with socket.create_connection(('127.0.0.1', port), timeout=0.4):
            print(f'ERROR: hay un programa abierto en el puerto {port}. Cerrá Cerecito antes de actualizar.')
            return 2
    except OSError:
        pass

    changes=[]
    for rel,expected_hash in MANIFEST['archivos'].items():
        source=PACK / rel
        target=ROOT / rel
        if not source.is_file() or sha256(source)!=expected_hash:
            print('ERROR: archivo de actualización faltante o dañado:',rel)
            return 2
        if rel.endswith('.py'):
            try:
                compile(source.read_text(encoding='utf-8-sig'),str(source),'exec')
            except SyntaxError as error:
                print('ERROR en código recibido:',error)
                return 2
        if not target.is_file() or sha256(target)!=expected_hash:
            changes.append((rel,source,target))
    if not changes:
        print('Cerecito ya está actualizado. No se cambió nada.')
        return 0

    stamp=datetime.now().strftime('%Y%m%d_%H%M%S')
    backup=ROOT / 'RESPALDO_ANTES_DE_ACTUALIZAR' / stamp
    # Primero crear todos los respaldos, sin modificar archivos de aplicación.
    original=set()
    try:
        for rel,src,dst in changes:
            if dst.is_file():
                original.add(rel)
                backup_target=backup / rel
                backup_target.parent.mkdir(parents=True,exist_ok=True)
                shutil.copy2(dst,backup_target)
        if original:
            print('Respaldo realizado en:',backup)
        processed=[]
        for rel,src,dst in changes:
            dst.parent.mkdir(parents=True,exist_ok=True)
            temp_path=None
            try:
                with tempfile.NamedTemporaryFile(dir=dst.parent,prefix='.cerecito_actualizacion_',delete=False) as tmp:
                    temp_path=Path(tmp.name)
                shutil.copy2(src,temp_path)
                os.replace(temp_path,dst)
                processed.append((rel,dst))
            finally:
                if temp_path and temp_path.exists():temp_path.unlink()
            print('Actualizado:',rel)
    except Exception as error:
        print('Error al instalar. Restaurando archivos anteriores:',error)
        for rel,dst in reversed(locals().get('processed',[])):
            if rel in original:
                shutil.copy2(backup / rel,dst)
            elif dst.exists():
                dst.unlink()
        return 1
    print('\nLISTO: actualización acumulativa aplicada.')
    print('No se tocaron facturas, base SQLite, configuraciones ni reglas aprendidas.')
    print('Reabrí Cerecito con ABRIR_CERECITO.bat.')
    return 0


if __name__=='__main__':
    sys.exit(main())
