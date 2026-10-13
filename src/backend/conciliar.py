"""Conciliación local, conservadora y sin escritura en Zeta.

Entradas:
 - entrada/cerecito/*.json, exportado desde Cerecito.
 - entrada/zeta/*.xlsx, histórico exportado desde Zeta.
Salida: cola.json, pendientes.csv, bloqueados.csv y resumen.txt.

La falta de datos en un reporte de Zeta nunca se interpreta como factura nueva.
"""
from __future__ import annotations

from runtime_paths import DATA_DIR, CODE_DIR, APP_DIR, APP_URL, company_rut, control_value, category_provider_rut

import argparse
import csv
import json
import hashlib
import re
import sys
import unicodedata
from collections import Counter
from datetime import datetime, date, timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path

from openpyxl import load_workbook

BASE = DATA_DIR


def normal(value):
    if value is None:
        return ""
    text = unicodedata.normalize("NFKD", str(value).strip())
    return re.sub(r"\s+", " ", "".join(c for c in text if not unicodedata.combining(c)).upper()).strip()


def digits(value):
    return re.sub(r"\D", "", str(value or ""))


def number(value):
    if value is None or value == "":
        return ""
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    text = normal(value)
    # Números de Excel pueden ser 00001234 o 1234.0. No se tocan sufijos alfabéticos.
    match = re.fullmatch(r"0*([0-9]+)(?:\.0+)?", text)
    return str(int(match.group(1))) if match else text


def serie(value):
    return re.sub(r"\s|-", "", normal(value))


def parse_serial(value):
    """Separar A-000123 si se exportó todo en la columna de número."""
    text = normal(value)
    m = re.fullmatch(r"([A-Z]{1,5})\s*[- /]\s*(\d+)(?:\.0+)?", text)
    if m:
        return serie(m.group(1)), number(m.group(2))
    return "", number(value)


def safe_amount(value):
    try:
        return Decimal(str(value if value is not None else 0))
    except (InvalidOperation, TypeError):
        return Decimal(0)


def one_file(folder, suffix):
    matches = sorted(p for p in folder.glob(f"*{suffix}") if not p.name.startswith("~$"))
    if len(matches) != 1:
        raise ValueError(f"En {folder} debe haber exactamente un {suffix}; encontré {len(matches)}")
    return matches[0]


ALIASES = {
    "rut": {"RUT", "RUC", "RUC EMISOR", "RUT PROVEEDOR", "DOCUMENTO PROVEEDOR", "RUC PROVEEDOR"},
    "codigo": {"CODIGO PROVEEDOR", "COD PROVEEDOR", "COD. PROVEEDOR", "ID PROVEEDOR", "PROVEEDOR CODIGO"},
    "proveedor": {"PROVEEDOR", "RAZON SOCIAL", "NOMBRE PROVEEDOR", "EMISOR", "NOMBRE"},
    "serie": {"SERIE", "SERIE COMPROBANTE", "SERIE DOCUMENTO"},
    "numero": {"NUMERO", "NRO", "N", "NO", "NUMERO COMPROBANTE", "NRO COMPROBANTE", "N COMPROBANTE", "NUMERO DOCUMENTO", "NRO DOCUMENTO", "FOLIO"},
    "tipo": {"COMPROBANTE", "TIPO COMPROBANTE", "CLASE COMPROBANTE"},
    "clase": {"TIPO", "TIPO MOVIMIENTO", "CLASE", "MOVIMIENTO"},
    "moneda": {"MONEDA", "DIVISA"},
    "total": {"TOTAL", "IMPORTE TOTAL", "IMPORTE", "MONTO"},
    "fecha": {"FECHA", "FECHA EMISION", "EMISION", "FECHA COMPROBANTE"},
}


def heading(value):
    x = normal(value)
    x = re.sub(r"[°º#.:_\-/]+", " ", x)
    return re.sub(r"\s+", " ", x).strip()


NORMALIZED_ALIASES = {k: {heading(a) for a in options} for k, options in ALIASES.items()}


def map_headings(row):
    found = {}
    for i, v in enumerate(row):
        h = heading(v)
        for field, choices in NORMALIZED_ALIASES.items():
            if field not in found and h in choices:
                found[field] = i
    return found


def excel_date(value):
    """Convierte fechas de Excel y los formatos más comunes sin depender de configuración regional."""
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, (int, float)):
        if 30000 < value < 80000:
            return (date(1899, 12, 30) + timedelta(days=int(value))).isoformat()
        return ""
    if value in (None, ""):
        return ""
    t = str(value).strip()
    try:
        return date.fromisoformat(t[:10]).isoformat()
    except ValueError:
        pass
    for fmt in ("%d/%m/%Y", "%d-%m-%Y", "%Y%m%d"):
        try:
            return datetime.strptime(t, fmt).date().isoformat()
        except ValueError:
            continue
    return ""


def currency(value):
    text = normal(value).replace(" ", "")
    if text in {"$", "$UY", "UYU", "PESOS", "PESO", "URUGUAYOS", "UY$"}:
        return "UYU"
    if text in {"U$S", "USD", "US$", "DOLARES", "DOLAR", "US DOLLARS"}:
        return "USD"
    return text


def amount(value):
    """Decimal en unidades monetarias; devuelve None si el valor no es interpretable."""
    if value is None or str(value).strip() == "":
        return None
    if isinstance(value, (float, int, Decimal)):
        try:
            result = Decimal(str(value))
            return result.quantize(Decimal('0.01')) if result.is_finite() else None
        except InvalidOperation:
            return None
    t = str(value).strip().replace("$", "").replace(" ", "")
    if "," in t:
        t = t.replace(".", "").replace(",", ".")
    try:
        result = Decimal(t)
        return result.quantize(Decimal('0.01')) if result.is_finite() else None
    except InvalidOperation:
        return None


def credit_kind(value):
    """Clase contable: NC, FACTURA o desconocida. Nunca igualar compra y devolución."""
    t = heading(value)
    if not t:
        return ""
    if "NOTA DE CREDITO" in t or "DEVOLUCION" in t:
        return "NC"
    if "COMPRA" in t or "FACTURA" in t:
        return "FACTURA"
    return ""


def cfe_kind(value):
    t = str(value or "").strip()
    if t in {"112", "122"}:
        return "NC"
    if t in {"111", "121"}:
        return "FACTURA"
    return ""


def read_excel(path):
    wb = load_workbook(path, read_only=True, data_only=True)
    records, sheets_info, issues = [], [], []
    for ws in wb.worksheets:
        rows = ws.iter_rows(values_only=True)
        candidates = []
        for pos, cells in enumerate(rows, 1):
            if pos > 40:
                break
            idx = map_headings(cells)
            if "numero" in idx and ({"rut", "codigo", "proveedor"} & idx.keys()):
                candidates.append((pos, idx))
        if not candidates:
            continue
        pos, idx = max(candidates, key=lambda pair: len(pair[1]))
        required = {"numero", "proveedor"}
        missing = required - set(idx)
        if missing and not ({"rut", "codigo"} & set(idx)):
            issues.append(f"{ws.title}: faltan columnas para identificar el proveedor")
            continue
        # El export real de Zeta incluye una línea A1='01/10/2026' B1='al'
        # (sin fecha de finalización cuando el usuario eligió desde una fecha).
        prefix = list(ws.iter_rows(min_row=1, max_row=max(1, pos-1), values_only=True))
        period_from = ""
        for cells in prefix:
            for j, v in enumerate(cells):
                if j + 1 < len(cells) and normal(cells[j+1]) == "AL":
                    period_from = excel_date(v)
                    break
            if period_from:
                break
        sheet_info = {"hoja": ws.title, "fila_encabezados": pos,
                      "columnas": sorted(idx.keys()), "fecha_desde_filtro": period_from}
        sheets_info.append(sheet_info)
        def get(row, field):
            return row[idx[field]] if field in idx and idx[field] < len(row) else None
        for rowidx, cells in enumerate(ws.iter_rows(min_row=pos + 1, values_only=True), pos + 1):
            if not any(v is not None and str(v).strip() for v in cells):
                continue
            identifier = get(cells, "numero")
            vendor = [get(cells, f) for f in ("rut", "codigo", "proveedor")]
            if identifier in (None, "") and not any(v not in (None, "") for v in vendor):
                continue
            if not any(v not in (None, "") for v in vendor):
                issues.append(f"{ws.title} fila {rowidx}: falta proveedor")
                continue
            xser = serie(get(cells, "serie"))
            yser, num = parse_serial(identifier)
            xser = xser or yser
            if not num:
                # Zeta permite comprobantes N° 0: no identifican una factura,
                # pero sí pueden revelar posible duplicación por monto/proveedor.
                if identifier is not None and number(identifier) == "0":
                    num = "0"
                else:
                    issues.append(f"{ws.title} fila {rowidx}: número inválido")
                    continue
            clas = normal(get(cells, "clase"))
            typ = normal(get(cells, "tipo"))
            records.append({
                "numero": num, "serie": xser, "rut": digits(get(cells, "rut")),
                "codigo": normal(get(cells, "codigo")), "nombre": normal(get(cells, "proveedor")),
                "tipo": typ or clas, "clase": clas, "fecha": excel_date(get(cells, "fecha")),
                "total": str(amount(get(cells, "total"))) if amount(get(cells, "total")) is not None else "",
                "moneda": currency(get(cells, "moneda")),
                "origen": f"{ws.title}:{rowidx}",
            })
    wb.close()
    if not sheets_info:
        raise ValueError("No encontré hoja con columnas 'Nº' y 'Proveedor' en el Excel Zeta.")
    if issues:
        raise ValueError("Excel Zeta tiene filas sin identificación segura; no se genera cola. " + "; ".join(issues[:8]))
    if not records:
        raise ValueError("El Excel no contiene comprobantes identificables.")
    return records, sheets_info


def supplier_match(invoice, row):
    p = invoice.get("proveedor", {})
    invoice_rut = digits(p.get("rut"))
    invoice_code = normal(p.get("codigoZeta"))
    invoice_name = normal(p.get("nombre"))
    # Conflictos fuertes entre identificadores conocidos: nunca afirmar coincidencia.
    if invoice_rut and row["rut"]:
        return invoice_rut == row["rut"], "RUT"
    if invoice_code and row["codigo"]:
        return invoice_code == row["codigo"], "código Zeta"
    if invoice_name and row["nombre"]:
        if invoice_name == row["nombre"]:
            return True, "nombre exacto"
        # Nombres distintos pueden ser alias del mismo proveedor: no descartarlos.
        return None, "nombres distintos sin RUT/código"
    # Columna Proveedor puede contener un código numérico en lugar del nombre.
    if invoice_code and row["nombre"] and invoice_code == row["nombre"]:
        return True, "código en proveedor"
    return None, "proveedor no verificable"


def invoice_id(inv):
    c, p = inv.get("cfe", {}), inv.get("proveedor", {})
    return "|".join([digits(p.get("rut")), str(c.get("tipo", "")), serie(c.get("serie")), number(c.get("numero"))])


def invoice_issues(inv):
    c = inv.get("cfe", {})
    warnings = []
    p = inv.get('proveedor', {})
    if not digits(p.get('rut')):
        warnings.append('Falta RUT del emisor: no se puede identificar el comprobante')
    if cfe_kind(c.get('tipo')) == '':
        warnings.append('Tipo CFE desconocido')
    if not excel_date(c.get('fecha')) or not currency(c.get('moneda')):
        warnings.append('Fecha/moneda XML faltante o inválida')
    if number(c.get('numero')) == '0':
        warnings.append('Número CFE cero: identificación ambigua')
    if inv.get("excluido"):
        warnings.append("Marcada No cargar en Cerecito")
    if digits(p.get('rut'))==category_provider_rut() and cfe_kind(c.get('tipo')):
        destination=inv.get('zeta',{}).get('destinoCompra')
        choices=[line.get('destinoCompra') for line in inv.get('lineas',[])]
        has_choices=any('destinoCompra' in line for line in inv.get('lineas',[]))
        if destination not in {'foods','housekeeping','mixed'} or (has_choices and any(value not in {'foods','housekeeping'} for value in choices)):
            warnings.append('Finesa: elegir Alimentos y bebidas o Housekeeping para cada producto en Cerecito → Correlaciones → Comprobantes')
        elif destination=='mixed' and (not choices or set(choices)!={'foods','housekeeping'}):
            warnings.append('Finesa: usos mixtos sin selección válida de todos los productos')
        elif destination!='mixed' and has_choices and any(value!=destination for value in choices):
            warnings.append('Finesa: la selección de los productos no coincide con el destino de la factura')
    if not inv.get("completo") or inv.get("estado") not in {"auto", "reviewed"}:
        alerts=inv.get('alertas',[])
        warnings.append('Cerecito: '+ '; '.join(alerts) if alerts else 'Pendiente o incompleta en Cerecito')
    if not inv.get("zeta", {}).get("comprobante"):
        warnings.append("Falta tipo comprobante Zeta")
    if not inv.get("proveedor", {}).get("codigoZeta"):
        warnings.append("Falta código proveedor Zeta")
    if not serie(c.get("serie")) or not number(c.get("numero")):
        warnings.append("Falta serie/número CFE")
    lines = inv.get("lineas", [])
    if not lines or any(not ln.get("codigoZeta") or not ln.get("tipoDestino") for ln in lines):
        warnings.append("Líneas sin destino/código Zeta")
    if any(ln.get("mezclaIVA") for ln in lines):
        warnings.append("Agrupación de líneas con distintos indicadores de IVA")
    if str(c.get("tipo", "")) in {"112", "122"}:
        warnings.append("Nota de crédito: requiere verificación de signo y referencia")
    # No inferimos IVA final desde IndFact: el control contra total se hará en Zeta.
    if c.get("total") is None:
        warnings.append("XML sin total")
    return warnings


def already_registered(path):
    if not path.exists():
        return set()
    found = set()
    latest = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            record = json.loads(line)
            if record.get("clave") and record.get("estado") == "confirmado":
                found.add(record["clave"])
                latest[record['clave']] = record
            elif record.get('estado') == 'eliminacion_verificada':
                prior = latest.get(record.get('clave'), {})
                proof = record.get('consultas_actuales', [])
                control = record.get('control_positivo', {})
                years = record.get('anios_verificados', [])
                if (prior.get('docid') and record.get('docid') == prior['docid']
                        and prior.get('sha256XML') and record.get('sha256XML') == prior['sha256XML']
                        and record.get('control_positivo_verificado') is True
                        and control.get('fecha') and control.get('numero') == control_value('numero')
                        and any(r.get('number') == control_value('numero') and 'Mabipark' in r.get('text', '') for r in control.get('filas', []))
                        and years and len(years) == len(proof)
                        and set(years) == {2000 + int(p.get('desde', '0')[-2:]) for p in proof}
                        and all(p.get('filas') == [] and p.get('fecha')
                                and str(p.get('numero')) == record['clave'].split('|')[-1] for p in proof)):
                    found.discard(record['clave'])
    return found


def type_conflict(inv, row):
    expected = cfe_kind(inv.get("cfe", {}).get("tipo"))
    found = credit_kind(row.get("tipo")) or credit_kind(row.get("clase"))
    return bool(expected and found and expected != found)


def money_conflict(inv, row):
    a = amount(inv.get("cfe", {}).get("total"))
    b = amount(row.get("total"))
    return a is not None and b is not None and a != b


def currency_conflict(inv, row):
    a = currency(inv.get("cfe", {}).get("moneda"))
    b = currency(row.get("moneda"))
    return bool(a and b and a != b)


def match_zeta(inv, row):
    """Devuelve (resultado, motivo): seguro, revisar, diferente.

    La falta de serie/RUT en el export obliga a dar prioridad a no duplicar.
    No se clasifica número 0 como duplicado confirmado.
    """
    c = inv.get("cfe", {})
    num, ser = number(c.get("numero")), serie(c.get("serie"))
    supplier, why = supplier_match(inv, row)
    if row["numero"] not in (num, "0"):
        return "diferente", "número distinto"
    if row["serie"] and row["serie"] != ser:
        return "diferente", "serie distinta"
    # El número cero en Zeta solo bloquea si el proveedor y el monto coinciden.
    if row["numero"] == "0":
        if supplier is True and not money_conflict(inv, row) and amount(row["total"]) is not None:
            if type_conflict(inv, row) or currency_conflict(inv, row):
                return "diferente", "tipo/moneda incompatible"
            return "revisar", "Zeta usa N° 0 para mismo proveedor e importe"
        return "diferente", "sin número identificable"
    if supplier is True:
        if type_conflict(inv, row):
            return "revisar", "mismo número y proveedor, pero tipo distinto"
        if money_conflict(inv, row):
            return "revisar", "mismo número y proveedor, pero total diferente"
        if currency_conflict(inv, row):
            return "revisar", "mismo número y proveedor, pero moneda diferente"
        if not row['serie'] or why == 'nombre exacto':
            return 'revisar', 'coincidencia parcial: falta serie o identificación fuerte del proveedor en Zeta'
        if not (credit_kind(row.get('tipo')) or credit_kind(row.get('clase'))):
            return 'revisar', 'mismo comprobante pero Zeta no identifica compra/devolución'
        return "seguro", why
    if supplier is None:
        return "revisar", "mismo número, proveedor sin identificación confiable; importe/tipo no bastan para descartar"
    # Proveedor de nombre distinto puede ser un alias: si coincide exactamente el
    # número e importe, preferimos que el operador confirme que es otra empresa.
    if amount(row["total"]) is not None and not money_conflict(inv, row) and not type_conflict(inv, row) and not currency_conflict(inv, row):
        return "revisar", "mismo número e importe, proveedor distinto (posible alias)"
    return "diferente", "proveedor diferente"


def evaluate(invoices, rows, registered, sheets_info=None):
    period_start = max((x.get("fecha_desde_filtro", "") for x in (sheets_info or []) if x.get("fecha_desde_filtro")), default="")
    processed = []
    counts = Counter(invoice_id(inv) for inv in invoices)
    for inv in invoices:
        invkey = invoice_id(inv)
        sure, uncertain = [], []
        for row in rows:
            result, why = match_zeta(inv, row)
            if result == "seguro":
                sure.append(f"{row['origen']} ({why}; {row['tipo']})")
            elif result == "revisar":
                uncertain.append(f"{row['origen']} ({why})")
        reasons = []
        if inv.get("excluido"):
            status = "EXCLUIDA"
        elif counts[invkey] > 1:
            status = 'REVISAR_DUPLICADO'
            reasons.append('Clave repetida en JSON Cerecito')
        elif invkey in registered:
            status = "YA_CONFIRMADA_LOCAL"
        elif uncertain:
            # Incluso ante una coincidencia fuerte, otras filas contradictorias
            # requieren intervención para evitar consolidar documentos distintos.
            status = "REVISAR_DUPLICADO"
            reasons.append("Coincidencias dudosas en Zeta: " + ", ".join(uncertain[:8]))
            if sure:
                reasons.append("También coincide con: " + ", ".join(sure[:5]))
        elif sure:
            status = "EN_ZETA"
            reasons.append("Coincide con exportación Zeta: " + ", ".join(sure[:8]))
        else:
            checks = invoice_issues(inv)
            if checks:
                status = "REVISAR_DATOS"
                reasons.extend(checks)
            elif period_start and (not excel_date(inv.get("cfe", {}).get("fecha")) or excel_date(inv["cfe"].get("fecha")) < period_start):
                status = "FUERA_PERIODO_ZETA"
                reasons.append(f"El reporte solo empieza el {period_start}; no permite comprobar esta factura anterior")
            else:
                status = "PENDIENTE"
        processed.append({"clave": invkey, "estado": status, "motivos": reasons, "datos": inv})
    return processed


COLUMNS = ["Estado", "Motivos", "RUT", "Proveedor", "Código proveedor Zeta", "Tipo CFE", "Serie", "Número", "Fecha", "Total XML", "Moneda", "Comprobante Zeta", "Centro costo", "Líneas"]


def write_csv(path, processed):
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f, delimiter=";")
        w.writerow(COLUMNS)
        for v in processed:
            x = v["datos"]
            p, c, z = x.get("proveedor", {}), x.get("cfe", {}), x.get("zeta", {})
            w.writerow([v["estado"], " | ".join(v["motivos"]), p.get("rut"), p.get("nombre"), p.get("codigoZeta"), c.get("tipo"),
                       c.get("serie"), c.get("numero"), c.get("fecha"), c.get("total"), c.get("moneda"), z.get("comprobante"),
                       z.get("centroCosto"), len(x.get("lineas", []))])


def run(base=BASE):
    # Invalidar la cola de una corrida anterior antes de verificar nuevos archivos.
    old_queue = base / "salida" / "cola.json"
    old_queue.unlink(missing_ok=True)
    infile = one_file(base / "entrada" / "cerecito", ".json")
    zeta = one_file(base / "entrada" / "zeta", ".xlsx")
    data = json.loads(infile.read_text(encoding="utf-8-sig"))
    if data.get("formato") != "cerecito-agente-v1" or not isinstance(data.get("comprobantes"), list):
        raise ValueError("El JSON no es una exportación de 'Exportar para automatización' de Cerecito.")
    keys = [invoice_id(x) for x in data["comprobantes"]]
    if not keys or any(k.startswith("|") or not k.split("|")[-1] for k in keys):
        raise ValueError("El JSON está vacío o contiene CFE sin identificador válido.")
    if len(set(keys)) != len(keys):
        raise ValueError("El JSON contiene claves duplicadas: revisar Cerecito.")
    rows, sheets = read_excel(zeta)
    registered = already_registered(base / "estado" / "confirmados.jsonl")
    result = evaluate(data["comprobantes"], rows, registered, sheets)
    out = base / "salida"
    out.mkdir(parents=True, exist_ok=True)
    write_csv(out / "pendientes_para_zeta.csv", [x for x in result if x["estado"] == "PENDIENTE"])
    write_csv(out / "bloqueadas_o_ya_cargadas.csv", [x for x in result if x["estado"] != "PENDIENTE"])
    (out / "cola.json").write_text(json.dumps({"formato": "cerecito-cola-v1", "generado": datetime.now().isoformat(), "origenZeta": zeta.name,
                                               "sha256Cerecito": hashlib.sha256(infile.read_bytes()).hexdigest(),
                                               "sha256Zeta": hashlib.sha256(zeta.read_bytes()).hexdigest(),
                                               "registrosExcelZeta": len(rows), "hojasZeta": sheets, "facturas": [x for x in result if x["estado"] == "PENDIENTE"]},
                                              ensure_ascii=False, indent=2), encoding="utf-8")
    counts = Counter(x["estado"] for x in result)
    desc = [f"Exportación Cerecito: {infile.name}", f"Exportación Zeta: {zeta.name}", f"Registros leídos de Zeta: {len(rows)}",
            f"Filtro desde: {max((s.get('fecha_desde_filtro', '') for s in sheets), default='') or 'NO DETECTADO'}",
            f"Comprobantes con Nº 0: {sum(1 for r in rows if r['numero'] == '0')} (solo bloquean coincidencias de importe/proveedor, nunca se consideran comprobados)",
            "IMPORTANTE: este listado tiene límites de fecha y no incluye RUT ni serie. No prueba que una factura ausente no exista en Zeta.",
            "No se escribió ni confirmó ninguna factura en Zeta.", "", "RESULTADOS:"]
    desc.extend(f"{status}: {n}" for status, n in sorted(counts.items()))
    desc += ["", "PENDIENTE significa: no figura en el Excel proporcionado y pasa las validaciones locales, NO autorización de carga automática.",
             "FUERA_PERIODO_ZETA: factura anterior al inicio del reporte; para conciliarla necesitás otro histórico.",
             "No equivale a comprobante inexistente en Zeta. Antes de guardar hay que confirmar en pantalla."]
    summary = "\n".join(desc) + "\n"
    (out / "resumen.txt").write_text(summary, encoding="utf-8")
    print(summary)
    return result


def main():
    parser = argparse.ArgumentParser(description="Conciliar Cerecito con exportación de comprobantes Zeta")
    parser.add_argument("--base", type=Path, default=BASE, help="Carpeta automatizacion")
    args = parser.parse_args()
    try:
        run(args.base)
    except (OSError, ValueError, json.JSONDecodeError) as err:
        print("ERROR (no se generó una cola nueva):", err, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
