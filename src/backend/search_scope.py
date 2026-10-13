"""Duplicados: consultar solo el año de emisión, dentro de los límites permitidos."""

from runtime_paths import DATA_DIR, CODE_DIR, APP_DIR, APP_URL, company_rut, control_value, category_provider_rut
import json
from datetime import date
from pathlib import Path

ALLOWED_YEARS = (2025, 2026, 2027)


def search_years(include_future=True):
    config=json.loads((DATA_DIR/'configuracion_zeta.json').read_text(encoding='utf-8'))
    years=config.get('anios_busqueda_duplicados',list(ALLOWED_YEARS))
    if not isinstance(years,list) or not years or any(type(y) is not int or y not in ALLOWED_YEARS for y in years):
        raise ValueError('La búsqueda solo permite años 2025, 2026 y 2027.')
    return sorted(set(y for y in years if include_future or y<=date.today().year))


def invoice_search_period(issued, *, today=None):
    """Rango anual de una factura; nunca ampliar la consulta a otros años."""
    today = today or date.today()
    issued = date.fromisoformat(issued)
    if issued > today:
        raise ValueError('Fecha futura: no se demuestra ausencia.')
    if issued.year not in search_years():
        raise ValueError('Año de factura fuera de las búsquedas configuradas: revisar su histórico.')
    start = f'01/01/{issued.year % 100:02d}'
    end = today.strftime('%d/%m/%y') if issued.year == today.year else f'31/12/{issued.year % 100:02d}'
    return start, end
