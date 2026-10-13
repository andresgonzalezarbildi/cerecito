"""Comprueba dependencias locales sin descargar nada ni usar PATH."""

from runtime_paths import DATA_DIR, CODE_DIR, APP_DIR, APP_URL, company_rut, control_value, category_provider_rut
from importlib.metadata import version, PackageNotFoundError
import sys

def main():
    print('Python:', sys.executable)
    missing = []
    for package, minimum in [('playwright', (1, 60)), ('openpyxl', (3, 1))]:
        try:
            installed = version(package)
            print(package, installed)
            if tuple(int(x) for x in installed.split('.')[:2]) < minimum:
                missing.append(package)
        except PackageNotFoundError:
            missing.append(package)
    if missing:
        print('Faltan dependencias/versiones:', ', '.join(missing))
        print('Instalalas en Miniconda antes de continuar; ningún lanzador instala ni abre Chromium.')
        return 1
    return 0

if __name__ == '__main__':
    raise SystemExit(main())
