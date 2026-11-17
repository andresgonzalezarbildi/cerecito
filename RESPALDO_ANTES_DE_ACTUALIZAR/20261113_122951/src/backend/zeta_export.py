"""Exportación de compras: solo consulta y Excel, sin guardar comprobantes."""
from datetime import date, datetime
from pathlib import Path
from collections import deque
import socket
import time


def period(invoices, today=None):
    dates = [date.fromisoformat(i['source']['fecha']) for i in invoices]
    if not dates:
        raise ValueError('Importá primero un ZIP con comprobantes.')
    return min(dates).isoformat(), max(today or date.today(), max(dates)).isoformat()


def command(connection, identifier, method, params, timeout=10, pending=None):
    connection.send_json({'id': identifier, 'method': method, 'params': params})
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            result = connection.receive()
        except socket.timeout:
            continue
        if result.get('id') == identifier:
            if result.get('error'):
                raise ValueError('Chrome no permitió preparar la descarga de Excel.')
            return result.get('result', {})
        if pending is not None and result.get('method', '').startswith('Browser.download'):
            pending.append(result)
    raise ValueError('Chrome no respondió al preparar la descarga.')


def download_excel(page, connection, directory, cancelled, timeout=120):
    """CDP allow conserva el nombre original; restaurar Descargas aun ante errores.

    https://chromedevtools.github.io/devtools-protocol/tot/Browser/
    Una conexión independiente recibe eventos sin alterar la sesión de Playwright.
    """
    directory = Path(directory).resolve()
    directory.mkdir(parents=True, exist_ok=True)
    session = page.context.new_cdp_session(page)
    try:
        frame = session.send('Page.getFrameTree')['frameTree']['frame']['id']
    finally:
        session.detach()
    pending = deque()
    def belongs_to_page(frame_id):
        if frame_id == frame:
            return True
        # GeneXus puede iniciar la descarga desde un iframe o una ventana hija.
        # Consultar su origen, sin tomar descargas de otras pestañas de Chrome.
        targets = command(connection, 4, 'Target.getTargets', {}, pending=pending).get('targetInfos', [])
        related = {frame}
        for _ in range(len(targets)):
            previous = len(related)
            for target in targets:
                if any(target.get(key) in related for key in ('parentId', 'openerId', 'parentFrameId', 'openerFrameId')):
                    related.add(target['targetId'])
            if len(related) == previous:
                break
        if frame_id in related:
            return True
        session = page.context.new_cdp_session(page)
        try:
            def frame_ids(tree):
                return {tree['frame']['id']}.union(*(frame_ids(child) for child in tree.get('childFrames', [])))
            return frame_id in frame_ids(session.send('Page.getFrameTree')['frameTree'])
        finally:
            session.detach()
    guid = filename = None
    try:
        command(connection, 1, 'Browser.setDownloadBehavior', {
            'behavior': 'allow', 'downloadPath': str(directory), 'eventsEnabled': True})
        if cancelled():
            raise InterruptedError('Comparación cancelada.')
        page.locator('#BTNEXPORT').click(timeout=15000)
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if cancelled():
                raise InterruptedError('Comparación cancelada.')
            try:
                event = pending.popleft() if pending else connection.receive()
            except socket.timeout:
                continue
            params = event.get('params', {})
            if event.get('method') == 'Browser.downloadWillBegin' and belongs_to_page(params.get('frameId')):
                if guid is not None:
                    raise ValueError('Zeta inició más de una descarga; volvé a comparar.')
                guid, filename = params['guid'], params['suggestedFilename']
                if Path(filename).name != filename or '/' in filename or '\\' in filename or not filename.lower().endswith('.xlsx'):
                    raise ValueError('Zeta no devolvió un archivo Excel .xlsx reconocible.')
            if event.get('method') == 'Browser.downloadProgress' and guid and params.get('guid') == guid:
                if params.get('state') == 'canceled':
                    raise ValueError('Zeta canceló la descarga de Excel.')
                if params.get('state') == 'completed':
                    # filePath es opcional en Chrome. La carpeta es exclusiva de este trabajo.
                    path = Path(params.get('filePath') or directory / filename).resolve()
                    if path.parent != directory or not path.is_file() or not path.stat().st_size:
                        raise ValueError('Chrome informó la descarga, pero el Excel no está disponible.')
                    return path
        raise ValueError('Zeta no completó la descarga de Excel en dos minutos. Reintentá la comparación.')
    finally:
        try:
            if guid and (cancelled() or not (directory / (filename or '')).is_file()):
                command(connection, 2, 'Browser.cancelDownload', {'guid': guid}, timeout=5)
        finally:
            command(connection, 3, 'Browser.setDownloadBehavior', {'behavior': 'default', 'eventsEnabled': False}, timeout=5)


def validate_export(path, rows):
    """Evitar presentar falsos faltantes si Zeta exportó solo una página."""
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
    if count != len(rows):
        raise ValueError(f'La exportación tiene {count} comprobantes y la consulta {len(rows)}. No se muestran faltantes con una exportación incompleta; volvé a comparar.')
    return count


def export(directory, start, end, company, progress, cancelled):
    from playwright.sync_api import sync_playwright
    from runtime_paths import config, DATA_DIR
    from chrome_connection import connect_chrome, choose_page, websocket_endpoint
    from cdp_transport import WebSocket
    from zeta_bot import exclusive_lock
    from zeta_session import prepare_session
    from zeta_compras import refresh_list
    from cargar_lote import verify_company
    import os
    directory = Path(directory)
    cfg = config()
    if cfg.get('empresa_rut') != company:
        raise ValueError('La empresa cambió antes de exportar.')
    control = cfg.get('control_duplicados', {})
    if not all(control.get(key) for key in ('numero', 'serie', 'proveedor', 'nombre')):
        raise ValueError('Completá el comprobante de control en Configuración para verificar la empresa en Zeta.')
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
            step('Verificando la empresa…')
            control_rows = refresh_list(page, '01/01/26', date.today().strftime('%d/%m/%y'), control['numero'])
            proof = verify_company(page, control_rows)
            if proof['rut'] != company:
                raise ValueError('Zeta está en otra empresa.')
            step('Aplicando fechas y comprobando todas las páginas…')
            query = refresh_list(page, date.fromisoformat(start).strftime('%d/%m/%y'), date.fromisoformat(end).strftime('%d/%m/%y'), '')
            step('Descargando el Excel de compras…')
            endpoint = os.environ.get('CERECITO_CHROME_ENDPOINT') or cfg.get('chrome', {}).get('endpoint') or 'http://127.0.0.1:9222'
            connection = WebSocket.connect(websocket_endpoint(endpoint))
            connection.sock.settimeout(1)
            try:
                path = download_excel(page, connection, directory / 'descarga', cancelled)
            finally:
                connection.close()
            step('Validando el Excel descargado…')
            count = validate_export(path, query['filas'])
            return {'filename': path.name, 'path': str(path), 'rows': count,
                    'company': company, 'from': start, 'to': end, 'proof': proof}
