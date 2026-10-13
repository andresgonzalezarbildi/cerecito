"""Transporte WebSocket local CDP, sin dependencias externas.

El puente evita que una pestaña ajena congelada bloquee connect_over_cdp.
Solo expone la pestaña seleccionada y sus iframes a Playwright.
No crea, navega, cierra ni descarta pestañas. No persiste tráfico ni sesiones.
"""

from runtime_paths import DATA_DIR, CODE_DIR, APP_DIR, APP_URL, company_rut, control_value, category_provider_rut
import base64
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
import socket
import struct
import threading
from urllib.parse import urlparse
import uuid

GUID = '258EAFA5-E914-47DA-95CA-C5AB0DC85B11'
MAX_FRAME = 32 * 1024 * 1024


class WebSocket:
    def __init__(self, sock, *, client, buffered=b''):
        self.sock, self.client, self.buffer = sock, client, buffered
        self.lock = threading.Lock()

    @classmethod
    def connect(cls, endpoint, timeout=45):
        url = urlparse(endpoint)
        if url.scheme != 'ws' or url.hostname not in {'localhost', '127.0.0.1', '::1'}:
            raise ValueError('CDP solo permite WebSocket local.')
        sock = socket.create_connection((url.hostname, url.port), timeout=timeout)
        key = base64.b64encode(os.urandom(16)).decode()
        request = f'GET {url.path} HTTP/1.1\r\nHost: {url.netloc}\r\nUpgrade: websocket\r\nConnection: Upgrade\r\nSec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n'
        try:
            sock.sendall(request.encode('ascii'))
            response = b''
            while b'\r\n\r\n' not in response:
                chunk = sock.recv(4096)
                if not chunk or len(response) > 16384:
                    raise ConnectionError('Handshake WebSocket inválido.')
                response += chunk
            header, buffered = response.split(b'\r\n\r\n', 1)
            expected = base64.b64encode(hashlib.sha1((key + GUID).encode()).digest()).decode()
            if not header.startswith(b'HTTP/1.1 101') or expected.encode() not in header:
                raise ConnectionError('Chrome rechazó el handshake WebSocket.')
            return cls(sock, client=True, buffered=buffered)
        except Exception:
            sock.close()
            raise

    def read(self, count):
        while len(self.buffer) < count:
            chunk = self.sock.recv(max(4096, count - len(self.buffer)))
            if not chunk:
                raise ConnectionError('CDP desconectado.')
            self.buffer += chunk
        result, self.buffer = self.buffer[:count], self.buffer[count:]
        return result

    def send(self, data, opcode=1):
        if isinstance(data, str):
            data = data.encode('utf-8')
        if len(data) > MAX_FRAME:
            raise ValueError('Mensaje CDP demasiado grande.')
        mask = os.urandom(4) if self.client else b''
        size, flag = len(data), 128 if self.client else 0
        if size < 126:
            header = bytes([128 | opcode, flag | size])
        elif size < 65536:
            header = bytes([128 | opcode, flag | 126]) + struct.pack('!H', size)
        else:
            header = bytes([128 | opcode, flag | 127]) + struct.pack('!Q', size)
        if mask:
            data = bytes(v ^ mask[i % 4] for i, v in enumerate(data))
        with self.lock:
            self.sock.sendall(header + mask + data)

    def receive(self):
        payload = b''
        while True:
            first, second = self.read(2)
            opcode, size = first & 15, second & 127
            if first & 112:
                raise ConnectionError('Extensión WebSocket no negociada.')
            if size == 126:
                size = struct.unpack('!H', self.read(2))[0]
            elif size == 127:
                size = struct.unpack('!Q', self.read(8))[0]
            if size > MAX_FRAME or len(payload) + size > MAX_FRAME:
                raise ConnectionError('Mensaje CDP supera el límite.')
            mask = self.read(4) if second & 128 else b''
            data = self.read(size)
            if mask:
                data = bytes(v ^ mask[i % 4] for i, v in enumerate(data))
            if opcode == 8:
                raise ConnectionError('Chrome cerró el transporte.')
            if opcode == 9:
                self.send(data, opcode=10)
                continue
            if opcode == 10:
                continue
            if opcode not in {0, 1}:
                raise ConnectionError('CDP requiere mensajes de texto.')
            payload += data
            if first & 128:
                return json.loads(payload)

    def send_json(self, obj):
        self.send(json.dumps(obj, separators=(',', ':')))

    def close(self):
        try:
            self.sock.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        self.sock.close()


def list_targets(endpoint, *, keep_connection=False):
    ws = WebSocket.connect(endpoint)
    try:
        ws.send_json({'id': 1, 'method': 'Target.getTargets'})
        while True:
            answer = ws.receive()
            if answer.get('id') == 1:
                if 'error' in answer:
                    raise ValueError('Chrome rechazó Target.getTargets.')
                if keep_connection:
                    kept = ws
                    ws = None
                    return answer['result']['targetInfos'], kept
                return answer['result']['targetInfos']
    finally:
        if ws:
            ws.close()


class ScopedCDP:
    def __init__(self, endpoint, target_id, initial_websocket=None):
        self.endpoint, self.target_id = endpoint, target_id
        self.path = '/devtools/browser/' + uuid.uuid4().hex
        owner = self
        self.connections = []
        self.guard = threading.Lock()
        self.initial_websocket = initial_websocket
        if initial_websocket:
            self.connections.append(initial_websocket)

        class Handler(BaseHTTPRequestHandler):
            protocol_version = 'HTTP/1.1'

            def log_message(self, *args):
                pass

            def do_GET(self):
                key = self.headers.get('Sec-WebSocket-Key')
                if self.path != owner.path or not key or self.headers.get('Upgrade', '').lower() != 'websocket':
                    self.send_error(404)
                    return
                self.send_response(101)
                self.send_header('Upgrade', 'websocket')
                self.send_header('Connection', 'Upgrade')
                self.send_header('Sec-WebSocket-Accept', base64.b64encode(hashlib.sha1((key + GUID).encode()).digest()).decode())
                self.end_headers()
                self.close_connection = True
                downstream = WebSocket(self.connection, client=False)
                upstream = None
                try:
                    with owner.guard:
                        upstream = owner.initial_websocket
                        owner.initial_websocket = None
                    if upstream is None:
                        upstream = WebSocket.connect(owner.endpoint)
                    upstream.sock.settimeout(None)
                    self.connection.settimeout(None)
                    with owner.guard:
                        owner.connections.extend([upstream, downstream])
                    suppressed = set()
                    internal_id = -1

                    def from_chrome():
                        nonlocal internal_id
                        try:
                            while True:
                                msg = upstream.receive()
                                if isinstance(msg.get('id'), int) and msg['id'] < 0:
                                    continue
                                method, params = msg.get('method'), msg.get('params', {})
                                if method == 'Target.attachedToTarget':
                                    info = params['targetInfo']
                                    # Iframes pertenecen al árbol seleccionado; no son páginas ajenas.
                                    if info['type'] != 'iframe' and info['targetId'] != owner.target_id:
                                        session = params['sessionId']
                                        suppressed.add(session)
                                        # No dejar suspendida una pestaña ajena por el auto-attach.
                                        if params.get('waitingForDebugger'):
                                            upstream.send_json({'id': internal_id, 'sessionId': session, 'method': 'Runtime.runIfWaitingForDebugger'})
                                            internal_id -= 1
                                        upstream.send_json({'id': internal_id, 'method': 'Target.detachFromTarget', 'params': {'sessionId': session}})
                                        internal_id -= 1
                                        continue
                                if msg.get('sessionId') in suppressed or (method == 'Target.detachedFromTarget' and params.get('sessionId') in suppressed):
                                    continue
                                if 'result' in msg and 'targetInfos' in msg['result']:
                                    msg['result']['targetInfos'] = [t for t in msg['result']['targetInfos'] if t['targetId'] == owner.target_id]
                                downstream.send_json(msg)
                        except (OSError, ValueError, ConnectionError):
                            downstream.close()

                    relay = threading.Thread(target=from_chrome, daemon=True)
                    relay.start()
                    while True:
                        msg = downstream.receive()
                        # Protección adicional: nunca cerrar el Chrome habitual desde este puente.
                        if msg.get('method') in {'Browser.close', 'Target.closeTarget'}:
                            downstream.send_json({'id': msg['id'], 'error': {'code': -32000, 'message': 'Cerrar Chrome/pestañas está bloqueado por Cerecito.'}})
                            continue
                        if msg.get('method') == 'Target.setAutoAttach':
                            msg.setdefault('params', {})['waitForDebuggerOnStart'] = False
                        upstream.send_json(msg)
                except (OSError, ValueError, ConnectionError):
                    pass
                finally:
                    if upstream:
                        upstream.close()
                    downstream.close()

        self.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self.server.daemon_threads = True
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.url = f'ws://127.0.0.1:{self.server.server_port}{self.path}'

    def close(self):
        for connection in self.connections:
            connection.close()
        self.server.shutdown()
        self.server.server_close()
