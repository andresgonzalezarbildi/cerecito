"use strict";

// Este proveedor vende productos para distintas áreas. La decisión es por comprobante,
// no por proveedor ni por descripción: nunca se aprende de otro documento.
const PURCHASE_CATEGORIES=DB.purchaseCategories||{};

function requiresPurchaseCategory(cfe) {
  return (DB.purchaseCategoryRuts||[]).includes(String(cfe.rut)) && ["111","112","121","122"].includes(String(cfe.tipo));
}

function purchaseCategoryProfile(saved) {
  return Object.hasOwn(PURCHASE_CATEGORIES,saved.purchaseCategory) ? PURCHASE_CATEGORIES[saved.purchaseCategory] : null;
}

function purchaseCategoryForItem(saved,item) {
  return saved.linePurchaseCategories?.[item.origIndex] ?? saved.purchaseCategory ?? "";
}

function activePurchaseItems(cfe,saved) {
  const excluded=new Set((saved.excludedLines||[]).map(Number));
  return cfe.items.filter(item=>!excluded.has(Number(item.origIndex)));
}

function purchaseCategoriesComplete(cfe,saved) {
  return !requiresPurchaseCategory(cfe) || activePurchaseItems(cfe,saved).every(item=>Object.hasOwn(PURCHASE_CATEGORIES,purchaseCategoryForItem(saved,item)));
}

function invoicePurchaseCategory(cfe,saved) {
  if(!requiresPurchaseCategory(cfe))return "";
  const items=activePurchaseItems(cfe,saved),values=[...new Set(items.map(item=>purchaseCategoryForItem(saved,item)))];
  if(values.length===1)return values[0];
  return values.every(value=>Object.hasOwn(PURCHASE_CATEGORIES,value)) ? "mixed" : "";
}

function purchaseCategoryForRow(cfe,saved,row) {
  const indices=row.sourceIndices||[row.origIndex];
  const values=[...new Set(indices.map(index=>purchaseCategoryForItem(saved,{origIndex:index})))];
  return requiresPurchaseCategory(cfe)&&values.length===1 ? values[0] : "";
}

function purchaseCategoryTarget(cfe, item, saved, base) {
  if(!requiresPurchaseCategory(cfe))return base;
  const value=purchaseCategoryForItem(saved,item);
  const category=Object.hasOwn(PURCHASE_CATEGORIES,value) ? PURCHASE_CATEGORIES[value] : null;
  if(!category)return base;
  const code=category.articles[String(item.ind)]||"", available=!!code&&targetAllowed("article",code);
  return {target:available?{kind:"article",code,desc:catalogDesc("article",code)}:null,
    confidence:available?1:0,count:1,total:1,automatic:available,manual:true,invalid:!available,
    source:available?"Destino de esta factura: "+category.label:
      "Destino "+category.label+": falta artículo disponible para "+invoiceTaxLabel(item.ind)};
}

function purchaseCategoryControl(x) {
  if(!requiresPurchaseCategory(x.cfe))return "";
  const options=Object.entries(PURCHASE_CATEGORIES).map(([key,profile])=>
    `<option value="${key}" ${invoicePurchaseCategory(x.cfe,x.saved)===key?'selected':''}>${esc(profile.label)}</option>`).join("");
  const mixed=invoicePurchaseCategory(x.cfe,x.saved)==='mixed';
  return `<div class="field ${purchaseCategoriesComplete(x.cfe,x.saved)?'':'attn'}"><label>Aplicar a todos los productos de Este proveedor
    <select aria-label="Destino de compra de Este proveedor" data-purchase-category="${esc(x.key)}" onchange="setPurchaseCategory(this.dataset.purchaseCategory,this.value)">
    <option value="">${mixed?'Usos distintos por producto':'Elegir para todos…'}</option>${options}</select></label>
    <div class="small">También podés elegir el uso junto a cada producto.${mixed?' Hay usos distintos: elegí el centro de la factura o dejalo vacío.':''}</div></div>`;
}

function purchaseLineCategoryControl(x,row) {
  if(!requiresPurchaseCategory(x.cfe))return "";
  const value=purchaseCategoryForItem(x.saved,row.item), options=Object.entries(PURCHASE_CATEGORIES).map(([key,profile])=>
    `<option value="${key}" ${value===key?'selected':''}>${esc(profile.label)}</option>`).join('');
  return `<label>Este producto se usa en
    <select aria-label="Uso de este producto de Este proveedor" data-purchase-line="${esc(row.origIndex)}" data-purchase-invoice="${esc(x.key)}" onchange="setLinePurchaseCategory(this.dataset.purchaseInvoice,this.dataset.purchaseLine,this.value)">
    <option value="">Elegir uso del producto…</option>${options}</select></label>`;
}

function purchaseArticleControl(x,row) {
  if(!requiresPurchaseCategory(x.cfe) || row.target?.kind==='concept')return `<input aria-label="Código de destino" list="codeCatalog" data-invoice-line-field="code" value="${esc(row.target?.code||'')}">`;
  const value=purchaseCategoryForItem(x.saved,row.item), selected=Object.hasOwn(PURCHASE_CATEGORIES,value);
  const eligible=new Set(Object.values(PURCHASE_CATEGORIES).flatMap(profile=>[profile.articles[String(row.item.ind)],...(profile.extraArticles?.[String(row.item.ind)]||[])]).filter(Boolean));
  const articles=DB.catalog.filter(item=>item.kind==='article'&&(eligible.has(item.code)||(selected&&row.target?.code===item.code)));
  const options=articles.map(item=>
    `<option value="${esc(item.code)}" ${selected&&row.target?.code===item.code?'selected':''}>${esc(item.code)} · ${esc(item.desc)}</option>`).join('');
  return `<select aria-label="Artículo Zeta para este producto" data-purchase-article="${esc(row.origIndex)}" data-purchase-invoice="${esc(x.key)}" onchange="setPurchaseArticle(this.dataset.purchaseInvoice,this.dataset.purchaseArticle,this.value)">
    <option value="">Elegir artículo para este producto…</option>${options}</select>`;
}

function setPurchaseArticle(key,index,code) {
  const cfe=getInvoiceByKey(key),item=cfe?.items.find(item=>String(item.origIndex)===String(index));
  if(!item || !requiresPurchaseCategory(cfe))return false;
  if(!code)return setLinePurchaseCategory(key,index,"");
  const matches=Object.entries(PURCHASE_CATEGORIES).filter(([,profile])=>profile.articles[String(item.ind)]===code || (profile.extraArticles?.[String(item.ind)]||[]).includes(code));
  if(matches.length!==1 || !targetAllowed('article',code)){toast('Artículo sin correspondencia validada para el IVA de este producto.');render();renderRelations();return false;}
  return setLinePurchaseCategory(key,index,matches[0][0],code);
}

function purchaseLoadArticleControl(x,row) {
  if(row.target?.kind==='concept')return `<input class="codeInput" list="codeCatalog" value="${esc(row.target.code||'')}" data-purchase-invoice="${esc(x.key)}" data-purchase-article="${esc(row.origIndex)}" onchange="setLine(this.dataset.purchaseInvoice,this.dataset.purchaseArticle,this.value,'concept')">`;
  return purchaseArticleControl(x,row);
}

function setLinePurchaseCategory(key,index,value,articleCode=null) {
  const cfe=getInvoiceByKey(key),item=cfe?.items.find(item=>String(item.origIndex)===String(index));
  if(!cfe || !requiresPurchaseCategory(cfe) || !item || (value && !Object.hasOwn(PURCHASE_CATEGORIES,value)))return false;
  if(articleCode){
    const profile=PURCHASE_CATEGORIES[value];
    if(!profile || !targetAllowed('article',articleCode) || !(profile.articles[String(item.ind)]===articleCode || (profile.extraArticles?.[String(item.ind)]||[]).includes(articleCode)))return false;
  }
  const next=JSON.parse(JSON.stringify(invoiceStates)), previous=next[key]||{lines:{},excludedLines:[]};
  const previousSummary=invoicePurchaseCategory(cfe,previous), previousChoice=purchaseCategoryForItem(previous,item);
  const saved=next[key]={...previous,linePurchaseCategories:{...previous.linePurchaseCategories,[index]:value},
    lines:{...previous.lines},reviewed:false};
  delete saved.lines[index];
  if(articleCode)saved.lines[index]={kind:'article',code:articleCode,desc:catalogDesc('article',articleCode)};
  const summary=invoicePurchaseCategory(cfe,saved),selected=activePurchaseItems(cfe,saved).map(item=>purchaseCategoryForItem(saved,item)).filter(Boolean);
  if(summary===previousSummary && value===previousChoice){/* Conservar un centro opcional elegido manualmente. */}
  else if(summary==='mixed' || new Set(selected).size>1)saved.center='';
  else if(selected.length){
    const category=PURCHASE_CATEGORIES[selected[0]];
    if(!isValidCenter(category.center)){toast('Centro no disponible: '+category.center);render();renderRelations();return false;}
    saved.center=category.center;
  }
  if(value)syncVoucherForDestination(next[key],'article');
  if(!storageSet(STORE_INV,next)){render();renderRelations();return false;}
  invoiceStates=next;render();renderRelations();return true;
}

function setPurchaseCategory(key, value) {
  const cfe=getInvoiceByKey(key);
  if(!cfe || !requiresPurchaseCategory(cfe) || (value && !Object.hasOwn(PURCHASE_CATEGORIES,value)))return false;
  const next=JSON.parse(JSON.stringify(invoiceStates)), previous=next[key]||{lines:{},excludedLines:[]};
  if(previous.purchaseCategory===value && !Object.keys(previous.linePurchaseCategories||{}).length)return true;
  // Una nueva elección de área sustituye los destinos particulares de sus líneas;
  // no toca las reglas reutilizables, datos fiscales, modalidad ni forma de pago.
  next[key]={...previous,purchaseCategory:value,linePurchaseCategories:{},lines:{},reviewed:false};
  const category=value?PURCHASE_CATEGORIES[value]:null;
  if(category){
    if(!isValidCenter(category.center)){
      toast("No está disponible el centro "+category.center+". Importá el catálogo real de centros antes de elegir este destino.");
      render();renderRelations();return false;
    }
    next[key].center=category.center;
  }
  if(value)syncVoucherForDestination(next[key],'article');
  if(!storageSet(STORE_INV,next)){render();renderRelations();return false;}
  invoiceStates=next;
  render();renderRelations();
  return true;
}

