"""Comportamiento del ciclo de vida de Cerecito, sin abrir un navegador real."""
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import MagicMock, patch

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
import launcher


class WindowLifecycleTests(unittest.TestCase):
    def test_window_has_its_own_browser_profile_and_app_mode(self):
        with tempfile.TemporaryDirectory() as temp:
            browser=Path(temp)/'msedge.exe'
            browser.write_bytes(b'')
            with patch.object(launcher,'find_window_browser',return_value=browser), \
                 patch.object(launcher,'PREFERENCES',Path(temp)/'Cerecito'/'runtime.json'), \
                 patch.object(launcher.subprocess,'Popen') as popen:
                launcher.open_cerecito_window('http://127.0.0.1:8765/')
            popen.assert_called_once()
            args=popen.call_args.args[0]
            self.assertEqual(args[0],str(browser))
            self.assertIn('--app=http://127.0.0.1:8765/',args)
            self.assertIn('--disable-background-mode',args)
            self.assertTrue(any(a.startswith('--user-data-dir=') and 'perfil_ventana' in a for a in args))

    def test_window_closing_shuts_down_python_server(self):
        server=MagicMock()
        window=MagicMock()
        with patch.object(launcher,'initialize'), patch.object(launcher,'services'), \
             patch.object(launcher,'ThreadingHTTPServer',return_value=server), \
             patch.object(launcher,'open_cerecito_window',return_value=window), \
             patch.object(launcher,'cancel_pending_jobs_before_exit') as cancel:
            launcher.main()
        window.wait.assert_called_once()
        cancel.assert_called_once()
        server.shutdown.assert_called_once()
        server.server_close.assert_called_once()

    def test_failed_launch_also_shuts_down_local_server(self):
        server=MagicMock()
        with patch.object(launcher,'initialize'), patch.object(launcher,'services'), \
             patch.object(launcher,'ThreadingHTTPServer',return_value=server), \
             patch.object(launcher,'open_cerecito_window',side_effect=RuntimeError('sin navegador')):
            with self.assertRaisesRegex(RuntimeError,'sin navegador'):
                launcher.main()
        server.shutdown.assert_called_once()
        server.server_close.assert_called_once()

    def test_closing_cancels_remaining_documents_before_exit(self):
        jobs=MagicMock()
        service=MagicMock()
        ctx=service.connect.return_value
        db=ctx.__enter__.return_value
        db.execute.side_effect=[[{'id':'A'}], []]
        with patch.object(launcher,'SERVICE',service), \
             patch.object(launcher,'JOB_SERVICE',jobs), \
             patch.object(launcher.time,'sleep') as sleep:
            launcher.cancel_pending_jobs_before_exit()
        jobs.cancel.assert_called_once_with('A')
        sleep.assert_called_once_with(0.25)


if __name__=='__main__':
    unittest.main()
