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
- Agrupación de toda una factura en una sola línea cuando no interesa conservar el detalle de ítems.
- Exclusión completa de una factura de la carga.
- Marcado individual o masivo como revisado.
- Pestaña para editar las correlaciones precargadas.
- Importación y exportación de correlaciones personalizadas en JSON.
- Persistencia local de correcciones mediante `localStorage`.
- Exportación CSV de las facturas listas/revisadas.
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

## Estructura

```text
facturalista-zeta/
├── index.html
├── css/
│   └── styles.css
├── js/
│   ├── data.js
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
5. Eliminar líneas o agruparlas cuando no interese conservar el detalle del CFE.
6. Marcar como `No cargar` los comprobantes que deban excluirse.
7. Marcar las facturas verificadas como revisadas.
8. Exportar la propuesta de carga y el informe de revisión.

Las correcciones de correlaciones quedan guardadas en el navegador y pueden exportarse a JSON para respaldo o para moverlas a otro equipo.

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
