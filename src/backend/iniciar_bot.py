"""Inicio local del lote completo, con comprobaciones antes de cada guardado."""

from runtime_paths import DATA_DIR, CODE_DIR, APP_DIR, APP_URL, company_rut, control_value, category_provider_rut
import argparse
from datetime import datetime
import json
import os
import subprocess
import sys
from conciliar import BASE
from zeta_bot import exclusive_lock


def lote_status(audit):
    if not audit or audit.get('total',0)<1:
        raise RuntimeError('Falta revisión completa del lote.')
    pending=audit.get('pendientes_locales',0);review=audit.get('por_revisar',0)
    return {'estado':audit['estado'],
            'motivo':f'Lote de {audit["total"]} comprobantes: {pending} candidatos pendientes; {review} requieren revisión. Los formularios sin validar quedan bloqueados.',
            'pendientes_locales':pending,'por_revisar':review,'informe_lote':str(BASE/'salida/lote/informe.json')}


def main(review_only=False):
    folder=BASE/'salida/inicio_bot'
    folder.mkdir(parents=True,exist_ok=True)
    report_path=folder/('ejecucion_'+datetime.now().strftime('%Y%m%d_%H%M%S_%f')+'.json')
    report={'inicio':datetime.now().isoformat(timespec='seconds'),'estado':'INICIANDO','guardado_real':False,'pasos':[],'modo':'SOLO_REVISAR' if review_only else 'CARGAR_LOTE'}
    def persist():report_path.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    persist();result=1
    try:
        with exclusive_lock(BASE/'estado/iniciar_bot.lock') as recovered:
            if recovered:report.setdefault('bloqueos_recuperados',[]).append(recovered)
            # El proceso principal pudo cerrarse dejando un hijo activo. Verificar
            # los recursos usados por esos hijos antes de volver a recibir archivos.
            state_folder=BASE/'estado'
            lock_paths=set(state_folder.glob('*.lock'))
            lock_paths.update(path.with_name(path.name[:-len('.guard')]) for path in state_folder.glob('*.lock.guard'))
            for lock_path in sorted(lock_paths):
                if lock_path.name=='iniciar_bot.lock':continue
                with exclusive_lock(lock_path) as child_recovery:
                    if child_recovery:report.setdefault('bloqueos_recuperados',[]).append(child_recovery)
            persist()
            print('Inicio del bot: recorrer todos los XML, comprobar duplicados y respetar correlaciones. Centros no definidos quedan vacios.',flush=True)
            steps=[('Entorno Python','preparar_entorno.py'),('Recepcion de archivos de la carpeta principal','recibir_archivos.py'),('Chrome dedicado','abrir_chrome_bot.py'),('Leer XML actuales y consultar todas las facturas en Zeta','ejecutar_desde_xml.py')]
            for name,script in steps:
                step={'paso':name,'estado':'INICIANDO'};report['pasos'].append(step);persist()
                print('\n'+name,flush=True)
                command=[sys.executable,'-X','utf8',str(CODE_DIR/script)]
                if script=='ejecutar_desde_xml.py' and not review_only:command.extend(['--guardar','--confirmar-correlaciones'])
                process=subprocess.run(command,timeout=7200 if script=='ejecutar_desde_xml.py' else 180,check=False)
                step.update(estado='VERIFICADO' if process.returncode==0 else 'BLOQUEADO',codigo_salida=process.returncode);persist()
                if script=='ejecutar_desde_xml.py' and (BASE/'salida/lote/carga.json').exists():
                    batch=json.loads((BASE/'salida/lote/carga.json').read_text(encoding='utf-8'))
                    report.update(estado=batch['estado'],guardado_real=batch.get('guardado_real',False),resumen=batch.get('resumen_resultados',{}),informe_lote=str(BASE/'salida/lote/carga.json'),total=batch.get('total',0))
                    if batch.get('motivo'):report['motivo']=batch['motivo']
                if process.returncode:
                    report.setdefault('motivo','Se detuvo en: '+name);report['estado']='DETENIDO';result=process.returncode;break
            else:result=0
    except subprocess.TimeoutExpired:
        report.update(estado='DETENIDO',motivo='Se excedio el plazo. Si hubo guardado iniciado, verificar su estado en Zeta antes de reanudar.')
    except Exception as error:
        report.update(estado='DETENIDO',motivo=str(error).splitlines()[0][:300])
    report['fin']=datetime.now().isoformat(timespec='seconds');persist()
    print('\n'+report['estado']+': '+report.get('motivo',json.dumps(report.get('resumen',{}),ensure_ascii=False)),flush=True)
    print('Informe: '+str(report_path.resolve()),flush=True)
    pending=BASE/'salida/lote/pendientes.html'
    if report.get('informe_lote') and pending.exists():
        print('Resultado de las facturas: '+str(pending.resolve()),flush=True)
        try:os.startfile(str(pending.resolve()))
        except OSError as error:print('No se pudo abrir el informe: '+str(error),flush=True)
    return result


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--solo-revisar',action='store_true')
    args=parser.parse_args();raise SystemExit(main(args.solo_revisar))
