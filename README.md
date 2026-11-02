# Cerecito

Asistente local para importar CFE de Uruguay, revisar destinos y ejecutar el motor de compras de Zeta desde una web local. Esta entrega implementa el flujo persistente y su API; **todavía no satisface todos los criterios de aceptación**. Las limitaciones reales están detalladas en [docs/VALIDACION.md](docs/VALIDACION.md).

## Clonación e instalación

Necesitás Git para clonar, Python 3.11 o posterior y Google Chrome. En una terminal de instalación:

```powershell
git clone URL_DEL_REPOSITORIO cerecito
cd cerecito
```

Después hacé doble clic en `INSTALAR.bat`. Crea `.venv`, instala las dependencias declaradas y aplica las migraciones de SQLite. Detecta Python instalado aunque no exista `py`: busca ejecutables, instalaciones registradas de Python y carpetas habituales de Conda. Omite los alias de Microsoft Store y comprueba Python 3.11 o posterior. También admite `CERECITO_PYTHON` con la ruta del ejecutable. No hay rutas personales de Miniconda en los lanzadores.

Para el uso diario hacé doble clic en **ABRIR_CERECITO.bat**. Abre `http://127.0.0.1:8765/` mediante `pythonw`, sin consola interactiva. Podés crear un acceso directo a ese archivo desde el Explorador. Cerrá la aplicación y Chrome del bot antes de actualizar; no ejecutes dos instalaciones contra la misma empresa.

## Primera apertura

1. En el asistente, elegí la carpeta externa de datos antes de guardar la empresa. El valor predeterminado es `../cerecito-datos-locales/`. Una variable `CERECITO_DATA_DIR` explícita prevalece sobre la elección del asistente.
2. Completá nombre de empresa, RUT receptor y tolerancia por comprobante, inicialmente 1,00 UYU.
3. Importá los maestros desde Configuración. Primero se muestra una vista previa; confirmá para aplicarlos. Los códigos vienen exclusivamente de tus maestros.
4. Importá el histórico Excel de Zeta desde Configuración. El motor existente necesita ese reporte y un comprobante de control positivo para comprobar las búsquedas en la empresa correcta. Si faltan, el trabajo informa el error y no guarda.
5. Completá número, serie, código y nombre de proveedor de una factura que ya exista en esa empresa, en «Control de conexión y duplicados».
6. Usá «Abrir conexión con Zeta», iniciá sesión y elegí la empresa en Chrome. Cerecito utiliza un perfil exclusivo y persistente, externo al repositorio.

No se guardan credenciales en el código. La configuración de ejemplo está vacía y la carga permanece deshabilitada hasta que se inicia explícitamente un trabajo de carga desde la interfaz.

## Uso normal

**Inicio → Análisis del ZIP → Revisión → Resumen → Carga → Resultados.**

Importá un ZIP de FacturaLista o un XML. El lote queda guardado; al volver a abrir Cerecito se recuperan automáticamente el último lote y sus decisiones. Una identidad repetida conserva las elecciones, mientras que un XML fiscal distinto con la misma identidad se rechaza.

«Revisar pendientes» presenta una factura por vez. Elegí Crédito/Contado independientemente de Artículo/Gasto, buscá códigos por nombre o descripción, seleccioná proveedor y, opcionalmente, centro. El XML sugiere la modalidad; una elección manual prevalece. Cada cambio se guarda inmediatamente. Confirmá y avanzá, o salteá la factura. Las salteadas tienen su propia bandeja y pueden volver a incluirse. Las facturas listas también permiten «Revisar elección».

Antes de revisar, Cerecito busca las facturas en Zeta. Las coincidencias exactas quedan en «Ya existentes en Zeta» y salen de la revisión. La búsqueda no requiere clasificar las líneas ni importar un histórico Excel. Si no puede comprobar una factura, la mantiene visible con el motivo; una consulta incompleta nunca demuestra ausencia. «Buscar ya cargadas» repite la comprobación.

La búsqueda abre Chrome del bot si está cerrado. Si venció la sesión, usa el autocompletado del perfil para enviar una vez el formulario de acceso, selecciona la empresa por su RUT y abre Compras. No copia ni almacena contraseñas. Si Chrome no completa el acceso o Zeta exige una verificación adicional, deja el navegador abierto con un mensaje concreto. La recuperación de sesión sucede antes del lote; nunca repite un guardado interrumpido.

En cada factura, elegí un sector y usá «Aplicar sector a toda la factura». La distribución vincula exento, IVA 10% e IVA 22% con destinos reales del catálogo. Para A&B se proponen los artículos de Alimentos cuando la coincidencia es única. La distribución queda disponible para futuras facturas del sector; los impuestos sin destino quedan pendientes. Podés ajustar excepciones por línea. La modalidad y los importes originales se conservan.

Para combustible y otros gastos compartidos, elegí «Sin sector / para distribuir». Al aplicar sin destinos por IVA, se vacía el centro y se conservan las elecciones de las líneas. También podés seleccionar conceptos en «Destinos por IVA» y aplicarlos a toda la factura con el centro vacío. Esto no distribuye importes automáticamente entre sectores.

Las coincidencias por nombre son propuestas. Cada destino elegido manualmente en una línea se guarda automáticamente como regla para la empresa, RUT proveedor, descripción e IVA. La regla tiene prioridad sobre el histórico; una corrección manual actualiza el destino futuro. No modifica elecciones explícitas de otras facturas ni aprende asignaciones masivas de sector. Un proveedor se reconoce automáticamente solo por coincidencia fiscal única. Para proveedores sin RUT, la revisión muestra ambos nombres y permite «Confirmar relación»; el motor vuelve a comprobar el nombre/código real antes de guardar. Un RUT contradictorio bloquea.

Solo se pueden ignorar líneas no facturables con indicadores 6/7. El XML y sus orígenes se conservan; los productos gravados y exentos no pueden ocultarse. Los redondeos admitidos se muestran con sus diferencias y respetan la tolerancia por comprobante.

Las líneas con `MontoItem=0` e indicador 1, 2, 3 o 5 no requieren código de artículo: quedan en el XML y el bot las excluye de la carga solo si el total del comprobante sigue cuadrando con la tolerancia configurada. Los importes negativos de líneas gravadas o exentas y los importes positivos con cantidad cero quedan identificados para revisión manual: no se borran, ni se convierten en importes positivos o cero. Si todas las líneas valen cero, no se crea una compra vacía.

En el resumen se pueden cargar las facturas listas aunque queden otras pendientes. Las notas de crédito, otros tipos sin adaptador y las facturas con ajustes globales quedan en «Carga manual en Zeta» y no bloquean las facturas comunes. Esos comprobantes no se cargan automáticamente ni se convierten en compras comunes. «Verificar en Zeta» ejecuta el motor sin guardar; «Cargar listas» solicita el guardado de las facturas listas. Los trabajos corren en segundo plano, conservan resultados fechados y evitan concurrencia por empresa. «Cancelar después de esta factura» espera a terminar la operación actual. Recargar el navegador no reinicia el trabajo. Consultá los resultados y expandí los detalles para soporte.

**Contado:** se deriva correctamente Compra Contado o Compra Contado Gastos y se exige elegir un pago del maestro, pero el guardado de estos formularios está bloqueado hasta inspeccionar sus campos nativos reales. No se transforma en crédito. Los destinos mixtos tampoco se separan en dos facturas.

## Formato de maestros

CSV (separador detectado), XLSX (primera hoja, encabezados en la primera fila) o JSON con una lista de registros / `{ "catalog": [...] }`.

| Columna | Uso |
|---|---|
| `kind` | `supplier`, `article`, `concept`, `vat`, `center`, `payment` o `form` |
| `code` | Código real de Zeta; usar texto para preservar ceros iniciales |
| `name` | Nombre/descripción real |
| `rut` | RUT fiscal del proveedor; vacío exige confirmación explícita |
| `vat` | Indicador XML admitido para el destino: 1 exento, 2 mínimo, 3 básico |
| `synonyms` | Lista en JSON, o textos separados por `|` en CSV/Excel |

Ejemplo **sintético**, para probar sin empresa real:

```json
[
  {"kind":"supplier","code":"P-DEMO","name":"Proveedor sintético","rut":"000000000002"},
  {"kind":"article","code":"A-DEMO","name":"Producto sintético","vat":"1"},
  {"kind":"payment","code":"PAGO-DEMO","name":"Pago sintético"}
]
```

Actualizar un catálogo reemplaza únicamente registros de la misma empresa, tipo y código; no borra elecciones. Los registros tienen fecha de versión y procedencia. Importar un maestro de formularios **no habilita automáticamente** un adaptador de guardado sin verificarlo en Zeta.

## Datos, copias y actualizaciones

`runtime_paths.py` es la entrada de configuración de rutas. La elección inicial se recuerda en `%LOCALAPPDATA%/Cerecito/runtime.json`; `CERECITO_DATA_DIR` tiene prioridad. XML/ZIP, catálogos, configuración, SQLite, histórico, journals, resultados y perfil Chrome quedan fuera de Git. SQLite está en `estado/cerecito.sqlite`.

Con Cerecito y Chrome del bot cerrados, respaldá **toda** la carpeta de datos. Para mover una instalación ya configurada, copiá esa carpeta completa al nuevo destino y definí `CERECITO_DATA_DIR` en las variables de usuario de Windows antes de abrir. El asistente permite elegir la carpeta durante la instalación inicial; no mueve datos activos.

Para actualizar, cerrá Cerecito, hacé un respaldo y ejecutá:

```powershell
git pull
```

Después ejecutá `INSTALAR.bat` y volvé a abrir. Las migraciones SQL numeradas en `src/backend/migrations/` se aplican transaccionalmente solo una vez. Una actualización del código conserva los datos externos. No borres `estado/` ni reemplaces la carpeta de datos por una carpeta vacía: los journals son necesarios para impedir duplicados.

## Migrar la instalación anterior

Exportá correlaciones/decisiones desde el Cerecito anterior mientras todavía podés abrirlo con su origen `file://`. Importá primero sus XML en la nueva web; después, desde Configuración → «Migrar decisiones anteriores», seleccioná la exportación de correlaciones o el JSON para agente, revisá la vista previa y confirmá.

Se migran destinos por origen, modalidad, centro, pagos, salteados y revisión. Se conserva una copia íntegra del archivo importado en `entrada/correlaciones/`. Los XML ausentes o las líneas agrupadas impiden confirmar una migración incompleta. Las reglas antiguas que no identifican IVA se archivan con una advertencia; deben confirmarse explícitamente desde cada línea antes de usarlas automáticamente. No se asume que una sugerencia histórica equivale a una identidad fiscal verificada.

Para copiar archivos y journals de la instalación anterior sin modificarla, el procedimiento de mantenimiento sigue disponible:

```powershell
.venv\Scripts\python.exe migrar_datos.py "RUTA_DE_LA_INSTALACION_ANTERIOR"
```

Ese script conserva la instalación anterior y no copia cookies ni perfiles. La migración íntegra del histórico de reglas y la recuperación de cabeceras parciales desde la UI todavía requieren el trabajo descrito en [VALIDACION.md](docs/VALIDACION.md). La página `legacy.html` se conserva como referencia de la interfaz previa; el flujo diario usa la página principal y SQLite.

## Desarrollo y comprobación

```powershell
.venv\Scripts\python.exe -m unittest discover -s tests -v
```

Las pruebas usan empresas y XML sintéticos, Zeta simulado y un servidor HTTP temporal. La prueba de navegador utiliza Chrome local en modo headless; se omite si no está instalado. No se guardan facturas de producción durante la suite. El resultado y el alcance de comprobación están en [docs/VALIDACION.md](docs/VALIDACION.md).
