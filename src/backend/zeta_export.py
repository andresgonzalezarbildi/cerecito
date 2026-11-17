"""Exportación de compras: solo consulta y Excel, sin guardar comprobantes."""
from datetime import date, datetime
from pathlib import Path
import time


def period(invoices, today=None):
    dates = [date.fromisoformat(i['source']['fecha']) for i in invoices]
    if not dates:
        raise ValueError('Importá primero un ZIP con comprobantes.')
    return min(dates).isoformat(), max(today or date.today(), max(dates)).isoformat()


def download_excel(page, directory, cancelled, timeout=45, progress=None):
    """Un clic en Excel; tomar el archivo terminado sin esperar eventos CDP."""
    directory = Path(directory).resolve()
    directory.mkdir(parents=True, exist_ok=True)
    previous = set(directory.iterdir())
    # Reutilizar la conexión activa de Playwright: abrir otro WebSocket puede
    # pedir una segunda autorización de Chrome y dejar el trabajo esperando.
    session = page.context.browser.new_browser_cdp_session()
    downloads = set()
    session.on('Browser.downloadWillBegin', lambda event: downloads.add(event['guid']))
    completed = False
    try:
        session.send('Browser.setDownloadBehavior', {
            'behavior': 'allow', 'downloadPath': str(directory), 'eventsEnabled': True})
        if cancelled():
            raise InterruptedError('Comparación cancelada.')
        if progress: progress('Pulsando Excel en Zeta…')
        # No esperar navegación: GeneXus puede abrir una ventana para descargar.
        page.locator('#BTNEXPORT').click(timeout=10000, no_wait_after=True)
        if progress: progress('Esperando el archivo Excel descargado…')
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if cancelled():
                raise InterruptedError('Comparación cancelada.')
            files = [path for path in directory.iterdir() if path not in previous
                     and path.is_file() and path.suffix.lower() == '.xlsx'
                     and path.stat().st_size > 0]
            if len(files) > 1:
                raise ValueError('Se descargó más de un Excel durante la exportación. Reintentá la comparación.')
            if files:
                completed = True
                return files[0]
            # Bombea también los eventos del navegador; no depende de que Chrome
            # publique downloadProgress ni de identificar el frame de la descarga.
            page.wait_for_timeout(100)
        raise ValueError('No apareció el Excel después de pulsar el botón. Revisá en Chrome si Zeta mostró un aviso o bloqueó la descarga y reintentá.')
    finally:
        try:
            if not completed:
                for guid in downloads:
                    session.send('Browser.cancelDownload', {'guid': guid})
        finally:
            try:
                session.send('Browser.setDownloadBehavior', {'behavior': 'default', 'eventsEnabled': False})
            finally:
                session.detach()


def apply_export_filters(page, start, end):
    """Aplicar el rango una sola vez; Excel exporta la consulta sin paginarla."""
    from urllib.parse import urlsplit
    from zeta_compras import set_control
    from zeta_ui_runtime import wait_value
    fields = [('#vDOCFECHA1', start), ('#vDOCFECHA1_TO', end), ('#vFILTERFULLTEXT', '')]
    for selector, value in fields:
        set_control(page, selector, value)
    with page.expect_response(lambda response:
            urlsplit(response.url).path == '/z.gestion.comprobantes.compras'
            and response.request.method == 'POST', timeout=60000) as response:
        page.evaluate("gx.O.executeServerEvent('REFRESH', true, null, false, false)")
    response.value.finished()
    wait_value(lambda: page.evaluate('!gx.evt.processing && !gx.spa.isNavigating()'),
               bool, 'Zeta sigue aplicando las fechas para exportar.')
    if any(page.locator(selector).input_value() != value for selector, value in fields):
        raise ValueError('Zeta cambió las fechas de exportación o el filtro de búsqueda.')


def validate_export(path):
    """Leer el Excel descargado sin consultar ni recorrer las páginas de Zeta."""
    from openpyxl import load_workbook
    from conciliar import map_headings, excel_date, amount, number
    book = load_workbook(path, read_only=True, data_only=True)
    count = 0
    found = False
    try:
        for sheet in book.worksheets:
            headings = None
            for index, cells in enumerate(sheet.iter_rows(values_only=True), 1):
                if headings is None:
                    candidate = map_headings(cells)
                    if {'fecha', 'numero', 'proveedor', 'moneda', 'total'} <= candidate.keys():
                        headings = candidate
                        found = True
                    elif index >= 40:
                        break
                    continue
                def get(key):
                    pos = headings[key]
                    return cells[pos] if pos < len(cells) else None
                if not get('proveedor') and get('numero') in (None, ''):
                    continue
                parsed_date = excel_date(get('fecha'))
                if not parsed_date and isinstance(get('fecha'), str):
                    try:
                        parsed_date = datetime.strptime(get('fecha'), '%d/%m/%y').date().isoformat()
                    except ValueError:
                        pass
                if not parsed_date or not number(get('numero')) or amount(get('total')) is None or not get('proveedor'):
                    raise ValueError('El Excel descargado tiene comprobantes sin fecha, número, proveedor o importe.')
                count += 1
    finally:
        book.close()
    if not found:
        raise ValueError('El archivo descargado no es el resumen de compras esperado de Zeta.')
    return count


def export(directory, start, end, company, progress, cancelled):
    from playwright.sync_api import sync_playwright
    from runtime_paths import config, DATA_DIR
    from chrome_connection import connect_chrome, choose_page
    from zeta_bot import exclusive_lock
    from zeta_session import prepare_session
    directory = Path(directory)
    cfg = config()
    if cfg.get('empresa_rut') != company:
        raise ValueError('La empresa cambió antes de exportar.')
    def step(message):
        if cancelled():
            raise InterruptedError('Comparación cancelada.')
        progress(message)
    with exclusive_lock(DATA_DIR / 'estado/bot.lock'):
        with sync_playwright() as pw:
            step('Abriendo la empresa en Zeta…')
            browser = connect_chrome(pw, cfg)
            page = choose_page(browser, cfg)
            prepare_session(page, cfg)
            step('Aplicando las fechas del ZIP…')
            apply_export_filters(page, date.fromisoformat(start).strftime('%d/%m/%y'), date.fromisoformat(end).strftime('%d/%m/%y'))
            step('Descargando el Excel de compras…')
            path = download_excel(page, directory / 'descarga', cancelled, progress=step)
            step('Validando el Excel descargado…')
            count = validate_export(path)
            return {'filename': path.name, 'path': str(path), 'rows': count,
                    'company': company, 'from': start, 'to': end}
