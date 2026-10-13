"""Ejecutor local y determinista para Zeta. Sin IA, sin API de Zeta.

Configuración explícita de selectores y acciones. Falla cerrado ante incertidumbre.
Un comprobante puede quedar 'INCIERTO' si una operación fue interrumpida:
nunca se reintenta automáticamente sin conciliación humana.
"""
from __future__ import annotations

from runtime_paths import DATA_DIR, CODE_DIR, APP_DIR, APP_URL, company_rut, control_value, category_provider_rut

import argparse
from contextlib import contextmanager
import json
import hashlib
import os
import re
import sys
import time
from dataclasses import dataclass
from datetime import datetime, date
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from pathlib import Path

from conciliar import BASE, already_registered, invoice_id, one_file, read_excel, evaluate
from conciliar import amount
from chrome_connection import connect_chrome, choose_page, locator_for, ChromeError
from lookup_actions import select_supplier, select_concept, validate_lookup_spec, LookupError
from process_locks import process_lock, ProcessLockError

MONEY = Decimal('0.01')
TEMPLATE_RE = re.compile(r'\{\{([a-zA-Z0-9_.]+)\}\}')
STEPS = {'click', 'fill', 'select_label', 'select_value', 'press', 'wait_visible',
         'assert_visible', 'assert_absent', 'assert_value', 'assert_text', 'assert_money', 'choose_text', 'lookup_supplier', 'lookup_concept'}
REQUIRED_PHASES = ('abrir_lista', 'comprobar_duplicado', 'abrir_nuevo', 'encabezado',
                   'linea', 'verificar_pre_guardado', 'guardar', 'verificar_guardado')

class BotError(RuntimeError):
    pass


@contextmanager
def exclusive_lock(path):
    """Una ejecución por recurso; recuperar solo bloqueos sin dueño activo."""
    try:
        with process_lock(path) as recovery:
            yield recovery
    except ProcessLockError as error:
        raise BotError(str(error)) from error


def json_load(path):
    return json.loads(path.read_text(encoding='utf-8-sig'))


def decimal_value(value):
    try:
        d = Decimal(str(value))
        if not d.is_finite():
            raise ValueError('no finito')
        return d
    except (InvalidOperation, ValueError, TypeError):
        raise BotError(f'Importe inválido: {value!r}')


def monetary(value):
    return decimal_value(value).quantize(MONEY, rounding=ROUND_HALF_UP)


def stamp():
    return datetime.now().isoformat(timespec='seconds')


def save_state(path, state):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix('.tmp')
    with tmp.open('w', encoding='utf-8') as f:
        json.dump(state, f, ensure_ascii=False, indent=2)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def append_ledger(path, record):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('a', encoding='utf-8') as f:
        f.write(json.dumps(record, ensure_ascii=False) + '\n')
        f.flush()
        os.fsync(f.fileno())


def unresolved_attempts(path):
    if not path.exists():
        return set()
    last = {}
    for line in path.read_text(encoding='utf-8').splitlines():
        if line.strip():
            event = json.loads(line)
            last[event['clave']] = event['estado']
    return {k for k, s in last.items() if s in {'INCIERTO', 'INICIANDO_GUARDADO', 'PREPARANDO', 'PREPARADA_SIN_GUARDAR'}}


def config_for_invoice(config, invoice):
    name = invoice.get('zeta', {}).get('comprobante', '')
    form = config.get('formularios', {}).get(name)
    if not isinstance(form, dict) or not form.get('validado'):
        raise BotError(f'Formulario {name!r} sin captura y validación específica. No se usa otro formulario como sustituto.')
    result = dict(config)
    result['flujos'] = form.get('flujos', {})
    result['formulario_actual'] = form
    return result


def prepare_context(invoice, linea=None, iva_label=None):
    p, c, z = invoice['proveedor'], invoice['cfe'], invoice['zeta']
    try:
        fecha = date.fromisoformat(c['fecha'])
    except (TypeError, ValueError):
        raise BotError('Fecha de CFE inválida')
    result = {
        'proveedor': {'codigoZeta': str(p['codigoZeta']), 'nombre': str(p['nombre']), 'rut': str(p.get('rut', ''))},
        'cfe': {'serie': str(c['serie']), 'numero': str(c['numero']),
                'fecha': fecha.isoformat(), 'fecha_dmy': fecha.strftime('%d/%m/%Y'),
                'fecha_dmy_corta': fecha.strftime('%d/%m/%y'),
                'moneda': str(c['moneda']), 'total': str(monetary(c['total'])),
                'tipo': str(c['tipo'])},
        'zeta': {'comprobante': str(z.get('comprobante') or ''),
                 'centroCosto': str(z.get('centroCosto') or ''),
                 'referencia': str(z.get('referencia') or ''),
                 'formaPago': str(z.get('formaPago') or ''),
                 'modalidad': str(z.get('modalidad') or '')},
        'linea': {}, 'iva': {'etiqueta': iva_label or ''}
    }
    if linea is not None:
        result['linea'] = {'codigoZeta': str(linea['codigoZeta']),
                           'descripcionZeta': str(linea.get('descripcionZeta') or ''),
                           'monto': str(monetary(linea['monto'])),
                           'indicadorIVA': str(linea['indicadorIVA']),
                           'cantidad': '1'}
    return result


def interpolate(template, context):
    if not isinstance(template, str):
        raise BotError('El valor/selector debe ser texto')
    def replacement(match):
        ref = context
        for piece in match.group(1).split('.'):
            if not isinstance(ref, dict) or piece not in ref:
                raise BotError(f'Variable desconocida: {match.group(0)}')
            ref = ref[piece]
        if ref is None or isinstance(ref, (list, dict)):
            raise BotError(f'Variable no imprimible: {match.group(0)}')
        return str(ref)
    return TEMPLATE_RE.sub(replacement, template)


def preflight_invoice(inv, config):
    """Valida sin asumir códigos fiscales que no se configuren expresamente."""
    c, z, p = inv['cfe'], inv['zeta'], inv['proveedor']
    issues = []
    if inv.get('excluido') or not inv.get('completo') or inv.get('estado') not in {'auto', 'reviewed'}:
        issues.append('Cerecito no marcó el comprobante como apto')
    for label, value in [('proveedor', p.get('codigoZeta')), ('comprobante', z.get('comprobante')),
                         ('centro de costo', z.get('centroCosto')), ('serie', c.get('serie')),
                         ('número', c.get('numero')), ('fecha', c.get('fecha')), ('moneda', c.get('moneda'))]:
        if not str(value or '').strip():
            issues.append(f'Falta {label}')
    if 'Contado' in str(z.get('comprobante', '')) and not z.get('formaPago'):
        issues.append('Contado sin forma de pago')
    if str(c.get('tipo')) in ('112', '122'):
        form = config.get('formulario_actual', {})
        if not (form.get('nota_credito_validada') and z.get('referencia')):
            issues.append('Nota de crédito requiere flujo, signo y referencia específicos validados')
    if not inv.get('lineas'):
        issues.append('Sin líneas')
    destinations = {ln.get('tipoDestino') for ln in inv.get('lineas', [])}
    if len(destinations) != 1 or not destinations <= {'article', 'concept'}:
        issues.append('Destino mixto o desconocido: requiere clasificación manual')
    if z.get('tipoDestino') and destinations != {z['tipoDestino']}:
        issues.append('El tipo de destino de líneas no coincide con la cabecera')
    vat_map = config.get('iva', {})
    estimated = Decimal(0)
    for n, ln in enumerate(inv.get('lineas', []), 1):
        if not ln.get('codigoZeta') or not ln.get('tipoDestino'):
            issues.append(f'Línea {n}: sin correlación Zeta')
        if ln.get('mezclaIVA'):
            issues.append(f'Línea {n}: mezcla IVA')
        indicator = str(ln.get('indicadorIVA', ''))
        rule = vat_map.get(indicator)
        if not isinstance(rule, dict) or 'etiqueta' not in rule or 'tasa' not in rule:
            issues.append(f'Línea {n}: IVA {indicator!r} sin equivalencia y tasa configurada')
            continue
        if not str(rule['etiqueta']).strip() or 'CONFIGURAR' in str(rule['etiqueta']):
            issues.append(f'Línea {n}: equivalencia IVA pendiente de verificar en Zeta')
        try:
            net = monetary(ln.get('monto'))
            rate = decimal_value(rule['tasa'])
            if net < 0 or not Decimal(0) <= rate <= Decimal(1):
                raise BotError('Monto/tasa negativa o inválida')
            estimated += (net * (Decimal(1) + rate)).quantize(MONEY, rounding=ROUND_HALF_UP)
        except BotError as exc:
            issues.append(f'Línea {n}: {exc}')
    try:
        gross = monetary(c.get('total'))
        if gross <= 0:
            issues.append('Total XML 0 o negativo')
        if all(str(x.get('indicadorIVA', '')) in vat_map for x in inv.get('lineas', [])):
            if abs(estimated - gross) > monetary(config.get('tolerancia_total', '0.02')):
                issues.append(f'Desajuste total: líneas+IVA={estimated} vs CFE={gross}')
    except BotError as exc:
        issues.append(str(exc))
    # Validar por categoría contra los totales fiscales exportados del XML,
    # no solamente que la suma bruta dé igual.
    fiscal_fields = ('netoMin', 'netoBas', 'exento', 'ivaMin', 'ivaBas')
    if any(field in c for field in fiscal_fields):
        if any(c.get(field) is None for field in fiscal_fields):
            issues.append('XML sin desglose fiscal completo')
        else:
            try:
                tolerance = monetary(config.get('tolerancia_total', '0.02'))
                if tolerance < 0 or tolerance > Decimal('0.05'):
                    raise BotError('Tolerancia fiscal fuera del límite 0 a 0.05')
                for indicator, net_field, tax_field in [('1', 'exento', None), ('2', 'netoMin', 'ivaMin'), ('3', 'netoBas', 'ivaBas')]:
                    net = sum((monetary(ln['monto']) for ln in inv.get('lineas', []) if str(ln.get('indicadorIVA')) == indicator), Decimal(0))
                    if abs(net - monetary(c[net_field])) > tolerance:
                        issues.append(f'Desajuste XML {net_field}: líneas={net} XML={c[net_field]}')
                    if tax_field and indicator in vat_map:
                        tax = monetary(net * decimal_value(vat_map[indicator]['tasa']))
                        if abs(tax - monetary(c[tax_field])) > tolerance:
                            issues.append(f'Desajuste IVA XML {tax_field}')
                xml_total = sum((monetary(c[field]) for field in fiscal_fields), Decimal(0))
                if abs(xml_total - monetary(c['total'])) > tolerance:
                    issues.append('Total XML distinto de bases más IVA; requiere revisar retenciones/ajustes')
            except BotError as exc:
                issues.append(str(exc))
    return issues


def validate_config(config, saving):
    if config.get('version') != 1:
        raise BotError('Configuración de versión desconocida')
    if config.get('formulario_actual', {}).get('cabecera_requiere_guardado') and not saving:
        raise BotError('Zeta guarda la cabecera antes de abrir el detalle: la prueba completa sin guardar está bloqueada. Usá una empresa de pruebas o captura en solo lectura.')
    if config.get('formulario_actual', {}).get('cabecera_requiere_guardado') and saving:
        raise BotError('Flujo con guardado de cabecera aún no implementado/validado para carga real. No usar el flujo genérico que guarda solamente al final.')
    if not config.get('url') or not str(config['url']).startswith('https://'):
        raise BotError('Indicá la URL HTTPS real de Zeta en configuracion_zeta.json')
    flows = config.get('flujos', {})
    phases = REQUIRED_PHASES if saving else ('abrir_lista', 'comprobar_duplicado', 'abrir_nuevo', 'encabezado', 'linea', 'verificar_pre_guardado')
    for phase in phases:
        if not flows.get(phase):
            raise BotError(f'Falta configurar el flujo {phase!r}')
    for phase in phases:
        steps = flows.get(phase, [])
        if not isinstance(steps, list):
            raise BotError(f'Flujo {phase} debe ser una lista')
        for step in steps:
            if step.get('accion') not in STEPS:
                raise BotError(f'Acción inválida en {phase}: {step.get("accion")}')
            if step.get('accion') in {'lookup_supplier', 'lookup_concept'}:
                try:
                    validate_lookup_spec(step)
                except LookupError as exc:
                    raise BotError(str(exc)) from exc
            if not step.get('selector') or 'CONFIGURAR' in step['selector']:
                raise BotError(f'Selector sin configurar en {phase}')
            if not isinstance(step.get('frames', []), list) or any(not isinstance(x, str) or not x or 'CONFIGURAR' in x for x in step.get('frames', [])):
                raise BotError(f'Cadena de iframes inválida en {phase}')
            if phase not in {'guardar', 'verificar_guardado'} and step.get('accion') == 'press' and step.get('tecla', 'Enter').lower() == 'enter':
                raise BotError('Enter puede enviar el formulario: configurá un selector explícito del resultado de búsqueda')
            if phase not in {'guardar', 'verificar_guardado'} and step.get('accion') in {'click', 'choose_text'} and step.get('seguro_sin_guardado') is not True:
                raise BotError(f'Click sin verificar como seguro antes de guardar en {phase}')
            if step['accion'] in ('fill', 'select_label', 'select_value', 'choose_text', 'assert_value', 'assert_text', 'assert_money') and 'valor' not in step:
                raise BotError(f'Acción {step["accion"]} sin valor en {phase}')
    if saving:
        if not config.get('historico_cobertura_verificada'):
            raise BotError('El histórico de Zeta no tiene cobertura verificada para guardar. El Excel parcial no prueba ausencia.')
        if not any(s['accion'] == 'assert_money' and s.get('valor') == '{{cfe.total}}' for s in flows['verificar_pre_guardado']):
            raise BotError('Falta validación monetaria exacta del total antes de guardar')
    if saving:
        for field in ('{{cfe.numero}}', '{{cfe.serie}}', '{{proveedor.codigoZeta}}', '{{zeta.comprobante}}'):
            if not any(field in s.get('selector', '') for s in flows['comprobar_duplicado'] if s['accion'] == 'assert_absent'):
                raise BotError('La búsqueda de duplicados debe identificar serie, número, proveedor y tipo en una misma fila de Zeta')
        if not config.get('habilitar_guardado'):
            raise BotError('El guardado está DESHABILITADO en la configuración')
        if not any(s['accion'] == 'assert_absent' for s in flows['comprobar_duplicado']):
            raise BotError('Falta comprobación activa de ausencia de duplicado en Zeta')
        if not any(s['accion'] in {'assert_visible', 'assert_text'} for s in flows['verificar_guardado']):
            raise BotError('Falta verificar el resultado del guardado en Zeta')


def execute_steps(page, steps, context):
    for step in steps:
        act = step['accion']
        css = interpolate(step['selector'], context)
        if 'CONFIGURAR' in css:
            raise BotError('Selector pendiente de configurar')
        locator = locator_for(page, dict(step, selector=css))
        val = interpolate(step.get('valor', ''), context)
        if act not in {'wait_visible', 'assert_absent'} and locator.count() != 1:
            raise BotError(f'Control ambiguo o inexistente: {css}')
        if act == 'click':
            locator.click(timeout=10000)
        elif act == 'fill':
            locator.fill(val, timeout=10000)
            if locator.input_value(timeout=10000) != val:
                raise BotError(f'Zeta truncó o cambió el valor de {css}; no se continúa')
        elif act == 'lookup_supplier':
            try:
                select_supplier(page, step, context['proveedor'])
            except LookupError as exc:
                raise BotError(str(exc)) from exc
        elif act == 'lookup_concept':
            try:
                select_concept(page, step, context['linea'])
            except LookupError as exc:
                raise BotError(str(exc)) from exc
        elif act == 'press':
            locator.press(step.get('tecla', 'Enter'), timeout=10000)
        elif act == 'select_label':
            locator.select_option(label=val, timeout=10000)
        elif act == 'select_value':
            locator.select_option(value=val, timeout=10000)
        elif act == 'choose_text':
            locator.get_by_text(val, exact=True).click(timeout=10000)
        elif act in ('wait_visible', 'assert_visible'):
            locator.wait_for(state='visible', timeout=10000)
        elif act == 'assert_absent':
            # Se valida después de esperar la condición de búsqueda mediante wait_visible.
            if locator.count() != 0:
                raise BotError(f'Zeta muestra coincidencia: {css}')
        elif act == 'assert_value':
            actual = locator.input_value(timeout=10000)
            if actual != val:
                raise BotError(f'Campo distinto: esperado {val!r}, Zeta muestra {actual!r}')
        elif act == 'assert_text':
            actual = locator.inner_text(timeout=10000)
            if actual.strip() != val.strip():
                raise BotError(f'Validación Zeta distinta de {val!r}')
        elif act == 'assert_money':
            actual = locator.input_value(timeout=10000) if step.get('leer_input') else locator.inner_text(timeout=10000)
            actual_decimal = amount(actual)
            if actual_decimal is None or actual_decimal != monetary(val):
                raise BotError(f'Total Zeta {actual!r} distinto del XML {val!r}')


def screenshots(page, base, key, tag):
    folder = base / 'salida' / 'capturas_bot'
    folder.mkdir(parents=True, exist_ok=True)
    safe = re.sub('[^A-Za-z0-9_-]', '_', key)
    try:
        page.screenshot(path=str(folder / f'{safe}_{tag}.png'), full_page=True)
    except Exception:
        pass


def load_and_recheck(base):
    # Obligatorio leer de nuevo el Excel antes de procesar: no confiar en colas viejas.
    datafile = one_file(base / 'entrada' / 'cerecito', '.json')
    xlsx = one_file(base / 'entrada' / 'zeta', '.xlsx')
    docs = json_load(datafile)
    queue_path = base / 'salida' / 'cola.json'
    if not queue_path.exists():
        raise BotError('Falta cola.json: ejecutá procesar.bat')
    queue = json_load(queue_path)
    if queue.get('formato') != 'cerecito-cola-v1':
        raise BotError('Formato de cola no reconocido')
    if queue.get('origenZeta') != xlsx.name:
        raise BotError('Cambió el Excel de Zeta: ejecutá procesar.bat otra vez')
    if queue.get('sha256Zeta') != hashlib.sha256(xlsx.read_bytes()).hexdigest() or queue.get('sha256Cerecito') != hashlib.sha256(datafile.read_bytes()).hexdigest():
        raise BotError('Cambió el contenido del JSON/Excel o la cola es antigua: ejecutá procesar.bat')
    if not docs.get('comprobantes'):
        raise BotError('JSON Cerecito sin facturas')
    excelrows, sheets = read_excel(xlsx)
    statuses = {r['clave']: r['estado'] for r in evaluate(docs['comprobantes'], excelrows,
               already_registered(base / 'estado' / 'confirmados.jsonl'), sheets)}
    raw = {invoice_id(i): i for i in docs['comprobantes']}
    queue_keys = [x['clave'] for x in queue['facturas']]
    if len(queue_keys) != len(set(queue_keys)) or any(k not in raw or statuses.get(k) != 'PENDIENTE' for k in queue_keys):
        raise BotError('La cola ya no coincide con la conciliación; ejecutá procesar.bat')
    return [raw[k] for k in queue_keys]


def print_preflight(base, config):
    docs = load_and_recheck(base)
    print(f'Facturas en cola: {len(docs)}')
    for item in docs:
        try:
            specific = config_for_invoice(config, item)
            issues = preflight_invoice(item, specific)
        except BotError as exc:
            issues = [str(exc)]
        print(f'  {invoice_id(item)}: ' + ('BLOQUEADA: ' + '; '.join(issues) if issues else 'APTA para preparar formulario'))
    return docs


def run(base, cfg_path, *, guardar=False, limitar=1):
    config = json_load(cfg_path)
    docs = print_preflight(base, config)
    if not docs:
        print('No hay facturas pendientes.'); return 0
    if not config.get('url') or 'CONFIGURAR' in str(config['url']):
        raise BotError('Configurá URL y selectores de Zeta; el informe previo está listo')
    if guardar:
        excel = one_file(base / 'entrada' / 'zeta', '.xlsx')
        max_hours = int(config.get('excel_max_age_horas', 24))
        if max_hours < 1 or time.time() - excel.stat().st_mtime > max_hours * 3600:
            raise BotError(f'El XLSX de Zeta tiene más de {max_hours} horas: descargá uno actualizado')
    ambiguous = unresolved_attempts(base / 'estado' / 'intentos.jsonl')
    eligible = []
    for item in docs:
        try:
            specific = config_for_invoice(config, item)
            validate_config(specific, saving=guardar)
            if not preflight_invoice(item, specific) and invoice_id(item) not in ambiguous:
                eligible.append((item, specific))
        except BotError as exc:
            print('BLOQUEADA:', invoice_id(item), exc)
    if not eligible:
        raise BotError('Ninguna factura apta para abrir; revisar bloqueadas o estados INCIERTO')
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        browser = connect_chrome(p, config)
        page = choose_page(browser, config)
        try:
            for item, specific in eligible[:limitar]:
                config = specific
                key = invoice_id(item)
                info = prepare_context(item)
                print('Procesando:', key)
                try:
                    append_ledger(base / 'estado' / 'intentos.jsonl', {'clave': key, 'estado': 'PREPARANDO', 'fecha': stamp()})
                    execute_steps(page, config['flujos']['abrir_lista'], info)
                    execute_steps(page, config['flujos']['comprobar_duplicado'], info)
                    execute_steps(page, config['flujos']['abrir_nuevo'], info)
                    execute_steps(page, config['flujos']['encabezado'], info)
                    dest = item['lineas'][0]['tipoDestino']
                    line_steps = config['flujos'].get('linea_' + dest, config['flujos']['linea'])
                    for ln in item['lineas']:
                        iva_rule = config['iva'][str(ln['indicadorIVA'])]
                        line_ctx = prepare_context(item, ln, iva_rule['etiqueta'])
                        execute_steps(page, line_steps, line_ctx)
                    if config['flujos'].get('verificar_pre_guardado'):
                        execute_steps(page, config['flujos']['verificar_pre_guardado'], info)
                    screenshots(page, base, key, 'antes_guardar')
                    if not guardar:
                        append_ledger(base / 'estado' / 'intentos.jsonl', {'clave': key, 'estado': 'PREPARADA_SIN_GUARDAR', 'fecha': stamp()})
                        print('MODO PRUEBA: formulario preparado, NO SE GUARDA.'); break
                    # Desde este momento, una interrupción bloquea nuevos intentos.
                    append_ledger(base / 'estado' / 'intentos.jsonl', {'clave': key, 'estado': 'INICIANDO_GUARDADO', 'fecha': stamp()})
                    execute_steps(page, config['flujos']['guardar'], info)
                    execute_steps(page, config['flujos']['verificar_guardado'], info)
                    append_ledger(base / 'estado' / 'confirmados.jsonl', {'clave': key, 'estado': 'confirmado', 'fecha': stamp(), 'origen': 'zeta_bot'})
                    append_ledger(base / 'estado' / 'intentos.jsonl', {'clave': key, 'estado': 'CONFIRMADO', 'fecha': stamp()})
                    print('Confirmado en Zeta:', key)
                except Exception as exc:
                    append_ledger(base / 'estado' / 'intentos.jsonl', {'clave': key, 'estado': 'INCIERTO', 'fecha': stamp()})
                    screenshots(page, base, key, 'error')
                    print(f'DETENIDO en {key}: {exc}', file=sys.stderr)
                    print('No se procesarán más comprobantes. Revisá Zeta y los registros.', file=sys.stderr)
                    return 2
        finally:
            print('Transporte desconectado al salir. Chrome y el formulario permanecen abiertos.')
    return 0


def main():
    parser = argparse.ArgumentParser(description='Bot local Zeta, sin IA')
    parser.add_argument('--base', type=Path, default=BASE)
    parser.add_argument('--config', type=Path)
    parser.add_argument('--solo-validar', action='store_true')
    parser.add_argument('--guardar', action='store_true', help='Permitir el flujo de guardado, tras configurarlo')
    parser.add_argument('--limite', type=int, default=1, help='Máximo de comprobantes; defecto 1')
    args = parser.parse_args()
    if args.limite < 1:
        parser.error('--limite debe ser positivo')
    cfg = args.config or args.base / 'configuracion_zeta.json'
    try:
        if args.solo_validar:
            print_preflight(args.base, json_load(cfg)); return 0
        with exclusive_lock(args.base / 'estado' / 'bot.lock'):
            return run(args.base, cfg, guardar=args.guardar, limitar=args.limite)
    except (BotError, ChromeError, OSError, ValueError, json.JSONDecodeError) as exc:
        print('BLOQUEADO:', exc, file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
