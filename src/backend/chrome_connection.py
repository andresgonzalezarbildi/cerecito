"""Conexión al Chrome habitual. Nunca lanza ni cierra navegadores/pestañas."""
from __future__ import annotations

from runtime_paths import DATA_DIR, CODE_DIR, APP_DIR, APP_URL, company_rut, control_value, category_provider_rut

import json
import os
import re
import time
import atexit
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import build_opener, ProxyHandler

from playwright.sync_api import Error as PlaywrightError
from cdp_transport import list_targets, ScopedCDP


class ChromeError(RuntimeError):
    pass


def endpoint_from_input(text):
    text = text.strip().replace('Server running at:', '').strip()
    if text.isdigit():
        text = 'http://127.0.0.1:' + text
    if re.fullmatch(r'(?:localhost|127\.0\.0\.1):\d+', text):
        text = 'http://' + text
    parsed = urlparse(text)
    try:
        port = parsed.port
    except ValueError:
        port = None
    if (parsed.scheme not in {'http', 'ws'} or parsed.hostname not in {'localhost', '127.0.0.1', '::1'}
            or not port or parsed.username or parsed.password or parsed.query or parsed.fragment):
        raise ChromeError('Usá solamente un puerto o una dirección local de depuración de Chrome.')
    if parsed.scheme == 'ws' and not parsed.path.startswith('/devtools/browser'):
        raise ChromeError('La dirección WebSocket debe apuntar a /devtools/browser.')
    return text


def websocket_endpoint(endpoint):
    endpoint = endpoint_from_input(endpoint)
    if endpoint.startswith('ws:'):
        return endpoint
    parsed = urlparse(endpoint)
    origin = f'{parsed.scheme}://{parsed.netloc}'
    # Chrome iniciado con --remote-debugging-port publica /json/version.
    # Chrome con chrome://inspect no lo publica: usa /devtools/browser.
    opener = build_opener(ProxyHandler({}))
    try:
        with opener.open(origin + '/json/version', timeout=2) as response:
            data = json.load(response)
        return endpoint_from_input(data['webSocketDebuggerUrl'])
    except HTTPError as exc:
        if exc.code != 404:
            raise ChromeError(f'Chrome respondió HTTP {exc.code}.') from exc
        return f'ws://{parsed.netloc}/devtools/browser'
    except (URLError, OSError, ValueError, KeyError) as exc:
        raise ChromeError('No responde Chrome. Revisá el puerto en chrome://inspect/#remote-debugging.') from exc


def connect_chrome(playwright, config=None, endpoint=None, *, interactive=False):
    config = config or {}
    endpoint = endpoint or os.environ.get('CERECITO_CHROME_ENDPOINT') or config.get('chrome', {}).get('endpoint') or 'http://127.0.0.1:9222'
    endpoint = websocket_endpoint(endpoint)
    print('Conectando al Chrome configurado:', endpoint, flush=True)
    if endpoint.endswith('/devtools/browser'):
        print('Si Chrome pide permiso de depuración, aceptalo.', flush=True)
    bridge = None
    upstream = None
    try:
        # Una pestaña ajena congelada puede bloquear la inicialización de CDP.
        # Enumerar targets desde el proceso del navegador no toca sus renderers.
        all_targets, upstream = list_targets(endpoint, keep_connection=True)
        targets = [t for t in all_targets if t['type'] == 'page']
        configured = config.get('url', '')
        host = urlparse(configured).hostname if configured.startswith('https://') else None
        candidates = [t for t in targets if (urlparse(t['url']).hostname == host if host else 'zeta' in (t['url'] + ' ' + t['title']).lower())]
        if len(candidates)>1 and urlparse(configured).path.startswith('/z.gestion.comprobantes.'):
            purchases=[t for t in candidates if urlparse(t['url']).path.startswith('/z.gestion.comprobantes.')]
            if len(purchases)==1:candidates=purchases
        if len(candidates) != 1:
            if not interactive:
                raise ChromeError(f'Hay {len(candidates)} pestañas de Zeta identificables. No se elige otra automáticamente.')
            for i, target in enumerate(targets, 1):
                print(f"{i}. {target['title']} | {target['url'].split('?', 1)[0]}")
            answer = input('Número de pestaña de Zeta: ').strip()
            if not answer.isdigit() or not 1 <= int(answer) <= len(targets):
                raise ChromeError('Número de pestaña inválido.')
            chosen = targets[int(answer) - 1]
        else:
          chosen = candidates[0]
        if 'ingresar' in chosen.get('title','').lower() and urlparse(chosen['url']).path=='/z.info.inicio':
            raise ChromeError('La sesión de Zeta venció. Iniciá sesión en el Chrome del bot y abrí la empresa; no se guardó nada.')
        print('Pestaña seleccionada:', chosen['url'].split('?', 1)[0], flush=True)
        bridge = ScopedCDP(endpoint, chosen['targetId'], initial_websocket=upstream)
        endpoint = bridge.url
        browser = playwright.chromium.connect_over_cdp(endpoint, no_defaults=True, timeout=45000)
    except (PlaywrightError, OSError, ValueError, ChromeError) as exc:
        if bridge:
            bridge.close()
        elif upstream:
            upstream.close()
        if isinstance(exc, ChromeError):
            raise
        raise ChromeError('Chrome no completó la conexión. Revisá el permiso de depuración y la dirección actual. No se reintenta ninguna operación.') from exc
    atexit.register(bridge.close)
    return browser


def choose_page(browser, config=None, *, interactive=False):
    config = config or {}
    # No depender de contexts[0] ni pages[0]; Chrome puede anunciar targets tarde.
    deadline = time.monotonic() + 5
    pages = []
    while time.monotonic() < deadline:
        if not browser.is_connected():
            raise ChromeError('Se perdió la conexión a Chrome. La operación queda detenida.')
        pages = [p for c in browser.contexts for p in c.pages if not p.is_closed()]
        if pages:
            break
        time.sleep(.1)
    if not pages:
        raise ChromeError('Chrome no expuso pestañas. Revisá el permiso de depuración y el perfil con Zeta abierto.')
    configured = config.get('url', '')
    host = urlparse(configured).hostname if configured.startswith('https://') else None
    candidates = [p for p in pages if (urlparse(p.url).hostname == host if host else 'zeta' in p.url.lower())]
    if len(candidates) == 1:
        return candidates[0]
    if not interactive:
        raise ChromeError(f'Hay {len(candidates)} pestañas de Zeta; dejá una sola o ejecutá el capturador para elegirla.')
    print('Elegí la pestaña de Zeta:')
    for i, page in enumerate(pages, 1):
        print(f'{i}. {page.url.split("?", 1)[0]}')
    answer = input('Número: ').strip()
    if not answer.isdigit() or not 1 <= int(answer) <= len(pages):
        raise ChromeError('Número de pestaña inválido.')
    return pages[int(answer) - 1]


def locator_for(page, step):
    root = page
    for selector in step.get('frames', []):
        iframe = root.locator(selector)
        if iframe.count() != 1:
            raise ChromeError(f'Iframe ambiguo o inexistente: {selector}')
        root = root.frame_locator(selector)
    return root.locator(step['selector'])
