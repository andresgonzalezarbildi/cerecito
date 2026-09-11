# FacturaLista → Zeta

Herramienta local para asistir la carga de CFE recibidos desde **FacturaLista** hacia **Zeta**.

La aplicación lee los ZIP/XML descargados desde FacturaLista, utiliza correlaciones construidas a partir de cargas históricas de Zeta y separa los comprobantes entre los que pueden prepararse con suficiente confianza y los que requieren revisión manual.

> Estado actual: prototipo funcional sin conexión directa a la API de Zeta.

## Funcionalidades

- Lectura directa de ZIP de CFE recibidos de FacturaLista.
- Eliminación de CFE duplicados dentro de la carga.
- Correlación de RUT con proveedor de Zeta.
- Sugerencia de centro de costo, comprobante y artículo/concepto.
- Uso exclusivo de artículos activos del último maestro incorporado.
- Centro de costo 29 y centros sin descripción excluidos de las sugerencias automáticas.
- Clasificación en listos, pendientes, revisados, contado y no cargar.
- Cambio manual entre compra contado y compra crédito.
- Forma de pago editable para comprobantes contado.
- Eliminación de líneas individuales.
- Agrupación opcional por código Zeta: todas las líneas asignadas al mismo artículo/concepto se consolidan en una sola línea. Códigos distintos (por ejemplo Alimentos EXENTO y Alimentos 22%) permanecen separados.
- Exclusión completa de una factura de la carga.
- Marcado individual o masivo como revisado.
- Pestaña para editar las correlaciones precargadas.
- Importación y exportación de correlaciones personalizadas en JSON.
- Persistencia local de correcciones mediante `localStorage`.
- Exportación a Excel `.xlsx` para importar en Zeta, usando la misma estructura de 28 columnas de `Carga UTE.xlsx`.
- El Excel incluye únicamente comprobantes cargados con artículos activos; las compras por conceptos quedan fuera hasta habilitar la API de Zeta.
- Exportación CSV adicional para control y diagnóstico.
- Informe HTML por factura con resumen de carga, menor confianza y comprobantes marcados para revisión.
- Limpieza de la carga actual sin borrar las correlaciones aprendidas.

## Ejecutar localmente

No requiere Node.js, npm ni instalación de dependencias.

1. Clonar o descargar el repositorio.
2. Abrir `index.html` en un navegador moderno.
3. Cargar uno o varios ZIP descargados desde FacturaLista.

También puede servirse con cualquier servidor estático. Por ejemplo, con Python:

```bash
python -m http.server 8000
```

Luego abrir:

```text
http://localhost:8000
```

## Deploy en Netlify

La aplicación puede mantenerse publicada como sitio independiente en `cerecito.netlify.app` y mostrarse también desde el sitio principal en `/cerecito` mediante un proxy/rewrite de Netlify.

El `index.html` incluye una pequeña corrección de base URL para que los recursos relativos (`css/`, `js/` y `assets/`) funcionen tanto en:

```text
https://andresgonzalez.netlify.app/cerecito
https://andresgonzalez.netlify.app/cerecito/
```

Por lo tanto no es necesario duplicar el código dentro del repositorio principal: el sitio principal puede limitarse a definir el rewrite correspondiente en su `netlify.toml`.

El favicon y los íconos de la aplicación están en `assets/icons/`.

## Estructura

```text
facturalista-zeta/
├── index.html
├── assets/
│   └── icons/
│       ├── favicon.ico
│       ├── favicon-16.png
│       ├── favicon-32.png
│       ├── favicon-64.png
│       ├── apple-touch-icon.png
│       ├── icon-192.png
│       └── icon-512.png
├── css/
│   └── styles.css
├── js/
│   ├── data.js
│   ├── zeta_template.js
│   └── app.js
├── README.md
└── .gitignore
```

### `index.html`

Contiene la estructura de la interfaz y referencia los archivos CSS y JavaScript.

### `css/styles.css`

Contiene únicamente los estilos de la aplicación.

### `js/data.js`

Contiene la base histórica precargada utilizada para sugerir proveedores, centros de costo, artículos/conceptos, comprobantes y otras relaciones inferidas de las cargas anteriores.

Actualmente contiene información real de la empresa, por lo que **el repositorio debería mantenerse privado** mientras este archivo conserve esos datos.

### `js/zeta_template.js`

Contiene codificada la plantilla base `Carga UTE.xlsx` utilizada para generar el archivo de importación sin depender de servicios externos.

### `js/app.js`

Contiene la lógica de la aplicación:

- lectura y descompresión de ZIP;
- parseo de XML CFE;
- clasificación;
- revisión;
- manejo de correlaciones;
- persistencia local;
- exportaciones e informes.

## Flujo de uso

1. Descargar desde FacturaLista los CFE recibidos en formato ZIP/XML.
2. Cargar el ZIP en la aplicación.
3. Revisar los comprobantes marcados como pendientes.
4. Corregir centro de costo, tipo de comprobante, forma de pago o artículo/concepto cuando corresponda.
5. Eliminar líneas o agrupar por código Zeta cuando no interese conservar el detalle original del CFE.
6. Marcar como `No cargar` los comprobantes que deban excluirse.
7. Marcar las facturas verificadas como revisadas.
8. Exportar **Excel Zeta** para las facturas de artículos, y el informe de revisión para controlar también los casos de conceptos/API.

Las correcciones de correlaciones quedan guardadas en el navegador y pueden exportarse a JSON para respaldo o para moverlas a otro equipo.

## Exportación a Zeta por Excel

Mientras la API no esté habilitada, la aplicación puede generar un archivo `.xlsx` con la estructura de la plantilla de importación de comprobantes utilizada por Zeta (`Carga UTE.xlsx`).

El archivo contiene una fila `C` de cabezal por comprobante y una o más filas `L` de artículos. Se completan, entre otros datos, código de comprobante, serie, número, fecha, moneda, cotización cuando corresponde, proveedor, centro de costo, referencia y código de artículo.

Solo se incluyen facturas que estén completas y cuyo destino sea exclusivamente **Artículo**. Las facturas de gastos cargadas mediante **Conceptos** no se incluyen porque actualmente requieren la API o carga manual.

Cuando se activa **Agrupar por código**, las líneas que tengan el mismo código Zeta se consolidan sumando sus importes. Los códigos diferentes siempre permanecen separados. Las líneas sin código no se agrupan.

Para evitar diferencias por cantidades o descuentos del CFE, cada línea de importación se genera con cantidad `1` y como precio unitario el importe total asignado a ese artículo/código, siguiendo el esquema usado en la plantilla `Carga UTE.xlsx`.

En comprobantes contado la app sigue exigiendo seleccionar una forma de pago para control, pero la plantilla de importación de facturas no posee una columna de forma de pago. Por eso ese dato debe verificarse en Zeta luego de la importación.

## Criterio de clasificación

La aplicación trabaja de forma conservadora. Una correlación histórica no se toma automáticamente como correcta solo porque haya aparecido una vez.

Se consideran, entre otros datos:

- RUT/proveedor;
- descripción del ítem del XML;
- centro de costo;
- indicador/tasa de IVA;
- frecuencia histórica;
- consistencia de la correlación;
- correcciones manuales posteriores.

Cuando la evidencia no es suficiente, la factura queda marcada como pendiente.

## Datos utilizados

La base inicial fue construida cruzando:

- XML históricos de CFE recibidos desde FacturaLista;
- compras históricas exportadas desde Zeta;
- proveedores;
- conceptos;
- artículos activos;
- comprobantes configurados;
- centros de costo;
- monedas;
- formas de pago.

## Pendientes

Antes de automatizar la carga definitiva falta principalmente:

- integración con la API de Zeta;
- verificación de duplicados contra comprobantes ya existentes en Zeta;
- validación final de totales, IVA, descuentos y redondeos;
- tratamiento completo de notas de crédito y referencia a facturas originales;
- condiciones de pago y vencimientos de compras crédito;
- obtención automática de local, depósito, caja, usuario y demás códigos requeridos por Zeta;
- comparación paralela entre la propuesta de la app y las cargas reales antes de habilitar escritura automática;
- previsualización final antes de enviar una carga a Zeta.

## Persistencia

La aplicación utiliza `localStorage` para guardar decisiones por factura y correlaciones personalizadas.

El botón de limpiar datos elimina la carga actual, pero conserva las correlaciones. Para respaldarlas o moverlas a otro equipo se puede utilizar **Exportar correlaciones**.

## Privacidad

Todo el procesamiento actual ocurre localmente en el navegador. Los ZIP/XML cargados no se envían a un servidor.

El archivo `js/data.js` contiene correlaciones históricas reales. No conviene publicarlo en un repositorio público.


## Cambios de la v0.6

- El botón **Agrupar todas por código** actúa sobre todas las facturas cargadas.
- El Excel de Zeta **siempre agrupa por código** aunque en pantalla se vea el detalle original.
- La pestaña **Correlaciones** tiene un filtro para mostrar solo relaciones pendientes de elegir.
- El centro de costo dejó de ser obligatorio para exportar. Los centros `29` o sin descripción se dejan vacíos.
- Esto permite exportar correctamente casos como **UTE y ANTEL**, que se cargan como artículos aunque históricamente hayan usado centro `29`.
- La agrupación conserva códigos distintos por separado: por ejemplo `Alimentos EXENTO` y `Alimentos 22%` nunca se mezclan si sus códigos Zeta son diferentes.

