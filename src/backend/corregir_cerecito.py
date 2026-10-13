"""Abre la pestaña original de correlaciones en el Chrome del bot."""

from runtime_paths import DATA_DIR, CODE_DIR, APP_DIR, APP_URL, company_rut, control_value, category_provider_rut
import json
from playwright.sync_api import sync_playwright
from convertir_cerecito import local_page
from conciliar import BASE


def open_correlations(page):
    if not page.evaluate("typeof RELATION_EDITOR_VERSION !== 'undefined' && RELATION_EDITOR_VERSION===4 && typeof invoiceNextSteps === 'function' && invoiceNextSteps.toString().includes('const unsupported=') && typeof invoiceLineEditor === 'function' && typeof syncVoucherForDestination === 'function' && typeof refreshBotReport === 'function'"):
        from abrir_catalogos import open_catalogs
        open_catalogs(page)
    page.bring_to_front()
    # Navegación local: no depende de coordenadas ni del estado de la ventana.
    # Si ya está abierta, conservar los campos que el usuario esté editando.
    page.evaluate('''() => {
        const panel=document.getElementById('relationsPage');
        if (!panel) throw Error('No se encuentra el editor de correlaciones');
        if (panel.classList.contains('active')) return;
        const buttons=document.querySelectorAll('button[data-tab="relations"]');
        if (buttons.length!==1 || buttons[0].disabled)
            throw Error('Botón de correlaciones ausente, ambiguo o deshabilitado');
        buttons[0].click();
    }''')
    page.wait_for_function('''() => {
        const panel=document.getElementById('relationsPage');
        const save=document.getElementById('saveCorrelationChanges');
        return panel?.classList.contains('active') && save && !save.disabled;
    }''',timeout=10000,polling=100)


def main():
    config=json.loads((BASE/'configuracion_zeta.json').read_text(encoding='utf-8'))
    with sync_playwright() as pw:
        page,bridge=local_page(pw,config['chrome']['endpoint'])
        try:
            open_correlations(page)
            print('Correlaciones abiertas en Cerecito del bot. Guardar cambios y volver a ejecutar INICIAR_BOT.bat.')
        finally:bridge.close()
    return 0


if __name__=='__main__':
    try:raise SystemExit(main())
    except Exception as error:
        print('No se pudo abrir Correlaciones: '+str(error).splitlines()[0][:300])
        raise SystemExit(1)





