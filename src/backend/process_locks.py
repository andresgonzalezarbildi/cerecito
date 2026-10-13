"""Exclusión local con bloqueo del sistema operativo y compatibilidad con PID antiguos.

El archivo .guard permanece para que todos los procesos bloqueen el mismo archivo.
Windows/POSIX libera el bloqueo al cerrar el descriptor o al terminar el proceso.
El .lock solo contiene información de la ejecución; no decide la exclusión nativa.
Estos bloqueos nunca borran journals ni estados de operaciones contables.
"""

from runtime_paths import DATA_DIR, CODE_DIR, APP_DIR, APP_URL, company_rut, control_value, category_provider_rut
from contextlib import contextmanager
from datetime import datetime
import errno
import json
import os
from pathlib import Path
import uuid


class ProcessLockError(RuntimeError):
    pass


def process_running(pid):
    """True/False; None si el sistema no permite verificarlo. No termina procesos."""
    if type(pid) is not int or pid<=0:
        return None
    if os.name=='nt':
        import ctypes
        from ctypes import wintypes
        kernel=ctypes.WinDLL('kernel32',use_last_error=True)
        kernel.OpenProcess.argtypes=[wintypes.DWORD,wintypes.BOOL,wintypes.DWORD]
        kernel.OpenProcess.restype=wintypes.HANDLE
        kernel.WaitForSingleObject.argtypes=[wintypes.HANDLE,wintypes.DWORD]
        kernel.WaitForSingleObject.restype=wintypes.DWORD
        kernel.CloseHandle.argtypes=[wintypes.HANDLE]
        kernel.CloseHandle.restype=wintypes.BOOL
        handle=kernel.OpenProcess(0x00100000,False,pid)  # SYNCHRONIZE, solo consulta.
        if not handle:
            return False if ctypes.get_last_error() in {87,1168} else None
        try:
            result=kernel.WaitForSingleObject(handle,0)
            return False if result==0 else True if result==258 else None
        finally:
            kernel.CloseHandle(handle)
    try:
        os.kill(pid,0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return None


def acquire_native(fd):
    os.lseek(fd,0,os.SEEK_SET)
    if os.name=='nt':
        import msvcrt
        msvcrt.locking(fd,msvcrt.LK_NBLCK,1)
    else:
        import fcntl
        fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)


def release_native(fd):
    os.lseek(fd,0,os.SEEK_SET)
    if os.name=='nt':
        import msvcrt
        msvcrt.locking(fd,msvcrt.LK_UNLCK,1)
    else:
        import fcntl
        fcntl.flock(fd,fcntl.LOCK_UN)


def read_owner(path):
    raw=path.read_bytes()
    if len(raw)>16384:
        raise ProcessLockError('Archivo de bloqueo no reconocido: '+path.name+'. No se libera sin revisar.')
    text=raw.decode('utf-8').strip()
    if text.isascii() and text.isdigit() and int(text)>0:
        return {'format':'legacy-pid','pid':int(text)}
    try:
        record=json.loads(text)
    except (ValueError,UnicodeError):
        record=None
    if isinstance(record,dict) and record.get('format')=='cerecito-process-lock' and record.get('version')==1:
        return record
    raise ProcessLockError('Archivo de bloqueo no reconocido: '+path.name+'. No se libera sin revisar.')


def write_owner(path,owner):
    temporary=path.with_name('.'+path.name+'.'+owner['token']+'.tmp')
    try:
        with temporary.open('x',encoding='utf-8') as stream:
            json.dump(owner,stream,ensure_ascii=False)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary,path)
    finally:
        temporary.unlink(missing_ok=True)


@contextmanager
def process_lock(path):
    path=Path(path).resolve()
    path.parent.mkdir(parents=True,exist_ok=True)
    guard=path.with_name(path.name+'.guard')
    fd=os.open(guard,os.O_CREAT|os.O_RDWR,0o600)
    os.set_inheritable(fd,False)
    locked=False
    owned=False
    owner={'format':'cerecito-process-lock','version':1,'pid':os.getpid(),
           'token':uuid.uuid4().hex,'inicio':datetime.now().isoformat(timespec='seconds')}
    recovery=None
    try:
        try:
            acquire_native(fd)
        except OSError as error:
            if error.errno in {errno.EACCES,errno.EAGAIN,errno.EDEADLK} or getattr(error,'winerror',None) in {32,33}:
                raise ProcessLockError('Hay una ejecución activa que mantiene '+path.name+'. No se inicia otra carga simultánea.') from error
            raise
        locked=True
        if os.fstat(fd).st_size==0:
            os.write(fd,b'0')
        if path.exists():
            previous=read_owner(path)
            if previous['format']=='legacy-pid':
                status=process_running(previous['pid'])
                if status is not False:
                    detail='sigue activo' if status else 'no se pudo verificar'
                    raise ProcessLockError(f"El proceso {previous['pid']} de {path.name} {detail}. No se libera el bloqueo.")
            # Un registro nativo solo se recupera tras obtener su bloqueo del SO.
            recovery={'archivo':path.name,'pid_anterior':previous.get('pid'),'formato_anterior':previous['format']}
        else:
            # Mantener compatibilidad con un script antiguo que usa O_EXCL.
            try:
                claim=os.open(path,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600)
            except FileExistsError as error:
                raise ProcessLockError('Otro proceso inició '+path.name+' durante la comprobación. No se carga.') from error
            try:
                os.write(claim,(str(os.getpid())+'\n').encode('ascii'))
            finally:
                os.close(claim)
        write_owner(path,owner)
        owned=True
        if recovery:
            print('Bloqueo de ejecución anterior recuperado: '+path.name,flush=True)
        yield recovery
    finally:
        try:
            if owned:
                try:
                    current=read_owner(path)
                except FileNotFoundError:
                    current=None
                if current and current.get('token')==owner['token']:
                    path.unlink()
        finally:
            try:
                if locked:
                    release_native(fd)
            finally:
                os.close(fd)
