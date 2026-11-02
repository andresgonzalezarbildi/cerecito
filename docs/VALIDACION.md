# Alcance de esta implementación

La nueva web y su API reemplazan el flujo diario de exportaciones manuales para preparar trabajos. La implementación no debe anunciarse como cumplimiento integral del prompt mientras falten los casos reales siguientes.

## Comprobado localmente

Resultado final de la suite: **42 pruebas aprobadas**, incluida la prueba real de la interfaz en Chrome headless con un motor simulado. Las pruebas de derivación de los cuatro formularios no son pruebas de sus campos reales ni de guardado nativo en Zeta.

- Persistencia SQLite externa con migraciones SQL 001, 002 y 003; catálogos y decisiones por empresa, líneas originales, reglas, identidades confirmadas, trabajos e intentos separados.
- Restauración automática del último ZIP y protección frente a XML contradictorios de la misma identidad.
- Decisión manual de modalidad independiente del destino, incluida reimportación y reinicio; derivación de los cuatro nombres de formulario.
- Propuestas por descripción, reglas explícitas por proveedor/IVA y rechazo de identidades fiscales contradictorias.
- Revisión secuencial, búsquedas, selección de centro opcional, salteados, ignorar/reincluir líneas no facturables y vista previa de catálogos y exportaciones anteriores.
- API y trabajo en segundo plano, lectura de la interfaz durante la ejecución simulada, prevención de doble trabajo y registro de interrupciones.
- Cálculo exento y tolerancia por comprobante; selector IVA cero comprobado contra una página simulada.
- Duplicados exactos: 67 no coincide con 944867, 71267 ni un texto de importe que contenga 67 en una consulta completa simulada. Otra serie con mismo proveedor/número exige verificación.
- Chrome headless: revisión → cambio de modalidad → elección → confirmación → recarga → carga simulada → consulta de resumen mientras corre → resultado.
- Plantilla XLSX base64 anterior inspeccionada: propiedades creator/lastModifiedBy «Cerecito», una fila de plantilla, sin base empresarial precargada. La nueva web no carga esa plantilla ni JavaScript histórico empresarial.

No se guardaron comprobantes reales en Zeta. Se inspeccionaron los controles del paginador de la conexión autenticada. La suite usa documentos y servicios simulados.

## Limitaciones que impiden la aceptación integral

1. **Contado nativo y pagos:** los nombres de los cuatro formularios se derivan y prueban, pero solo existen adaptadores de guardado para Crédito (21) y Crédito Gastos (26), heredados del motor. No se inventaron códigos, rutas ni campos de contado. Elegir un pago real valida su presencia en el maestro, pero faltan validaciones de sus campos nativos. La API impide guardar contado.
2. **Paginador de Zeta:** se recorren todas las páginas y se comprueban los filtros y resultados repetidos. Se inspeccionaron los controles reales y se probaron tres páginas simuladas. Falta comprobar una consulta real de varias páginas.
3. **Cabeceras parciales y recuperación:** se conservan los journals y se muestra ID/resultado técnico. El motor mantiene recuperación de ausencias verificadas y bloquea registros inciertos propios, pero todavía no hay una acción web que lea/complemente una cabecera propia parcial sin repetir líneas. No se presenta como factura cargada.
4. **Destinos mixtos:** se bloquean con motivo concreto. Falta comprobar en Zeta una alternativa compatible con una única factura fiscal.
5. **Histórico y migración amplia:** se puede importar el Excel que necesita el motor y migrar elecciones de exportaciones reconocidas. El histórico aún no alimenta automáticamente una base extensa de sugerencias estadísticas. Las reglas antiguas sin alcance de IVA quedan archivadas para confirmación; las exportaciones agrupadas no se convierten silenciosamente. Los journals JSON/JSONL existentes se conservan, pero su historial completo no se normaliza a SQLite ni aparece todavía íntegro en la nueva bandeja.
6. **Admisión fiscal:** el plan de guardado conserva el alcance anterior: CFE 111, UYU, sin ajustes globales, indicadores admitidos y cantidades/importes positivos. Notas de crédito, otras monedas y otros casos no tienen adaptador completo. Centro y revisión válidos no se bloquean por variabilidad estadística, pero una coincidencia exacta posible en el Excel aún exige comprobación de duplicados.
7. **Resultados:** las coincidencias exactas se apartan de la revisión. Los intentos inciertos siguen visibles. Se probaron la persistencia, la reimportación y la asignación de sector por factura con IVA mixto y destinos faltantes.

Para completar estos puntos se necesita una sesión de Zeta en una empresa de pruebas, inspección sin guardar de menú/formularios/paginador y fixtures de esos controles. Los ensayos de guardado real requieren autorización específica y documentos de prueba. No basta con mocks que inventen los campos que faltan.

## Archivos principales

`workspace_store.py`: datos, importación, decisiones, propuestas y clasificación. `workspace_jobs.py`: ejecución, exclusión y progreso. `launcher.py`: HTTP local, API y token de sesión. `workflow.js` / `workflow.css`: experiencia guiada. `cargar_lote.py`: motor protegido con callback de progreso y cancelación entre documentos.

Acceso automático: verificados en la sesión real el autocompletado de Chrome, el ingreso, la selección por RUT y la llegada a Compras. Se agregaron pruebas de apertura del perfil administrado, respeto de puertos personalizados, rechazo de empresas ambiguas y secuencia de acceso sin leer contraseñas. No se guardaron comprobantes.
