"""Selección determinista de proveedor en el buscador real de Zeta."""

from runtime_paths import DATA_DIR, CODE_DIR, APP_DIR, APP_URL, company_rut, control_value, category_provider_rut
import re

from chrome_connection import locator_for


class LookupError(RuntimeError):
    pass


def supplier_row(root, spec, supplier):
    for field in ('codigoZeta', 'nombre', 'rut'):
        if not str(supplier.get(field, '')).strip():
            raise LookupError(f'Proveedor sin {field}: no se puede elegir una fila.')
    if not re.fullmatch(r'\d{12}', str(supplier['rut'])):
        raise LookupError('RUT del proveedor inválido para la búsqueda exacta.')
    row = root.locator(spec['fila'])
    for field in ('codigoZeta', 'rut', 'nombre'):
        row = row.filter(has=root.get_by_text(str(supplier[field]), exact=True))
    if row.count() != 1:
        raise LookupError('El buscador no muestra una única fila con el código, RUT y nombre correlacionados.')
    return row


def select_supplier(page, step, supplier):
    opener = locator_for(page, step)
    if opener.count() != 1:
        raise LookupError('Buscador de proveedor ambiguo.')
    opener.click(timeout=10000)
    spec = step['buscador']
    root = page
    for iframe in spec['frames']:
        handle = root.locator(iframe).element_handle(timeout=10000)
        loaded_frame = handle.content_frame() if handle else None
        if loaded_frame is None:
            raise LookupError('El iframe del buscador todavía no está disponible.')
        loaded_frame.wait_for_load_state('load', timeout=10000)
        root = root.frame_locator(iframe)
    search = root.locator(spec['filtro'])
    search.fill(supplier['nombre'], timeout=10000)
    if search.input_value() != supplier['nombre']:
        raise LookupError('Zeta truncó el texto del buscador.')
    search.press('Tab', timeout=10000)
    # La fila exacta debe aparecer antes de evaluarla. Nunca elegir el primer resultado.
    root.get_by_text(supplier['rut'], exact=True).wait_for(state='visible', timeout=10000)
    row = supplier_row(root, spec, supplier)
    select = row.locator(spec['seleccionar'])
    if select.count() != 1:
        raise LookupError('El control de selección del proveedor es ambiguo.')
    select.click(timeout=10000)
    locator_for(page, {'selector': spec['frames'][-1], 'frames': spec['frames'][:-1]}).wait_for(state='hidden', timeout=10000)
    readback = locator_for(page, step['lectura'])
    actual = readback.input_value(timeout=10000)
    expected = f"{supplier['codigoZeta']} - {supplier['nombre']}"
    if actual != expected:
        raise LookupError(f'Proveedor seleccionado distinto: {actual!r}. No se continúa.')


def validate_lookup_spec(step):
    if step.get('seguro_sin_guardado') is not True:
        raise LookupError('Buscador sin verificación de que no guarda.')
    spec = step.get('buscador', {})
    if not isinstance(spec.get('frames'), list) or not spec['frames']:
        raise LookupError('Falta la cadena de iframes del buscador.')
    selectors = [spec.get('filtro'), spec.get('fila'), spec.get('seleccionar'), step.get('lectura', {}).get('selector')] + spec['frames']
    if any(not isinstance(x, str) or not x.strip() or 'CONFIGURAR' in x for x in selectors):
        raise LookupError('Faltan selectores verificados del buscador.')


def concept_row(root, spec, line):
    for field in ('codigoZeta', 'descripcionZeta'):
        if not str(line.get(field, '')).strip():
            raise LookupError('Concepto sin código o descripción correlacionados.')
    row = root.locator(spec['fila'])
    for field in ('codigoZeta', 'descripcionZeta'):
        row = row.filter(has=root.get_by_text(str(line[field]), exact=True))
    if row.count() != 1:
        raise LookupError('Concepto ausente o ambiguo; no se selecciona.')
    return row


def select_concept(page, step, line):
    validate_lookup_spec(step)
    for field in ('codigoZeta', 'descripcionZeta'):
        if not str(line.get(field, '')).strip():
            raise LookupError('Falta la correlación del concepto.')
    opener = locator_for(page, step)
    if opener.count() != 1:
        raise LookupError('Buscador de conceptos ambiguo.')
    opener.click(timeout=10000)
    spec = step['buscador']
    root = page
    for selector in spec['frames']:
        handle = root.locator(selector).element_handle(timeout=10000)
        frame = handle.content_frame() if handle else None
        if frame is None:
            raise LookupError('Iframe de conceptos no disponible.')
        frame.wait_for_load_state('load', timeout=10000)
        root = root.frame_locator(selector)
    for field in ('codigoZeta', 'descripcionZeta'):
        if not str(line.get(field, '')).strip():
            raise LookupError('Falta la correlación del concepto.')
    search = root.locator(spec['filtro'])
    search.fill(line['descripcionZeta'], timeout=10000)
    if search.input_value() != line['descripcionZeta']:
        raise LookupError('Texto del concepto truncado.')
    search.press('Tab', timeout=10000)
    root.get_by_text(line['descripcionZeta'], exact=True).wait_for(state='visible', timeout=10000)
    row = concept_row(root, spec, line)
    button = row.locator(spec['seleccionar'])
    if button.count() != 1:
        raise LookupError('Selección del concepto ambigua.')
    button.click(timeout=10000)
    locator_for(page, {'selector': spec['frames'][-1], 'frames': spec['frames'][:-1]}).wait_for(state='hidden', timeout=10000)
    expected = str(line['codigoZeta']) + ' - ' + line['descripcionZeta']
    if locator_for(page, step['lectura']).input_value() != expected:
        raise LookupError('Concepto seleccionado distinto al correlacionado.')
