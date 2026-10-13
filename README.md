# Cerecito

Base de código separada de los datos locales para preparar la nueva implementación.

Este repositorio contiene el programa y sus archivos de desarrollo. Los datos locales, la instalación anterior y las copias de trabajo quedan excluidos mediante `.gitignore`.

## Qué contiene

| Ruta | Contenido |
|---|---|
| `src/web/` | HTML, CSS y JavaScript de Cerecito, con base histórica vacía por defecto. |
| `src/backend/` | Motor Python y adaptador de Zeta. Solo módulos necesarios; sin reparaciones de facturas particulares. |
| `config/` | Configuración de ejemplo, sin empresa ni relaciones aprobadas. |
| `tests/` | Comprobaciones de separación y estructura. |
| `docs/` | Prompt de implementación y explicación de la separación. |
| `launcher.py`, `ABRIR_CERECITO.bat` | Apertura de la interfaz local. |
| `INSTALAR.bat`, `requirements.txt` | Preparación de dependencias en `.venv`. |
| `migrar_datos.py` | Copia los datos locales desde la instalación anterior, sin borrarla. |

## Datos fuera del repositorio

Por defecto se guardan en `../cerecito-datos-locales/`, una carpeta hermana del repositorio. Otra ubicación se define con la variable de entorno `CERECITO_DATA_DIR`.

```text
carpeta-de-trabajo/
  cerecito/               ← repositorio de código
  cerecito-datos-locales/  ← conservar localmente; no subir
    importar/             ← archivos nuevos para el receptor
    entrada/              ← XML/ZIP, Excel, catálogos y correlaciones
    estado/               ← journal e intentos, necesarios para no duplicar
    salida/               ← informes y diagnósticos
    web/                  ← resultado actual del bot
    perfil_chrome_bot/    ← sesión local de Chrome
    configuracion_zeta.json
    base_historica.js
```

No todos los datos locales son descartables: catálogos, correlaciones, decisiones y registros de cargas se deben conservar. Logs e informes anteriores pueden archivarse aparte. Un `git pull` actualiza el código y no toca esta carpeta.

## Primera instalación

1. Tener Python 3.11 o posterior y Google Chrome instalados.
2. Ejecutar `INSTALAR.bat`. Crea `.venv`, instala las dependencias y genera las carpetas de datos.
3. Ejecutar `ABRIR_CERECITO.bat`. Abre la interfaz en `http://127.0.0.1:8765/`.
4. Importar los catálogos y correlaciones desde la interfaz. Cargar los ZIP/XML.
5. Para operar contra Zeta, configurar la empresa y el documento de control en la configuración externa e iniciar sesión en Chrome del bot.

Si Python no está en el lanzador `py`, definir `CERECITO_PYTHON` con su ejecutable antes de instalar. La copia limpia no fija una ruta personal de Miniconda.

## Migración de los datos existentes

Con las dependencias instaladas:

```powershell
.venv\Scripts\python.exe migrar_datos.py "RUTA_DE_LA_INSTALACION_ANTERIOR"
```

No sobreescribe datos ya presentes ni copia cookies o perfiles de Chrome. Conserva los journals de cargas, copia las entradas y la base histórica. Catálogos y relaciones guardados únicamente en el navegador se deben exportar desde el Cerecito anterior e importar en el nuevo: el origen cambia de `file://` a HTTP local.

La configuración privada existente ya está separada en la carpeta de datos local de esta entrega. Un clon en otra computadora necesita configurar su empresa y sesión; los XML por sí solos no aportan esos datos.

## Estado de esta entrega

Es una **base de migración**, no la nueva experiencia terminada. La interfaz abre con datos separados y el motor conserva su funcionamiento anterior. La carga desde botones, las confirmaciones sin consola, la elección independiente de crédito/contado y la base SQLite se implementan con el prompt de `docs/PROMPT_IMPLEMENTACION.md`.

La entrada anterior del motor sigue siendo `src/backend/iniciar_bot.py`; ejecutarla requiere la interfaz local abierta, la configuración externa y los archivos de entrada. No ejecutar simultáneamente esta copia y la instalación original contra la misma empresa.

Los cambios de Tamareo y Luissi que habían quedado en preparación no se mezclaron con esta copia: el prompt los incluye como casos que la nueva implementación debe resolver.

## Verificación

```powershell
python -m unittest discover -s tests -v
```

Estas comprobaciones validan estructura y aislamiento. No guardan comprobantes en Zeta. La validación funcional de la nueva implementación debe añadir las pruebas de aceptación del prompt.

