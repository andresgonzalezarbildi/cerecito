"""Acceso mediante el formulario y el autocompletado de Chrome, sin copiar claves."""
import re
import time
from urllib.parse import urlsplit
from playwright.sync_api import TimeoutError as PlaywrightTimeout

ORIGIN = 'https://www.zetasoftware.com'
PURCHASES = '/z.gestion.comprobantes.compras'


def company_row(rows, rut):
    matches = [row for row in rows if re.sub(r'\D', '', row['rut']) == rut]
    if len(matches) != 1:
        raise ValueError('No se identifica una única empresa con el RUT configurado en Zeta.')
    return matches[0]['id']


def prepare_session(page, config):
    """Solo navegación y un intento de login; nunca reintenta un guardado."""
    rut = str(config.get('empresa_rut', ''))
    if not re.fullmatch(r'\d{12}', rut):
        raise ValueError('Falta el RUT de la empresa para abrir Zeta.')
    page.bring_to_front()
    page.goto(ORIGIN + '/z.usuarios.seleccionarempresa', wait_until='load')
    deadline = time.monotonic() + 75
    submitted = selected = False
    while time.monotonic() < deadline:
        url = urlsplit(page.url)
        if url.scheme != 'https' or url.hostname != 'www.zetasoftware.com':
            raise ValueError('El acceso salió del sitio esperado de Zeta.')
        path = url.path.lower()
        if path == PURCHASES:
            return
        if path == '/z.info.inicio':
            if submitted:
                raise ValueError('Zeta no aceptó el acceso automático. Revisá el aviso en Chrome; no se repite la contraseña.')
            page.locator('#vUSULIBRAEMAIL').click()
            page.keyboard.press('Escape')
            try:
                page.wait_for_function("() => Boolean(document.querySelector('#vUSULIBRAEMAIL')?.value && document.querySelector('#vSIMPLEPASSWORD')?.value)", timeout=8000)
            except PlaywrightTimeout:
                raise ValueError('Chrome está abierto, pero no completó el acceso guardado. Elegí la contraseña guardada en el perfil del bot y volvé a buscar.') from None
            page.locator('#BTNENTER').click()
            submitted = True
            try:
                page.wait_for_url(lambda u: urlsplit(u).path != '/z.info.inicio', timeout=20000)
            except PlaywrightTimeout:
                raise ValueError('Zeta no completó el inicio de sesión. Revisá el aviso o la verificación adicional en Chrome.') from None
        elif path == '/z.usuarios.seleccionarempresa':
            if selected:
                raise ValueError('Zeta no abrió la empresa seleccionada.')
            page.locator('[id^="span_EMPRUT_"]').first.wait_for(state='attached', timeout=15000)
            rows = page.locator('[id^="span_EMPRUT_"]').evaluate_all("els => els.map(e => ({rut:e.textContent,id:'vSELECCIONAR_'+e.id.replace('span_EMPRUT_','')}))")
            target = company_row(rows, rut)
            notice = page.get_by_text('Entendido', exact=True)
            if notice.count() == 1 and notice.is_visible():notice.click()
            control=page.locator('#' + target)
            control.wait_for(state='visible',timeout=15000)
            control.click(timeout=10000)
            selected = True
            page.wait_for_url(lambda u: urlsplit(u).path != '/z.usuarios.seleccionarempresa', timeout=20000)
            # La URL cambia antes de que termine la redirección de la empresa.
            # Esperar su carga evita que esa navegación aborte la de Compras.
            page.wait_for_load_state('load', timeout=20000)
            page.goto(ORIGIN + PURCHASES, wait_until='load')
        elif path == '/z.usuarios.home':
            page.wait_for_timeout(300)
        else:
            raise ValueError('Zeta requiere un paso de acceso adicional en Chrome: ' + path)
    raise ValueError('Zeta demoró demasiado en abrir Compras. Volvé a buscar cuando termine de responder.')
