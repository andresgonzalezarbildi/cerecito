import ast
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src/backend'))
import runtime_paths

class SeparationTests(unittest.TestCase):
    def test_source_is_valid_python(self):
        files=list((ROOT/'src').rglob('*.py'))+list((ROOT/'tests').rglob('*.py'))+[ROOT/'launcher.py',ROOT/'migrar_datos.py']
        for file in files:
            ast.parse(file.read_text(encoding='utf-8-sig'),filename=str(file))

    def test_initialize_writes_only_external_data(self):
        with tempfile.TemporaryDirectory(dir=ROOT.parent,prefix='cerecito-test-') as temporary:
            data=Path(temporary).resolve()
            self.assertTrue(data.is_relative_to(ROOT.parent.resolve()))
            with patch.object(runtime_paths,'DATA_DIR',data):
                runtime_paths.initialize()
                self.assertTrue((data/'entrada/xml').is_dir())
                self.assertTrue((data/'configuracion_zeta.json').is_file())
                self.assertFalse(runtime_paths.ROOT in data.parents)

    def test_data_directory_inside_repository_is_rejected(self):
        with patch.object(runtime_paths,'DATA_DIR',ROOT/'datos'):
            with self.assertRaises(ValueError):runtime_paths.initialize()

    def test_example_has_no_company_or_confirmed_providers(self):
        example=json.loads((ROOT/'config/configuracion_zeta.example.json').read_text())
        self.assertEqual(example['empresa_rut'],'')
        self.assertEqual(example['correlaciones_proveedor_confirmadas'],{})
        self.assertFalse(example['carga_lote']['habilitada'])

    def test_no_runtime_input_files_in_repository(self):
        forbidden={'.xml','.xlsx','.zip','.sqlite','.db','.jsonl','.log','.pdf','.csv'}
        # Una instalación existente puede conservar datos excluidos por Git.
        # Comprobar las carpetas de código, no recorrer el perfil de Chrome ni
        # los datos locales conservados junto a la instalación anterior.
        files=[path for folder in ('src','config','docs','tests') for path in (ROOT/folder).rglob('*') if path.is_file()]
        self.assertEqual([str(path.relative_to(ROOT)) for path in files if path.suffix in forbidden],[])

if __name__=='__main__':unittest.main()
