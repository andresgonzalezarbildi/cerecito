import sys
import unittest
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src/backend'))
from zeta_session import company_row, prepare_session, ORIGIN
from chrome_connection import connect_chrome,restore_default_downloads,ChromeError


class SessionTests(unittest.TestCase):
    def test_company_match_is_exact_and_unambiguous(self):
        rows = [{'rut': '000000000001', 'id': 'wrong'}, {'rut': '000000000002', 'id': 'correct'}]
        self.assertEqual(company_row(rows, '000000000002'), 'correct')
        for candidates in [rows[:1], rows + [rows[1]]]:
            with self.assertRaises(ValueError): company_row(candidates, '000000000002')

    def test_closed_managed_browser_is_opened_before_connect(self):
        with patch('abrir_chrome_bot.main') as launch, patch('chrome_connection.websocket_endpoint', side_effect=RuntimeError('stop')):
            with self.assertRaises(RuntimeError):
                connect_chrome(MagicMock(), {'chrome': {'endpoint': 'http://127.0.0.1:9223'}})
            launch.assert_called_once()

    def test_custom_browser_is_not_launched(self):
        with patch('abrir_chrome_bot.main') as launch, patch('chrome_connection.websocket_endpoint', side_effect=RuntimeError('stop')):
            with self.assertRaises(RuntimeError):
                connect_chrome(MagicMock(), endpoint='http://127.0.0.1:9222')
            launch.assert_not_called()

    def test_connections_preserve_native_downloads_and_restore_managed_browser(self):
        for managed in (True,False):
            with self.subTest(managed=managed),tempfile.TemporaryDirectory() as temp:
                endpoint='http://127.0.0.1:'+('9223' if managed else '9222')
                upstream=MagicMock()
                upstream.receive.return_value={'id':2,'result':{}}
                bridge=MagicMock();bridge.url='ws://127.0.0.1:3333/devtools/browser/test'
                playwright=MagicMock()
                target={'type':'page','targetId':'zeta','url':'https://www.zetasoftware.com/z.gestion.comprobantes.compras','title':'Zeta'}
                with patch('chrome_connection.DATA_DIR',Path(temp)),patch('abrir_chrome_bot.main'), \
                     patch('chrome_connection.websocket_endpoint',return_value='ws://127.0.0.1:9223/devtools/browser/root'), \
                     patch('chrome_connection.list_targets',return_value=([target],upstream)), \
                     patch('chrome_connection.ScopedCDP',return_value=bridge),patch('chrome_connection.atexit.register'):
                    connect_chrome(playwright,endpoint=endpoint)
                playwright.chromium.connect_over_cdp.assert_called_once_with(bridge.url,no_defaults=True,timeout=45000)
                if managed:
                    upstream.send_json.assert_called_once_with({'id':2,'method':'Browser.setDownloadBehavior','params':{'behavior':'default'}})
                else:upstream.send_json.assert_not_called()

    def test_native_download_reset_rejects_protocol_errors(self):
        connection=MagicMock()
        connection.receive.side_effect=[{'method':'Browser.downloadProgress'},{'id':2,'error':{'message':'error'}}]
        with self.assertRaisesRegex(ChromeError,'restaurar sus descargas'):
            restore_default_downloads(connection)

    def test_saved_login_company_and_purchases(self):
        page = MagicMock()
        page.url = ORIGIN + '/z.info.inicio'
        def clicked(selector):
            if selector == '#BTNENTER': page.url = ORIGIN + '/z.usuarios.seleccionarempresa'
            if selector == '#vSELECCIONAR_0002': page.url = ORIGIN + '/z.gestion.inicio'
        controls = {}
        def locator(selector):
            if selector not in controls:
                controls[selector] = MagicMock()
                controls[selector].click.side_effect = lambda **kwargs: None if kwargs.get('trial') else clicked(selector)
            return controls[selector]
        page.locator.side_effect = locator
        locator('[id^="span_EMPRUT_"]').evaluate_all.return_value = [{'rut': '000000000002', 'id': 'vSELECCIONAR_0002'}]
        page.get_by_text.return_value.count.return_value = 0
        def goto(url, **kwargs):
            if url.endswith('/z.gestion.comprobantes.compras'): page.url = url
        page.goto.side_effect = goto
        prepare_session(page, {'empresa_rut': '000000000002'})
        locator('#BTNENTER').click.assert_called_once()
        locator('#vSELECCIONAR_0002').click.assert_called_once_with(timeout=10000)
        self.assertTrue(page.url.endswith('/z.gestion.comprobantes.compras'))
        locator('#vSIMPLEPASSWORD').input_value.assert_not_called()


if __name__ == '__main__': unittest.main()
