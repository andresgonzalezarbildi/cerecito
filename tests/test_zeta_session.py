import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src/backend'))
from zeta_session import company_row, prepare_session, ORIGIN
from chrome_connection import connect_chrome


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
