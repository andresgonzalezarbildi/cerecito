"""Native Zeta catalog exports through preview and commit."""
import io
from pathlib import Path
import sys
import tempfile
import unittest
from openpyxl import Workbook

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src/backend'))
from workspace_store import Store


class CatalogDetectionTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store=Store(self.temp.name)
        self.store.configure({'rut':'000000000001','name':'Prueba','tolerance':'1'})

    def workbook(self,headers,values,title='Sheet0',banner=None):
        book=Workbook();sheet=book.active;sheet.title=title
        if banner:sheet.append([banner])
        sheet.append(headers);sheet.append(values)
        stream=io.BytesIO();book.save(stream);book.close()
        return stream.getvalue()

    def test_standard_filenames_detect_each_catalog_and_allow_same_code(self):
        for filename,kind in [('ContactosWWExport-13.xlsx','supplier'),('ArticulosWWExport-14.xlsx','article'),('ConceptosDeGastosWWExport-15.xlsx','concept')]:
            with self.subTest(filename=filename):
                data=self.workbook(['Código','Nombre'],['A1','Nombre de prueba'])
                rows=self.store.preview_catalog(filename,data)
                self.assertEqual(rows[0]['kind'],kind)
                self.store.import_catalog(rows)
        self.assertEqual({row['kind'] for row in self.store.catalog()},{'supplier','article','concept'})

    def test_sheet_and_specific_column_identify_renamed_exports(self):
        for headers,title,kind in [(['Código','Nombre'],'Contactos','supplier'),(['Código artículo','Descripción'],'Sheet0','article'),(['Código concepto','Nombre'],'Sheet0','concept')]:
            data=self.workbook(headers,['001','Prueba'],title)
            rows=self.store.preview_catalog('descarga.xlsx',data)
            self.assertEqual(rows[0]['kind'],kind)
            self.assertEqual(rows[0]['code'],'001')

    def test_unknown_or_contradictory_type_requires_explicit_choice(self):
        data=self.workbook(['Código','Nombre'],['1','Prueba'])
        with self.assertRaisesRegex(ValueError,'Elegí el tipo'):
            self.store.preview_catalog('descarga.xlsx',data)
        self.assertEqual(self.store.preview_catalog('descarga.xlsx',data,'concept')[0]['kind'],'concept')
        data=self.workbook(['Código concepto','Nombre'],['1','Prueba'])
        with self.assertRaisesRegex(ValueError,'único catálogo'):
            self.store.preview_catalog('ArticulosWWExport.xlsx',data)

    def test_vat_is_kept_when_type_is_detected(self):
        data=self.workbook(['Código','Descripción','IVA'],['AL103','Alimentos 22%',22])
        rows=self.store.preview_catalog('ArticulosWWExport.xlsx',data)
        self.assertEqual(rows[0]['vat'],'3')
        self.store.import_catalog(rows)
        self.assertEqual(self.store.catalog()[0]['vat'],'3')


if __name__=='__main__':unittest.main()
