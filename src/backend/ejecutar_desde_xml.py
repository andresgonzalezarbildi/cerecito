"""Convierte los XML actuales en memoria y consulta Zeta en cada ejecución."""

from runtime_paths import DATA_DIR, CODE_DIR, APP_DIR, APP_URL, company_rut, control_value, category_provider_rut
import argparse
from conciliar import BASE
from convertir_cerecito import convert
from cargar_lote import run
from zeta_bot import exclusive_lock


def main(save=False,interactive=False):
    paths=sorted(p for p in (BASE/'entrada/xml').iterdir() if p.suffix.lower() in {'.zip','.xml'})
    with exclusive_lock(BASE/'estado/conversion.lock'):
        payload=convert(paths,memory_only=True,activate_json=False)
    return run(save=save,interactive=interactive,payload=payload)


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--guardar',action='store_true')
    parser.add_argument('--confirmar-correlaciones',action='store_true')
    args=parser.parse_args()
    try:raise SystemExit(main(args.guardar,args.confirmar_correlaciones))
    except Exception as error:
        print('NO SE PUDO PROCESAR: '+str(error).splitlines()[0][:300])
        raise SystemExit(1)
