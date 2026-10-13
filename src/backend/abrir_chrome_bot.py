"""Abre Google Chrome con un perfil persistente exclusivo del bot."""

from runtime_paths import DATA_DIR, CODE_DIR, APP_DIR, APP_URL, company_rut, control_value, category_provider_rut
import json
import os
from pathlib import Path
import subprocess
import time
from urllib.request import build_opener, ProxyHandler

from conciliar import BASE

ENDPOINT = 'http://127.0.0.1:9223'
PROFILE = DATA_DIR / 'perfil_chrome_bot'


def version():
    with build_opener(ProxyHandler({})).open(ENDPOINT + '/json/version', timeout=1) as response:
        info = json.load(response)
    if not info.get('webSocketDebuggerUrl', '').startswith('ws://127.0.0.1:9223/'):
        raise RuntimeError('El puerto 9223 no anuncia el depurador local esperado.')
    return info


def main():
    try:
        info = version()
        print('Chrome de automatización ya está abierto en ' + ENDPOINT)
        print('Navegador: ' + info.get('Browser', 'Chrome'))
        return 0
    except OSError:
        pass
    candidates = [Path(os.environ.get('PROGRAMFILES', r'C:\Program Files')) / 'Google/Chrome/Application/chrome.exe',
                  Path(os.environ.get('PROGRAMFILES(X86)', r'C:\Program Files (x86)')) / 'Google/Chrome/Application/chrome.exe',
                  Path(os.environ.get('LOCALAPPDATA', str(Path.home()/'AppData/Local'))) / 'Google/Chrome/Application/chrome.exe']
    chrome = next((p for p in candidates if p.is_file()), None)
    if chrome is None:
        raise RuntimeError('No se encuentra Google Chrome instalado.')
    PROFILE.mkdir(parents=True, exist_ok=True)
    subprocess.Popen([str(chrome), '--remote-debugging-address=127.0.0.1',
                      '--remote-debugging-port=9223', '--user-data-dir=' + str(PROFILE),
                      '--no-first-run', '--no-default-browser-check', '--new-window',
                      'https://www.zetasoftware.com/z.gestion.comprobantes.compras'],
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        try:
            info = version()
            print('Chrome abierto. Depuración verificada en ' + ENDPOINT)
            print('Navegador: ' + info.get('Browser', 'Chrome'))
            print('Perfil persistente: ' + str(PROFILE))
            print('Iniciá sesión en Zeta en esta ventana; el bot no guarda credenciales.')
            return 0
        except OSError:
            time.sleep(.4)
    raise RuntimeError('Chrome no publicó el puerto 9223 dentro del plazo. No se cierra ningún navegador.')


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(str(exc))
        raise SystemExit(1)
