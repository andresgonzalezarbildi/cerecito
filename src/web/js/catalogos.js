"use strict";

// Catálogos maestros, separados de XML, decisiones por factura y correlaciones.
const STORE_CATALOGS = "cerecito_catalogs_v1";
const CATALOG_EDITOR_VERSION = 3;
const CATALOG_TYPES = {
  articles:"Artículos", concepts:"Conceptos", vouchers:"Comprobantes",
  providers:"Proveedores", centers:"Centros de costos", payments:"Formas de pago", currencies:"Monedas"
};
let catalogOverrides = {}, catalogBase = null, catalogPreview = null, catalogLoadError = "";
let catalogImportGeneration = 0;
const catalogKey = value => String(value ?? "").normalize("NFD").replace(/\p{Diacritic}/gu, "").toUpperCase().replace(/[^A-Z0-9]/g, "");
const catalogCodeKey = value => String(value).trim().toUpperCase();
const catalogCopy = value => JSON.parse(JSON.stringify(value));

function catalogRecord(type, row) {
  if (!Object.hasOwn(CATALOG_TYPES,type) || !row || typeof row !== "object" || Array.isArray(row)) throw Error("Tipo o fila de catálogo inválido.");
  const fields = new Map();
  for (const [key,value] of Object.entries(row)) {
    const name = catalogKey(key);
    if (fields.has(name) && String(fields.get(name)) !== String(value)) throw Error("Columnas repetidas con valores diferentes: " + key);
    fields.set(name,value);
  }
  const pick = (...names) => {
    for (const name of names) if (fields.has(catalogKey(name))) return fields.get(catalogKey(name));
    return undefined;
  };
  const clean = value => String(value ?? "").trim();
  const code = clean(pick("code","código","cod","código Zeta", "código artículo", "código concepto", "código proveedor", "providerCode"));
  const name = clean(pick("name","nombre","desc","descripción","denominación","artículo","concepto","comprobante","providerName","razón social")).replace(/\s+/g," ");
  if (!code || !name || code.length>80 || name.length>300 || /[\u0000-\u001f]/.test(code+name)) throw Error("Cada fila necesita código y nombre/descripción válidos.");
  if (["__PROTO__","CONSTRUCTOR","PROTOTYPE"].includes(catalogCodeKey(code))) throw Error("Código inválido.");
  const rawActive = pick("active","activo","activa","estado");
  let active = true;
  if (rawActive !== undefined) {
    const value = catalogKey(rawActive);
    if (["S","SI","TRUE","1","ACTIVO","ACTIVA","HABILITADO"].includes(value)) active = true;
    else if (["N","NO","FALSE","0","INACTIVO","INACTIVA","DESHABILITADO"].includes(value)) active = false;
    else throw Error("Estado activo/inactivo no reconocido para " + code + ".");
  }
  const result = {code,name,active};
  if (type === "providers") {
    // El maestro Contactos de Zeta no incluye RUT: basta código y nombre.
    // La identidad fiscal se valida al correlacionar el emisor de una factura.
    result.rut = clean(pick("rut","ruc","RUCEmisor","documento","identificación fiscal")).replace(/[.\s-]/g, "");
    if (result.rut && !/^\d{12}$/.test(result.rut)) throw Error("El RUT del proveedor " + code + " debe tener 12 dígitos.");
  }
  if (type === "currencies") {
    result.iso = clean(pick("iso","código ISO")).toUpperCase();
    if (!/^[A-Z]{3}$/.test(result.iso)) throw Error("La moneda " + code + " necesita su código ISO.");
    result.symbol = clean(pick("symbol","símbolo"));
  }
  return result;
}

function catalogBatch(input) {
  const result = {};
  for (const [type,rows] of Object.entries(input)) {
    if (!Object.hasOwn(CATALOG_TYPES,type) || !Array.isArray(rows)) throw Error("Sección de catálogo no reconocida: " + type);
    if (rows.length>20000) throw Error("El catálogo supera 20.000 registros.");
    const seen = new Map();
    rows.forEach((row,index) => {
      let record;
      try { record = catalogRecord(type,row); } catch (error) { throw Error(CATALOG_TYPES[type]+", fila "+(index+1)+": "+error.message); }
      const key = catalogCodeKey(record.code);
      if (seen.has(key) && JSON.stringify(seen.get(key))!==JSON.stringify(record)) throw Error("Código repetido con datos distintos: " + record.code);
      seen.set(key,record);
    });
    result[type] = [...seen.values()];
  }
  if (!Object.values(result).some(rows => rows.length)) throw Error("No hay registros para importar.");
  return result;
}

function applyCatalogOverrides() {
  DB.catalog = catalogCopy(catalogBase.catalog);
  for (const [type,kind] of [["articles","article"],["concepts","concept"]]) {
    for (const row of catalogOverrides[type] || []) {
      DB.catalog = DB.catalog.filter(item => !(item.kind===kind && catalogCodeKey(item.code)===catalogCodeKey(row.code)));
      if (row.active) DB.catalog.push({kind,code:row.code,desc:row.name});
    }
  }
  for (const [type,table] of [["vouchers","comprobantes"],["centers","centerOptions"],["payments","formasPago"],["currencies","monedas"]]) {
    const rows = new Map(catalogCopy(catalogBase[table]).map(row => [catalogCodeKey(row.code ?? row.Código),row]));
    for (const row of catalogOverrides[type] || []) {
      const key = catalogCodeKey(row.code), previous = rows.get(key) || {};
      if (!row.active) { rows.delete(key); continue; }
      rows.set(key,type==="centers" ? {code:row.code,name:row.name} :
        {...previous,Código:row.code,Nombre:row.name,Activo:"S",
          ...(type==="currencies" ? {ISO:row.iso,Símbolo:row.symbol} : {})});
    }
    DB[table] = [...rows.values()];
  }
  DB.providers = catalogCopy(catalogBase.providers);
  const providers = new Map(catalogCopy(catalogBase.providerCatalog).map(row=>[catalogCodeKey(row.code),row]));
  for (const row of catalogOverrides.providers || []) {
    const key=catalogCodeKey(row.code);
    if(row.active)providers.set(key,row);else providers.delete(key);
    // Un nombre/código sin RUT no crea una correlación fiscal automáticamente.
    if(row.rut){
      if (row.active) DB.providers[row.rut] = {...DB.providers[row.rut],providerCode:row.code,providerName:row.name};
      else delete DB.providers[row.rut];
    }
  }
  DB.providerCatalog = [...providers.values()];
}

function initializeCatalogs() {
  const providers = new Map();
  for(const [rut,p] of Object.entries(DB.providers)){
    const key=catalogCodeKey(p.providerCode),previous=providers.get(key);
    // El histórico puede asociar un mismo código a varios RUT. No exportar
    // esa ambigüedad como una nueva correlación confirmada.
    providers.set(key,{code:p.providerCode,name:previous?.name||p.providerName,
      rut:previous && previous.rut!==rut ? "" : rut,active:true});
  }
  catalogBase = catalogCopy({catalog:DB.catalog,comprobantes:DB.comprobantes,centerOptions:DB.centerOptions,
    formasPago:DB.formasPago,monedas:DB.monedas,providers:DB.providers,providerCatalog:[...providers.values()]});
  try {
    const saved = localStorage.getItem(STORE_CATALOGS);
    if (saved) {
      const data = JSON.parse(saved);
      if (data.version!==1 || !data.catalogs) throw Error("Formato guardado desconocido.");
      catalogOverrides = catalogBatch(data.catalogs);
    }
  } catch (error) { catalogLoadError = "No se pudo leer la actualización de catálogos: " + error.message; }
  applyCatalogOverrides();
}

function refreshCatalogBindings() {
  ACTIVE_ARTICLE_CODES.clear();
  DB.catalog.filter(row=>row.kind==="article").forEach(row=>ACTIVE_ARTICLE_CODES.add(catalogCodeKey(row.code)));
  VALID_CENTERS.splice(0,VALID_CENTERS.length,...DB.centerOptions.filter(validCenterOption));
  codeDatalist(); render(); renderRelations(); refreshNewRelationCodes();
  for (const id of ["newProviderCenter","newLineCenter"]) {
    const node = document.getElementById(id), value = node.value;
    node.innerHTML = centerOptions(value);
  }
  const payment = document.getElementById("newProviderPayment"), value = payment.value;
  payment.innerHTML = paymentOptions(value);
  refreshProviderCatalogSuggestions();
  renderCatalogTable();
}

function commitCatalogs(batch) {
  batch = catalogBatch(batch);
  const next = catalogCopy(catalogOverrides);
  for (const [type,records] of Object.entries(batch)) {
    const indexed = new Map((next[type] || []).map(row => [catalogCodeKey(row.code),row]));
    records.forEach(row => indexed.set(catalogCodeKey(row.code),row));
    if (type==="providers") {
      const all = [...indexed.values()].filter(row=>row.active), ruts = new Set();
      for (const row of all) {
        if(!row.rut)continue;
        if (ruts.has(row.rut)) throw Error("Un RUT tiene más de un código de proveedor: " + row.rut);
        ruts.add(row.rut);
        const other = Object.entries(DB.providers).find(([rut,p])=>rut!==row.rut && catalogCodeKey(p.providerCode)===catalogCodeKey(row.code));
        if (other) throw Error("Código de proveedor asociado a otro RUT: " + row.code);
        const manual = rel.providers[row.rut];
        if (manual?.providerCode && catalogCodeKey(manual.providerCode)!==catalogCodeKey(row.code)) throw Error("El código del proveedor contradice su correlación guardada: " + row.rut);
      }
    }
    next[type] = [...indexed.values()];
  }
  // Un solo guardado, antes de tocar la base en memoria. No cambia correlaciones.
  if (!storageSet(STORE_CATALOGS,{version:1,catalogs:next})) throw Error("No se guardó el catálogo. Los valores anteriores se conservan.");
  catalogOverrides = next; applyCatalogOverrides(); refreshCatalogBindings();
  return Object.values(batch).reduce((count,rows)=>count+rows.length,0);
}

function parseCatalogCSV(text) {
  text = text.replace(/^\uFEFF/, "");
  if (/^sep=[;,\t]\r?\n/i.test(text)) text = text.replace(/^sep=[;,\t]\r?\n/i, "");
  const firstLine = text.split(/\r?\n/,1)[0];
  const count = delimiter => { let quotes=false,n=0; for (let i=0;i<firstLine.length;i++) { if(firstLine[i]==='"') { if(quotes&&firstLine[i+1]==='"')i++;else quotes=!quotes; } else if(!quotes&&firstLine[i]===delimiter)n++; } return n; };
  const delimiter = [";","\t",","].sort((a,b)=>count(b)-count(a))[0];
  const rows=[]; let row=[], cell="", quoted=false;
  for (let i=0;i<text.length;i++) {
    const c=text[i];
    if (c==='"') {
      if(quoted && text[i+1]==='"'){cell+='"';i++;}
      else if(quoted)quoted=false;
      else if(!cell)quoted=true;
      else throw Error("Comillas inválidas en el CSV.");
    } else if(c===delimiter && !quoted){row.push(cell);cell="";}
    else if((c==='\n'||c==='\r')&&!quoted){row.push(cell);if(row.some(x=>x.trim()))rows.push(row);row=[];cell="";if(c==='\r'&&text[i+1]==='\n')i++;}
    else cell+=c;
  }
  if(quoted)throw Error("El CSV tiene comillas sin cerrar.");
  row.push(cell);if(row.some(x=>x.trim()))rows.push(row);
  return rows;
}

function catalogObjects(rows) {
  const codes=["CODIGO","CODE","COD","CODIGOZETA","CODIGOARTICULO","CODIGOCONCEPTO","CODIGOPROVEEDOR"];
  const names=["NOMBRE","NAME","DESC","DESCRIPCION","DENOMINACION","ARTICULO","CONCEPTO","COMPROBANTE","RAZONSOCIAL"];
  const header = rows.findIndex(row=>row.some(x=>codes.includes(catalogKey(x)))&&row.some(x=>names.includes(catalogKey(x))));
  if(header<0)throw Error("No encontré encabezados Código y Nombre/Descripción. Importá un maestro de Zeta, no el Excel de compras.");
  const keys=rows[header].map(value=>String(value).trim()), normalized=keys.filter(Boolean).map(catalogKey);
  if(new Set(normalized).size!==normalized.length)throw Error("El archivo tiene encabezados repetidos.");
  const result=[];
  for(const [index,row] of rows.slice(header+1).entries()){
    if(!row.some(x=>String(x??"").trim()))continue;
    if(row.slice(keys.length).some(x=>String(x??"").trim()))throw Error("Fila "+(header+index+2)+": más valores que encabezados.");
    result.push(Object.fromEntries(keys.flatMap((key,i)=>key?[[key,row[i]??""]]:[])));
  }
  return result;
}

async function catalogXLSX(file) {
  const bytes=new Uint8Array(await file.arrayBuffer()), view=new DataView(bytes.buffer), end=getEOCD(view);
  let offset=view.getUint32(end+16,true), expanded=0; const entries=new Map();
  const decoder=new TextDecoder("utf-8");
  for(let i=0;i<view.getUint16(end+10,true);i++){
    if(view.getUint32(offset,true)!==0x02014b50)throw Error("Excel ZIP inválido.");
    const method=view.getUint16(offset+10,true), size=view.getUint32(offset+20,true), plain=view.getUint32(offset+24,true),
      n=view.getUint16(offset+28,true),extra=view.getUint16(offset+30,true),comment=view.getUint16(offset+32,true),
      local=view.getUint32(offset+42,true),name=decoder.decode(bytes.slice(offset+46,offset+46+n));
    if(/^xl\/(sharedStrings\.xml|styles\.xml|worksheets\/sheet\d+\.xml)$/.test(name)){
      if(plain>8*1024*1024 || (expanded+=plain)>24*1024*1024)throw Error("Excel demasiado grande para importar.");
      const start=local+30+view.getUint16(local+26,true)+view.getUint16(local+28,true), data=bytes.slice(start,start+size);
      if(method!==0 && method!==8)throw Error("Compresión de Excel no compatible.");
      let output=data;
      if(method===8){
        const reader=new Blob([data]).stream().pipeThrough(new DecompressionStream("deflate-raw")).getReader(), chunks=[];let length=0;
        for(;;){const {value,done}=await reader.read();if(done)break;length+=value.length;if(length>plain || length>8*1024*1024){await reader.cancel();throw Error("Tamaño del Excel inválido.");}chunks.push(value);}
        output=new Uint8Array(length);let pos=0;for(const chunk of chunks){output.set(chunk,pos);pos+=chunk.length;}
      }
      if(output.length!==plain)throw Error("Excel truncado.");
      const xml=new DOMParser().parseFromString(decoder.decode(output),"application/xml");
      if(xml.querySelector("parsererror"))throw Error("XML interno del Excel inválido.");
      entries.set(name,xml);
    }
    offset+=46+n+extra+comment;
  }
  const strings=[...(entries.get("xl/sharedStrings.xml")?.getElementsByTagNameNS("*","si")||[])].map(si=>[...si.getElementsByTagNameNS("*","t")].map(t=>t.textContent).join(""));
  const styles=entries.get("xl/styles.xml"), formats=new Map([...(styles?.getElementsByTagNameNS("*","numFmt")||[])].map(f=>[f.getAttribute("numFmtId"),f.getAttribute("formatCode")]));
  const xfs=[...(styles?.getElementsByTagNameNS("*","cellXfs")[0]?.children||[])];
  const sheets=[];
  for(const [name,xml] of entries){
    if(!name.startsWith("xl/worksheets/"))continue;
    const rows=[];
    for(const r of xml.getElementsByTagNameNS("*","row")){
      const row=[];
      for(const cell of r.getElementsByTagNameNS("*","c")){
        const reference=cell.getAttribute("r")||"", letters=reference.match(/^[A-Z]+/)?.[0];if(!letters)throw Error("Celda sin referencia.");
        let col=0;for(const char of letters)col=col*26+char.charCodeAt(0)-64;if(col>256)throw Error("El maestro tiene demasiadas columnas.");
        const t=cell.getAttribute("t"),v=cell.getElementsByTagNameNS("*","v")[0]?.textContent||"";let value=v;
        if(cell.getElementsByTagNameNS("*","f").length)throw Error("El maestro contiene fórmulas. Exportalo como valores.");
        if(t==="s"){if(!/^\d+$/.test(v)||strings[Number(v)]===undefined)throw Error("Texto del Excel inválido.");value=strings[Number(v)];}
        else if(t==="inlineStr")value=[...cell.getElementsByTagNameNS("*","t")].map(e=>e.textContent).join("");
        else if(t==="e")throw Error("El Excel contiene errores de celda.");
        else {
          const mask=formats.get(xfs[Number(cell.getAttribute("s")||0)]?.getAttribute("numFmtId"));
          if(mask && /^0{2,20}$/.test(mask) && /^\d+$/.test(v))value=v.padStart(mask.length,"0");
        }
        row[col-1]=value;
      }
      rows.push(Array.from({length:row.length},(_,i)=>row[i]??""));
      if(rows.length>20020)throw Error("El maestro supera 20.000 filas.");
    }
    sheets.push(rows);
  }
  if(!sheets.length)throw Error("El Excel no contiene hojas de datos.");
  return sheets;
}

async function readCatalogFile(file,type) {
  if(file.size>10*1024*1024)throw Error("El archivo supera 10 MB.");
  const ext=file.name.toLowerCase().split('.').pop();
  if(ext==="json"){
    const data=JSON.parse(await file.text());
    if(data.formato==="cerecito-catalogos-v1" && data.version===1)return catalogBatch(data.catalogs);
    const source=Array.isArray(data)?data:data[type];
    if(!Array.isArray(source))throw Error("El JSON debe contener una lista de registros o una exportación de catálogos de Cerecito.");
    return catalogBatch({[type]:source});
  }
  const sheets=ext==="xlsx"?await catalogXLSX(file):ext==="csv"||ext==="tsv"?[parseCatalogCSV(await file.text())]:null;
  if(!sheets)throw Error("Usá Excel .xlsx, CSV o JSON. Para .xls, guardá como .xlsx.");
  let selected=null;
  for(const rows of sheets){
    // Las hojas sin encabezados de maestro pueden ser carátulas del Excel.
    let objects;try{objects=catalogObjects(rows);}catch(error){if(error.message.startsWith("No encontré encabezados"))continue;throw error;}
    if(selected)throw Error("Hay varios maestros en el Excel. Importá una sola hoja de catálogo por archivo.");
    selected=objects;
  }
  if(!selected)throw Error("No encontré un maestro con Código y Nombre/Descripción.");
  return catalogBatch({[type]:selected});
}

function catalogMessage(text,error=false) {
  const node=document.getElementById("catalogStatus");node.textContent=text;node.classList.toggle("relationError",error);
}

function renderCatalogTable() {
  const type=document.getElementById("catalogType").value, search=catalogKey(document.getElementById("catalogSearch").value);
  let rows;
  if(["articles","concepts"].includes(type)) rows=DB.catalog.filter(row=>row.kind===(type==="articles"?"article":"concept")).map(row=>({code:row.code,name:row.desc,active:true}));
  else if(type==="providers")rows=DB.providerCatalog;
  else rows=DB[{vouchers:"comprobantes",centers:"centerOptions",payments:"formasPago",currencies:"monedas"}[type]].map(row=>({code:row.code??row.Código,name:row.name??row.Nombre,active:row.Activo!=="N"}));
  const filtered=rows.filter(row=>!search||catalogKey([row.code,row.name,row.rut].join(" ")).includes(search));
  document.getElementById("catalogCount").textContent=`${filtered.length} registros disponibles · mostrando hasta 200`;
  document.getElementById("catalogBody").innerHTML=filtered.slice(0,200).map(row=>`<tr><td>${esc(row.code)}</td><td>${esc(row.name)}</td><td>${esc(row.rut||"")}</td></tr>`).join("")||'<tr><td colspan="3">No hay registros con ese filtro.</td></tr>';
  document.getElementById("catalogProviderExtra").hidden=type!=="providers";
  document.getElementById("catalogCurrencyExtra").hidden=type!=="currencies";
}

function showCatalogPreview(batch,fileName) {
  catalogPreview=batch;const rows=[];
  for(const [type,records] of Object.entries(batch))for(const row of records)rows.push({type,...row});
  document.getElementById("catalogPreviewSummary").textContent=`${fileName}: ${rows.length} registros. Se agregan o actualizan por código; los demás se conservan.`;
  document.getElementById("catalogPreviewBody").innerHTML=rows.slice(0,100).map(row=>`<tr><td>${esc(CATALOG_TYPES[row.type])}</td><td>${esc(row.code)}</td><td>${esc(row.name)}</td><td>${row.active?"Activo":"Inactivo"}</td></tr>`).join("");
  document.getElementById("catalogPreview").hidden=false;
}

function initCatalogEditor() {
  const type=document.getElementById("catalogType");
  type.addEventListener("change",()=>{catalogImportGeneration++;catalogPreview=null;document.getElementById("catalogPreview").hidden=true;catalogMessage("Elegí un archivo de este catálogo o agregá un código.");renderCatalogTable();});
  document.getElementById("catalogSearch").addEventListener("input",renderCatalogTable);
  document.getElementById("catalogImport").addEventListener("change",async e=>{
    const file=e.target.files[0];e.target.value="";if(!file)return;
    const generation=++catalogImportGeneration;
    catalogPreview=null;document.getElementById("catalogPreview").hidden=true;
    catalogMessage("Leyendo catálogo…");
    try{const batch=await readCatalogFile(file,type.value);if(generation!==catalogImportGeneration)return;showCatalogPreview(batch,file.name);catalogMessage("Revisá el archivo y pulsá Guardar catálogo.");}
    catch(error){if(generation===catalogImportGeneration)catalogMessage(error.message,true);}
  });
  document.getElementById("catalogApply").addEventListener("click",()=>{
    if(!catalogPreview)return;
    try{const count=commitCatalogs(catalogPreview);catalogPreview=null;document.getElementById("catalogPreview").hidden=true;catalogMessage(`${count} registros guardados. Ya están disponibles en Carga y Correlaciones.`);}
    catch(error){catalogMessage(error.message,true);}
  });
  document.getElementById("catalogCancel").addEventListener("click",()=>{catalogImportGeneration++;catalogPreview=null;document.getElementById("catalogPreview").hidden=true;catalogMessage("Importación cancelada; no se guardó ningún cambio.");});
  document.getElementById("catalogAddForm").addEventListener("submit",e=>{
    e.preventDefault();const fields=e.target.elements;
    try{
      const row={code:fields.code.value,name:fields.namedItem("name").value,active:fields.active.checked,
        rut:fields.rut.value,iso:fields.iso.value,symbol:fields.symbol.value};
      commitCatalogs({[type.value]:[row]});
      catalogMessage("Código guardado y disponible para correlacionar: "+row.code);e.target.reset();
    }catch(error){catalogMessage(error.message,true);}
  });
  document.getElementById("catalogExport").addEventListener("click",()=>{
    const catalogs={articles:DB.catalog.filter(r=>r.kind==="article").map(r=>({code:r.code,name:r.desc,active:true})),
      concepts:DB.catalog.filter(r=>r.kind==="concept").map(r=>({code:r.code,name:r.desc,active:true})),
      vouchers:DB.comprobantes.map(r=>({code:r.Código,name:r.Nombre,active:r.Activo!=="N"})),
      centers:DB.centerOptions.map(r=>({code:r.code,name:r.name,active:true})),
      payments:DB.formasPago.map(r=>({code:r.Código,name:r.Nombre,active:true})),
      currencies:DB.monedas.map(r=>({code:r.Código,name:r.Nombre,active:true,iso:r.ISO,symbol:r.Símbolo})),
      providers:DB.providerCatalog};
    // Conservar también inactivaciones explícitas para que el respaldo sea fiel.
    for(const [type,rows] of Object.entries(catalogOverrides))for(const row of rows.filter(r=>!r.active)){
      catalogs[type]=catalogs[type].filter(r=>catalogCodeKey(r.code)!==catalogCodeKey(row.code));catalogs[type].push(row);
    }
    downloadText("catalogos_cerecito.json",JSON.stringify({formato:"cerecito-catalogos-v1",version:1,catalogs},null,2),"application/json;charset=utf-8");
  });
  document.getElementById("newProviderCode").setAttribute("list","providerCatalogCodes");
  document.getElementById("newProviderCode").addEventListener("input",()=>{
    const code=document.getElementById("newProviderCode").value,name=document.getElementById("newProviderName"),
      match=DB.providerCatalog.find(row=>catalogCodeKey(row.code)===catalogCodeKey(code));
    if(match && (!name.value || name.value===name.dataset.catalogSuggestion)){
      name.value=match.name;name.dataset.catalogSuggestion=match.name;
    }
  });
  refreshProviderCatalogSuggestions();renderCatalogTable();if(catalogLoadError)catalogMessage(catalogLoadError,true);
}

function refreshProviderCatalogSuggestions(){
  document.getElementById("providerCatalogCodes").innerHTML=DB.providerCatalog.map(row=>`<option value="${esc(row.code)}">${esc(row.name)}</option>`).join("");
}

document.addEventListener("DOMContentLoaded",initCatalogEditor);
