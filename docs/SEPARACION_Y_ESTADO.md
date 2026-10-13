# Separación de archivos y alcance de esta copia

La instalación de origen mezclaba el programa, enlaces a carpetas de entradas/resultados, configuración de empresa y un data.js con históricos reales. Esta copia usa rutas de datos externas y un punto único de configuración en runtime_paths.py.

## Subir

Todo el contenido de `cerecito-para-git/`, incluidos los archivos ocultos `.gitignore` y `.gitattributes`. Es una carpeta autónoma; no subir todo Desktop/FBD ni la carpeta original Documentos/cerecito.

## Conservar fuera de Git

| Datos necesarios para continuar | Datos generados que pueden archivarse |
|---|---|
| Configuración de empresa y conexión | Informes anteriores |
| Maestros de artículos, conceptos y proveedores | Capturas y diagnósticos |
| Correlaciones y decisiones manuales | Logs |
| XML y Excel de entrada | Cachés de Python y herramientas |
| Journals de cargas e intentos | Exportaciones de conversiones anteriores |
| Base de datos y sus migraciones aplicadas | Respaldos de versiones del código |
| Sesión local de Chrome | Entorno virtual recreable |

Los journals y las correlaciones no son basura: borrarlos puede perder decisiones o controles de duplicados. La carpeta `cerecito-datos-locales/` es privada y permanece fuera del repositorio aunque sus archivos sean necesarios para el programa.

## Diferencias frente a la instalación actual

- Copia de los módulos usados por el inicio y la interfaz, no de todas las herramientas de reparación.
- Configuración y base histórica separadas; el repositorio incluye una base vacía.
- Identidad de empresa y comprobante de control leídos de la configuración externa.
- El servidor local sirve la base histórica y el resultado actual desde datos externos; no escribe esos archivos en el programa.
- Plantilla XLSX reconstruida sin filas de facturas ni metadatos personales.
- Sin perfiles de Chrome, facturas de prueba reales, capturas, informes, ejecutables ni dependencias instaladas.

No es todavía la nueva implementación sin consola. La selección independiente de crédito/contado, el motor de trabajos desde la UI y la persistencia SQLite forman parte del prompt de desarrollo.

No se trasladaron cambios que habían quedado sin instalar o verificar en la sesión anterior. En particular, las correcciones del buscador para números cortos y el caso de formularios heredados de contado deben ser resueltos con las pruebas de aceptación del prompt.

La instalación original permanece en su lugar. La copia no configura un remoto Git, no publica ni hace push.
