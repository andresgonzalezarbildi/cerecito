"""Abre Catálogos del Cerecito original; conserva la carga en memoria y ediciones."""

from runtime_paths import DATA_DIR, CODE_DIR, APP_DIR, APP_URL, company_rut, control_value, category_provider_rut
import json
from playwright.sync_api import sync_playwright
from conciliar import BASE
from convertir_cerecito import local_page


def open_catalogs(page):
    if not page.evaluate("typeof commitCatalogs === 'function' && typeof CATALOG_EDITOR_VERSION !== 'undefined' && CATALOG_EDITOR_VERSION === 3 && typeof RELATION_EDITOR_VERSION !== 'undefined' && RELATION_EDITOR_VERSION === 4 && typeof invoiceNextSteps === 'function' && invoiceNextSteps.toString().includes('const unsupported=') && typeof invoiceLineEditor === 'function' && typeof syncVoucherForDestination === 'function' && typeof refreshBotReport === 'function' && !!document.getElementById('catalogsPage')"):
        snapshot = page.evaluate('''() => {
            const inputs=[...document.querySelectorAll('#relationsPage input:not([type=file]),#relationsPage select')].map(e=>({
                id:e.id,provider:e.closest('[data-provider-rut]')?.dataset.providerRut,
                rule:e.closest('[data-rule-key]')?.dataset.ruleKey,
                invoice:e.closest('[data-invoice-key]')?.dataset.invoiceKey,line:e.closest('[data-invoice-line]')?.dataset.invoiceLine,
                field:e.dataset.providerField||e.dataset.ruleField||e.dataset.invoiceField||e.dataset.invoiceLineField,value:e.value
            }));
            const saved=Object.fromEntries([STORE_RULES,STORE_REL,STORE_INV].map(key=>[key,localStorage.getItem(key)]));
            const catalogInputs=[...document.querySelectorAll('#catalogAddForm input')].map(e=>({name:e.name,value:e.value,checked:e.checked}));
            return {invoices:state.invoices,duplicates:state.duplicates,filter:state.filter,search:state.search,
                providerLimit:providerRelationLimit,lineLimit:lineRelationLimit,pending:relationPendingOnly,inputs,saved,
                catalogType:document.getElementById('catalogType')?.value,catalogSearch:document.getElementById('catalogSearch')?.value,
                catalogPreview:typeof catalogPreview==='undefined'?null:catalogPreview,
                catalogName:document.getElementById('catalogPreviewSummary')?.textContent.split(':')[0],catalogInputs,
                section:document.querySelector('[data-relation-tab].active')?.dataset.relationTab,
                pages:typeof providerRelationPage==='undefined'?null:{provider:providerRelationPage,line:lineRelationPage,invoice:invoiceRelationPage,providerPending:providerPendingOnly},
                invoiceSearch:document.getElementById('invoiceRelSearch')?.value,invoiceFilter:document.getElementById('invoiceRelFilter')?.value};
        }''')
        page.reload(wait_until='load')
        page.wait_for_function("typeof commitCatalogs === 'function' && typeof CATALOG_EDITOR_VERSION !== 'undefined' && CATALOG_EDITOR_VERSION===3 && typeof RELATION_EDITOR_VERSION !== 'undefined' && RELATION_EDITOR_VERSION===4 && document.getElementById('catalogsPage')", timeout=15000, polling=100)
        page.evaluate('''saved => {
            for(const [key,value] of Object.entries(saved.saved))
                if(localStorage.getItem(key)!==value)throw Error('Las correlaciones cambiaron al actualizar Cerecito. Revisar antes de continuar.');
            Object.assign(state,{invoices:saved.invoices,duplicates:saved.duplicates,filter:saved.filter,search:saved.search});
            providerRelationLimit=saved.providerLimit;lineRelationLimit=saved.lineLimit;relationPendingOnly=saved.pending;
            if(saved.pages){providerRelationPage=saved.pages.provider;lineRelationPage=saved.pages.line;invoiceRelationPage=saved.pages.invoice;providerPendingOnly=saved.pages.providerPending;}
            if(saved.invoiceSearch!==undefined)document.getElementById('invoiceRelSearch').value=saved.invoiceSearch;
            if(saved.invoiceFilter)document.getElementById('invoiceRelFilter').value=saved.invoiceFilter;
            if(saved.section)showRelationSection(saved.section);
            // Recuperar filtros antes de dibujar las filas que estaba editando.
            for(const item of saved.inputs)if(item.id){const e=document.getElementById(item.id);if(e)e.value=item.value;}
            render();renderRelations();
            for(const item of saved.inputs){
                let e=item.id ? document.getElementById(item.id) : null;
                if(!e && item.provider && item.field)e=document.querySelector(`[data-provider-rut="${CSS.escape(item.provider)}"] [data-provider-field="${CSS.escape(item.field)}"]`);
                if(!e && item.rule && item.field)e=document.querySelector(`[data-rule-key="${CSS.escape(item.rule)}"] [data-rule-field="${CSS.escape(item.field)}"]`);
                if(!e && item.invoice && item.field){
                    const row=document.querySelector(`[data-invoice-key="${CSS.escape(item.invoice)}"]`);
                    e=item.line!==undefined ? row?.querySelector(`[data-invoice-line="${CSS.escape(item.line)}"] [data-invoice-line-field="${CSS.escape(item.field)}"]`) : row?.querySelector(`[data-invoice-field="${CSS.escape(item.field)}"]`);
                }
                if(e)e.value=item.value;
            }
            if(saved.catalogType)document.getElementById('catalogType').value=saved.catalogType;
            if(saved.catalogSearch!==undefined)document.getElementById('catalogSearch').value=saved.catalogSearch;
            for(const item of saved.catalogInputs){const e=document.getElementById('catalogAddForm').elements.namedItem(item.name);if(e){e.value=item.value;e.checked=item.checked;}}
            renderCatalogTable();
            if(saved.catalogPreview)showCatalogPreview(saved.catalogPreview,saved.catalogName||'Archivo pendiente');
        }''', snapshot)
    page.bring_to_front()
    page.evaluate("document.querySelector('[data-tab=\"catalogs\"]').click()")
    page.wait_for_function("document.getElementById('catalogsPage').classList.contains('active')", timeout=10000, polling=100)


def main():
    config=json.loads((BASE/'configuracion_zeta.json').read_text(encoding='utf-8'))
    with sync_playwright() as pw:
        page,bridge=local_page(pw,config['chrome']['endpoint'])
        try:
            open_catalogs(page)
            print('Catálogos abiertos. Importar maestro de Zeta, revisar y pulsar Guardar catálogo.')
        finally:bridge.close()
    return 0


if __name__=='__main__':
    try:raise SystemExit(main())
    except Exception as error:
        print('No se pudo abrir Catálogos: '+str(error).splitlines()[0][:300])
        raise SystemExit(1)





