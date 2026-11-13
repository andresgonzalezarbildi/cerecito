"""Browser acceptance on synthetic data, never connects to Zeta."""
from http.server import ThreadingHTTPServer
import os
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from test_workflow import xml, COMPANY, SUPPLIER
import launcher
from workspace_store import Store
from workspace_jobs import Jobs


class BrowserTests(unittest.TestCase):
    def test_review_restore_and_background_progress(self):
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            self.skipTest('Playwright no instalado')
        chrome=Path(os.environ.get('PROGRAMFILES','C:/Program Files'))/'Google/Chrome/Application/chrome.exe'
        if not chrome.is_file():self.skipTest('Chrome no instalado')
        with tempfile.TemporaryDirectory(prefix='cerecito-browser-') as temp:
            store=Store(temp)
            store.configure({'rut':COMPANY,'name':'Empresa sintética','tolerance':'1'})
            store.import_catalog([
                {'kind':'supplier','code':'P1','name':'Proveedor sintético','rut':SUPPLIER},
                {'kind':'article','code':'A1','name':'Alimentos exento','vat':'1'},
                {'kind':'center','code':'SECTOR1','name':'A&B'}])
            store.import_documents('synthetic.xml',xml())
            entered,release=threading.Event(),threading.Event()
            def runner(payload,save,progress,cancelled):
                if payload.get('duplicates_only'):
                    progress({'clave':payload['comprobantes'][0]['clave'],'estado':'AUSENTE_VERIFICADO'})
                    return 0
                entered.set();release.wait(5)
                progress({'clave':payload['comprobantes'][0]['clave'],'proveedor':'Proveedor sintético','estado':'CONFIRMADO'})
                return 0
            jobs=Jobs(store,runner)
            with patch.object(launcher,'SERVICE',store),patch.object(launcher,'JOB_SERVICE',jobs),patch.object(launcher,'DATA_DIR',Path(temp)):
                server=ThreadingHTTPServer(('127.0.0.1',0),launcher.Handler)
                thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
                try:
                    with sync_playwright() as pw:
                        browser=pw.chromium.launch(executable_path=str(chrome),headless=True)
                        try:
                            page=browser.new_page()
                            errors=[]
                            page.on('pageerror',lambda error:errors.append(str(error)))
                            page.goto(f'http://127.0.0.1:{server.server_port}/')
                            page.get_by_role('button',name='Revisar pendientes',exact=True).click()
                            page.locator('#mode').select_option('credit')
                            page.wait_for_function("state.invoices[0].mode==='credit'")
                            # A background refresh must release the progress screen
                            # even when another request already received completion.
                            page.evaluate("state.jobs.find(j=>j.data.precheck).status='running';draw()")
                            self.assertEqual(page.locator('#precheckProgress').count(),1)
                            page.evaluate('refresh(false)')
                            page.wait_for_selector('.invoice-sheet')
                            self.assertEqual(page.locator('#precheckProgress').count(),0)
                            # Recover the same stale screen when state is already final.
                            page.evaluate("document.querySelector('#content').innerHTML='<section id=precheckProgress>Revisando</section>'")
                            page.evaluate('pollJobs()')
                            page.wait_for_selector('.invoice-sheet')

                            page.locator('.invoice-extra summary').click()
                            self.assertEqual(page.get_by_role('searchbox',name='Buscar proveedor').count(),0)
                            page.locator('#invoiceSector').select_option('SECTOR1')
                            page.get_by_role('button',name='Aplicar sector a toda la factura').click()
                            page.wait_for_function("state.invoices[0].state==='ready'")
                            page.get_by_role('button',name='Resumen',exact=True).click()
                            page.get_by_role('button',name='Revisar pendientes',exact=True).click()
                            page.wait_for_selector('.invoice-sheet')
                            self.assertEqual(page.locator('.invoice-sheet').count(),1)
                            self.assertEqual(page.locator('.invoice-sticky').evaluate("e=>getComputedStyle(e).position"),'sticky')
                            self.assertTrue(page.locator('.line-description').inner_text())
                            page.locator('.line-assignment summary').first.click()
                            page.locator('#search0').fill('A1')
                            page.locator('#choices0 button').first.click()
                            page.wait_for_selector('.invoice-sheet')
                            page.set_viewport_size({'width':390,'height':844})
                            self.assertTrue(page.evaluate('document.documentElement.scrollWidth <= innerWidth'))
                            page.set_viewport_size({'width':1280,'height':900})
                            page.locator('#invoiceSector').select_option('__none__')
                            page.get_by_role('button',name='Aplicar sector a toda la factura').click()
                            page.wait_for_function("state.invoices[0].decision.center==='' && state.invoices[0].decision.lines['0'].code==='A1'")
                            page.get_by_role('button',name='Confirmar y siguiente').click()
                            page.wait_for_selector('#load')
                            page.reload()
                            page.get_by_role('button',name='Cargar comprobantes',exact=True).click()
                            self.assertTrue(entered.wait(2))
                            page.wait_for_selector('#content progress',state='visible')
                            page.get_by_role('button',name='Resumen',exact=True).click()
                            self.assertTrue(page.locator('#content').is_visible())
                            self.assertTrue(page.locator('#load').is_disabled())
                            self.assertIn('curso',page.locator('#load').text_content())
                            release.set()
                            page.get_by_role('button',name='Resultados e historial',exact=True).click()
                            page.wait_for_function("document.querySelector('#content').textContent.includes('CONFIRMADO')",timeout=8000)
                            page.evaluate("focusKey=state.invoices[0].key;page='review';draw()")
                            self.assertEqual(page.locator('.invoice-sheet').count(),0)
                            self.assertEqual(page.locator('[data-edit]').count(),0)
                            self.assertTrue(page.locator('#load').is_disabled())
                            self.assertEqual(errors,[])
                        finally:release.set();browser.close()
                finally:release.set();server.shutdown();server.server_close();thread.join(2)


if __name__=='__main__':unittest.main()
