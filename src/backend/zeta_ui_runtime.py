"""Controles deterministas del formulario real. No contiene acciones de guardado."""

from runtime_paths import DATA_DIR, CODE_DIR, APP_DIR, APP_URL, company_rut, control_value, category_provider_rut
import time
from urllib.parse import urlsplit
from playwright.sync_api import Error


def wait_value(read, predicate, message, timeout=60):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            result = read()
            if predicate(result):
                return result
        except Error:
            pass
        time.sleep(.15)
    raise RuntimeError(message)


def wait_path(page, path):
    result = wait_value(lambda: page.evaluate('location.pathname'), lambda value: value == path,
                        'Zeta no abrió el formulario esperado: ' + path)
    page.wait_for_load_state('load', timeout=15000)
    server = path.rsplit('/', 1)[-1].removeprefix('z.')
    wait_value(lambda: page.evaluate('window.gx?.O?.ServerClass'),
               lambda value: value == server, 'GeneXus no terminó de inicializar el formulario.')
    # PXTools puede dejar SPA en estado navegando después de cambiar de formulario.
    # Recargar la URL del editor/detalle antes de escribir campos restablece el estado.
    if page.evaluate('window.gx?.spa?.isNavigating()'):
        page.reload(wait_until='load', timeout=30000)
        wait_value(lambda: page.evaluate('window.gx?.O?.ServerClass'),
                   lambda value: value == server, 'GeneXus no inicializó el formulario recargado.')
    return result


def click_readonly(locator):
    if locator.count() != 1:
        raise RuntimeError('Control de navegación ausente o ambiguo.')
    # GeneXus utiliza enlaces/inputs sin un cuadro estable para el clic de Playwright.
    # Este helper se limita a navegación y selección; nunca confirmar ni eliminar.
    forbidden = {'BTNTRN_ENTER', 'BTNELIMINARFACTURA', 'BTNANULAR', 'BTNDUPLICARFACTURA'}
    if locator.get_attribute('id') in forbidden:
        raise RuntimeError('Acción contable prohibida en click_readonly.')
    if locator.get_attribute('id') == 'BTNENTER':
        if not locator.is_visible() or not locator.is_enabled():
            raise RuntimeError('Nueva línea no está habilitada en este detalle; no se fuerza un control oculto.')
        locator.evaluate('''element => {
            if (gx.O.ServerClass!=='gestion.comprobantes.facturacompra') throw Error('Confirmar no es navegación de solo lectura');
            const field = gx.O.getValidStructFld(element);
            if (!field || !field.evt || typeof gx.O[field.evt] !== 'function') throw Error('Evento de Nueva línea no disponible');
            gx.O[field.evt]();
        }''')
    else:
        locator.evaluate('element => element.click()')


def prompt_frame(page):
    def read():
        page.evaluate('document.querySelectorAll("iframe").length')
        frames = [frame for frame in page.frames if frame.parent_frame == page.main_frame
                  and 'prompt' in urlsplit(frame.url).path.lower()]
        return frames
    frames = wait_value(read, lambda frames: len(frames) == 1, 'Buscador ausente o ambiguo.')
    frame = frames[0]
    frame.wait_for_load_state('load', timeout=15000)
    wait_value(lambda: frame.locator('#GridContainerTbl').count(), bool, 'Buscador sin tabla.')
    return frame


def select_prompt_result(locator,read,predicate,message):
    """El clic de selección puede destruir su iframe antes de responder a CDP."""
    try:click_readonly(locator)
    except Error as error:
        if 'Frame was detached' not in str(error):raise
        # No repetir el clic: la lectura del formulario padre decide si se seleccionó.
    return wait_value(read,predicate,message)


def table_signature(frame):
    return frame.locator('#GridContainerTbl').inner_text(timeout=3000)


def select_article(page, code, description, max_pages=10):
    if not code or not description:
        raise RuntimeError('Artículo sin código o descripción correlacionada.')
    existing = [f for f in page.frames if urlsplit(f.url).path == '/z.gestion.configuracion.articulosprompt']
    if not existing:
        open_prompt(page, '[data-gx-for="ARTIDLIN"] a', 'z.gestion.configuracion.articulosprompt')
    frame = prompt_frame(page)
    seen = set()
    for _ in range(max_pages):
        signature = table_signature(frame)
        if signature in seen:
            raise RuntimeError('El buscador repitió una página; no se selecciona un artículo.')
        seen.add(signature)
        rows = frame.locator('#GridContainerTbl tbody tr')
        row = rows.filter(has=frame.get_by_text(code, exact=True)).filter(
            has=frame.get_by_text(description, exact=True))
        if row.count() > 1:
            raise RuntimeError('Artículo ambiguo en el buscador.')
        if row.count() == 1:
            select_prompt_result(row.locator('img[title="Seleccionar"]'),
                       lambda: page.locator('#ARTIDLIN').input_value(timeout=3000),
                       lambda value: value.startswith(code + ' - '), 'Zeta seleccionó otro artículo.')
            wait_value(lambda: page.locator('#LINCONCEPTO').input_value(timeout=3000),
                       lambda value: value == description, 'La descripción del artículo difiere del catálogo.')
            return
        next_link = frame.get_by_text('Sig', exact=True)
        if next_link.count() != 1 or next_link.locator('..').get_attribute('class', timeout=3000) and 'disabled' in (next_link.locator('..').get_attribute('class') or ''):
            break
        click_readonly(next_link)
        wait_value(lambda: table_signature(frame), lambda value: value != signature,
                   'El buscador no avanzó a la página siguiente.')
    raise RuntimeError('Artículo correlacionado no encontrado en el catálogo de Zeta.')


def open_prompt(page, selector, name):
    control = page.locator(selector)
    if control.count() != 1:
        raise RuntimeError('Enlace de buscador ausente o ambiguo.')
    control.evaluate('''(element, name) => {
        const href=element.getAttribute('href'), prefix="javascript:gx.popup.openPrompt('"+name+"'";
        // PXTools de Zeta redefine startsWith como expresión regular: usar indexOf.
        if (!href || href.indexOf(prefix)!==0) throw Error('Buscador diferente al esperado');
        new Function(href.slice(11))();
    }''', name)
