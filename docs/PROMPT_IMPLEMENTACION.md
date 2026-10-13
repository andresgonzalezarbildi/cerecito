# Prompt para la nueva implementación

Copiar el texto siguiente al agente que vaya a trabajar en este repositorio.

---

Trabajá sobre este repositorio de Cerecito y completá una nueva experiencia de uso. No te limites a presentar una propuesta: implementá, probá y documentá el resultado. Primero leé README.md y revisá el motor y la interfaz existentes. Conservá las validaciones útiles y refactorizá los acoplamientos que impidan el flujo solicitado. No modifiques una instalación productiva externa al repositorio.

El programa importa comprobantes CFE XML/ZIP de Uruguay, propone sus correlaciones y los carga en Zeta. Quiero usarlo completamente desde una interfaz amigable: importar, revisar, elegir, confirmar identidades, ejecutar el bot y ver el resultado. No quiero interactuar con una consola, editar JSON para operaciones normales ni resolver instrucciones técnicas del tipo «revisar antes de cargar» sin una acción concreta.

## 1. Código separado de datos

Mantené todo el código, dependencias declaradas, migraciones y pruebas en Git. Guardá fuera del repositorio los XML, ZIP, Excel, catálogos reales, base histórica, SQLite, correlaciones, decisiones por comprobante, configuraciones de empresa, journals, sesiones de Chrome, logs y resultados. Usá CERECITO_DATA_DIR y runtime_paths.py como punto único de configuración de rutas. La ubicación por defecto es una carpeta hermana del repositorio.

Un git pull debe actualizar el programa sin sobreescribir los datos. La instalación inicial debe crear las carpetas y aplicar migraciones idempotentes; las actualizaciones deben migrar el esquema preservando los datos. No exigir enlaces de carpetas, nombres de usuario de Windows ni una ruta particular de Miniconda.

Mantené una configuración de ejemplo sin empresa, proveedores, credenciales ni facturas reales. Los ejemplos y fixtures deben ser sintéticos. No versionar un perfil de Chrome ni una base histórica empresarial embebida en JavaScript. Revisá también plantillas XLSX codificadas en base64 y sus metadatos.

Agregá un asistente inicial en la interfaz para empresa, catálogos, carpeta de datos y conexión con Zeta. La carga de catálogos y la configuración no deben requerir editar archivos a mano. Documentá el procedimiento concreto de clonación, primera instalación y actualización.

## 2. Crédito o contado: decisión independiente por factura

Cada comprobante debe tener un selector claramente visible «Crédito / Contado». El XML aporta una sugerencia, no impone la modalidad: a veces dice contado y la compra real es a crédito. La elección manual debe prevalecer y persistir al recargar, reimportar el XML o actualizar el programa.

La modalidad y el tipo de destino contable son DOS decisiones diferentes. Elegir un artículo o concepto no debe cambiar una decisión manual de Crédito/Contado.

Derivá el formulario por la combinación de ambas decisiones, usando el catálogo real:

| Modalidad elegida | Destino | Formulario esperado |
|---|---|---|
| Crédito | Artículos | Compra Crédito |
| Crédito | Conceptos/gastos | Compra Crédito Gastos |
| Contado | Artículos | Compra Contado |
| Contado | Conceptos/gastos | Compra Contado Gastos |

Esta regla REEMPLAZA la regla anterior de «artículo siempre crédito / concepto siempre gasto a crédito». No heredar silenciosamente una modalidad del histórico después de una decisión del usuario.

Para contado, pedir una forma de pago real y validar sus campos. Verificar e implementar los formularios nativos de Zeta: la base actual solo ejecuta los flujos de crédito. No convertir contado en crédito para lograr que cargue ni usar una ruta o un código de formulario inventado.

Si un documento tiene destinos mixtos que Zeta no admite en el mismo formulario, comprobar qué alternativa soporta Zeta y explicarla en la interfaz. No crear dos facturas fiscales ni cambiar códigos silenciosamente para eludir esta limitación.

## 3. Base extensa de artículos, conceptos y correlaciones

Implementá SQLite local, fuera del repositorio, con migraciones. Separá al menos empresas, proveedores y sus identidades fiscales, artículos, conceptos, tasas de IVA, centros, pagos, comprobantes importados, líneas originales, decisiones, reglas confirmadas, propuestas, trabajos e intentos de guardado. Registrá versiones de catálogos, fecha de actualización y procedencia de las sugerencias.

La amplitud de la base debe venir de importar los maestros reales y el histórico, no de inventar códigos contables. Permitir importación Excel/CSV/JSON con vista previa, validación y actualización sin borrar decisiones existentes. Incluir sinónimos y búsquedas por código, nombre, descripción original, proveedor, IVA y uso. Podés ofrecer una taxonomía genérica de categorías, pero los códigos de Zeta se obtienen de los maestros de cada empresa.

Proponer por proveedor + descripción + IVA y, cuando sirva, centro y uso. Mostrar de manera comprensible el motivo y confianza de cada sugerencia. Una coincidencia de nombre no confirma una identidad fiscal ni una regla de IVA. Distinguir «sugerida» de «elegida/confirmada».

Permitir carga automática de coincidencias suficientemente confiables que pasen las validaciones. Para las poco confiables, mostrar tarjetas de líneas con una elección sencilla de «Artículo / Gasto», un buscador de código + descripción y las mejores opciones. No exigir saber de memoria los códigos.

Cada elección se guarda por factura y línea. Ofrecer «usar esta elección también como regla para próximas compras» como acción explícita, con alcance empresa/proveedor/IVA; no aprender automáticamente una compra ocasional para todas las futuras.

Retirar las reglas y códigos particulares de proveedores que aún estén en el código. Llevarlos a datos/configuración importable, preservando su significado en la migración.

## 4. Interfaz de comprobantes y pendientes

Usar una tarjeta claramente separada por factura. Cabecera con proveedor, RUT, serie/número, fecha, moneda, total y estado. Dentro, campos alineados y líneas individuales con descripción, cantidad, importe, IVA original, sugerencia y selección.

Mostrar una bandeja con grupos claros: listas para cargar, necesitan una decisión, requieren verificar un registro en Zeta, cargadas, ya existentes, salteadas y documentos que no son facturas. Los salteados no deben aparecer como datos faltantes ni pedir completar correlaciones.

No mostrar «Requiere corregir el bot» cuando falta una elección que el usuario puede hacer, ni mostrar una factura completa como pendiente por un motivo viejo. Diferenciar el estado actual de las decisiones del resultado fechado de la última ejecución. Cada pendiente debe incluir motivo específico y una acción disponible; no dejar «Qué hacer ahora» vacío.

Centro de costos opcional: respetar el que eligió el usuario y permitir dejarlo vacío. La variabilidad del histórico no debe bloquear una selección manual válida ni una factura cuya revisión ya fue confirmada.

## 5. Bot y confirmaciones dentro de Cerecito

Conectar la UI con el motor Python mediante una API local. El launcher.py actual solo sirve archivos y un endpoint de salud: todavía no es una API de trabajos. Implementar ejecución en segundo plano, progreso por factura, actualización de resultados, cancelación segura y estados persistentes. Abrir la interfaz con un acceso directo sin una consola interactiva para el uso diario.

Botones como «Importar comprobantes», «Revisar pendientes», «Cargar listas», «Verificar en Zeta» y «Reintentar después de verificar». Mostrar errores comprensibles y detalles técnicos expandibles para soporte.

Si un proveedor de Zeta tiene el RUT vacío, mostrar en la UI el nombre/código de Zeta y el nombre/RUT del XML y un botón «Confirmar relación». Guardar esa confirmación para próximas cargas. Un RUT contradictorio debe bloquear y explicar el problema. Nunca pedir escribir CONFIRMAR en una consola.

La ejecución no debe congelar la interfaz ni volver a cargar una factura por un doble clic, una actualización del navegador o una respuesta perdida. Evitar trabajos concurrentes sobre la misma empresa y comprobante.

## 6. IVA exento, redondeos y líneas ignoradas

Conservar indicadores fiscales y valores originales del XML. El IVA exento es una operación admitida, no un pendiente genérico. En la inspección de la instalación anterior, el selector real de IVA de Zeta mostraba valor 0 con etiqueta «-», y el artículo exento quedaba con IVA 0. Verificar esa equivalencia en el formulario actual antes de confirmar cada línea; no asumir que cualquier artículo puede cambiar de IVA sin revisión.

Aplicar una tolerancia inicial de 1,00 UYU por comprobante a las diferencias de redondeo de base, IVA y total, incluyendo las diferencias de cálculo entre líneas y cabecera y la verificación posterior en Zeta. No multiplicarla por la cantidad de líneas. Preservar el XML y registrar los valores de origen, valores guardados y diferencias aceptadas. Mostrar/configurar la tolerancia desde la interfaz; una diferencia mayor debe explicar cuánto difiere y qué decisión se requiere.

Permitir «Ignorar línea» y «Volver a incluir» para redondeos o líneas no facturables admitidas. Persistir la decisión sin editar ni borrar el XML. Comprobar la identidad/origen de la línea y la consistencia de los totales dentro de la tolerancia. No ocultar productos gravados o exentos para hacer pasar una factura incompleta.

## 7. Duplicados y recuperaciones

Buscar por identidad fiscal y empresa; no bloquear por coincidencias de texto parcial. Caso de aceptación: la factura número 67 no debe quedar bloqueada por 944867, 71267 ni por un importe que contenga 67. Recorrer todas las páginas necesarias y verificar los filtros antes de demostrar ausencia. Una consulta incompleta no autoriza guardar.

Si aparece el mismo proveedor y número con una serie distinta, mostrar la comparación concreta y permitir verificar si es un registro ya existente mal identificado. No recrearlo automáticamente ni exigir una revisión vaga.

Antes de guardar, validar proveedor, formulario, todas las líneas y cálculos. Zeta puede guardar cabecera y líneas por separado: no prometer atomicidad que el sistema no ofrece. Mostrar cargas parciales con su ID y las líneas realmente guardadas. Ofrecer recuperación verificada de la misma operación; no crear otra cabecera, confirmar dos veces una línea ni borrar registros de terceros.

Un intento interrumpido debe liberarse automáticamente si se demuestra ausencia en la empresa correcta y el XML coincide. Si existe una cabecera propia incompleta, ofrecer verificar/completarla desde la interfaz. No anunciarla como factura completa ni dejar un bloqueo inexplicable sin salida.

## 8. Migración y pruebas de aceptación

Migrar catálogos, reglas, decisiones por factura e historial de intentos. La instalación anterior usaba localStorage y una URL file://; pasar a HTTP local cambia el origen. Implementar una importación guiada de las exportaciones anteriores y comprobar que no se pierdan decisiones. Conservar los journals aunque el usuario reinstale o actualice código.

Añadir pruebas significativas con Zeta simulado y fixtures sintéticos:

- Un XML dice contado; el usuario elige crédito y la elección persiste al reimportar.
- Elegir un concepto mantiene una decisión manual de contado y selecciona Contado Gastos; elegir artículo mantiene crédito y selecciona Compra Crédito.
- Los cuatro formularios se ejecutan con sus campos reales y pagos correctos.
- Un producto exento queda con IVA cero y conserva su importe.
- Una diferencia de 0,01 o 1,00 se acepta y se informa; más de 1,00 requiere decisión.
- Ignorar un redondeo no omite otro producto, conserva el origen y respeta la tolerancia.
- Los resultados parciales para número 67 se recorren y no se confunden con duplicados exactos.
- Una selección de centro válida y una revisión confirmada no quedan bloqueadas por el histórico.
- Confirmar un proveedor sin RUT funciona dentro de la UI y persiste.
- Saltear una factura la separa de los datos faltantes.
- La interfaz sigue respondiendo durante un lote y un doble clic no duplica operaciones.
- Una interrupción se verifica y recupera sin repetir cabecera ni líneas.
- Un clon limpio abre la aplicación; actualizar código conserva los datos externos; el repositorio no incorpora archivos reales.

No probar escribiendo facturas reales de producción sin autorización específica. Hacé primero pruebas locales, mocks, inspección de formularios y revisión sin guardar. Reportá con claridad qué flujos quedaron comprobados por simulación, lectura real o guardado real.

Entregá el código completo, migraciones, launcher, pruebas y documentación actualizados. El README debe explicar el uso normal sin consola. Identificá cualquier limitación real que aún impida el objetivo; no ocultarla bajo un mensaje genérico.

---
