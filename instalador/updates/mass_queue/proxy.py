"""One-shot dispatch guard and native FastCGI transport; runs as xui on loopback."""
from contextlib import contextmanager
from email.parser import BytesParser
from email.policy import default
import fcntl
import hashlib
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import socket
import struct
import tempfile
import threading
import time
from urllib.parse import parse_qs, urlsplit

ROOT = Path('/home/xui/custom-mass-queue')
STATE = Path('/home/xui/tmp/xui-mass-queue')
os.umask(0o077)


def read(path):
    return json.loads(path.read_text())


def write(path, data):
    fd, name = tempfile.mkstemp(prefix=path.name + '.', dir=str(path.parent))
    try:
        with os.fdopen(fd, 'w') as handle:
            json.dump(data, handle, ensure_ascii=False, separators=(',', ':'))
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


@contextmanager
def locked():
    with (STATE / 'queue.lock').open('a') as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        yield


def post_fields(content_type, body):
    pairs = []
    if content_type.startswith('application/x-www-form-urlencoded'):
        for key, values in parse_qs(body.decode(), keep_blank_values=True).items():
            pairs.extend((key, value) for value in values)
    elif content_type.startswith('multipart/form-data'):
        message = BytesParser(policy=default).parsebytes(('Content-Type: ' + content_type + '\r\nMIME-Version: 1.0\r\n\r\n').encode() + body)
        if not message.is_multipart():
            raise ValueError('Invalid multipart')
        for part in message.iter_parts():
            if part.get_filename() is not None:
                raise ValueError('Files not supported')
            key = part.get_param('name', header='content-disposition')
            value = (part.get_payload(decode=True) or b'').decode('utf-8')
            if not isinstance(key, str):
                raise ValueError('Missing field name')
            pairs.append((key, value))
    else:
        raise ValueError('Invalid content type')
    result = {}
    for key, value in pairs:
        if key.endswith('[]'):
            name = key[:-2]
            if name in result and not isinstance(result[name], list):
                raise ValueError('Mixed field types')
            result.setdefault(name, []).append(value)
        else:
            result[key] = value
    return result


def reserve(token, cookie_header, post):
    if len(token) != 64 or any(c not in '0123456789abcdef' for c in token):
        raise PermissionError('Reload the Mass Edit page')
    digest = hashlib.sha256(token.encode()).hexdigest()
    cookies = SimpleCookie(cookie_header)
    sid = cookies['PHPSESSID'].value if 'PHPSESSID' in cookies else ''
    with locked():
        try:
            pointer = read(STATE / 'dispatch' / (digest + '.json'))
        except FileNotFoundError:
            raise PermissionError('Invalid batch token')
        job_path = STATE / 'jobs' / (pointer['job'] + '.json')
        job = read(job_path)
        batch = job.get('batch', {})
        expected = dict(job['post'])
        expected['streams'] = json.dumps(batch['ids'], separators=(',', ':'))
        lease = read(STATE / 'lease.json')
        if job['phase'] != 'reserved' or job.get('stop') or batch.get('token_hash') != digest or lease.get('job') != job['id'] or \
                not sid or hashlib.sha256(sid.encode()).hexdigest() != batch.get('session') or expected != post:
            raise PermissionError('Invalid or already dispatched batch')
        job['phase'] = 'dispatched'
        job['batch']['dispatched'] = int(time.time())
        write(job_path, job)
        return job['id'], digest


def acknowledge(job_id, digest, body, status):
    try:
        reply = json.loads(body)
        accepted = status == 200 and isinstance(reply, dict) and reply.get('status') in (1, 2) and reply.get('result') is not False
    except (ValueError, UnicodeDecodeError):
        accepted = False
    with locked():
        path = STATE / 'jobs' / (job_id + '.json')
        job = read(path)
        if job.get('batch', {}).get('token_hash') != digest:
            return
        job['batch']['accepted'] = accepted
        job['phase'] = 'waiting' if accepted else 'uncertain'
        if not accepted:
            job['message'] = 'O XUI não confirmou o lote. A fila vai verificar o estado sem repetir o envio.'
        write(path, job)


def record(kind, content):
    return struct.pack('!BBHHBB', 1, kind, 1, len(content), 0, 0) + content


def length(value):
    return bytes([value]) if value < 128 else struct.pack('!I', value | 0x80000000)


def exact(connection, size):
    value = b''
    while len(value) < size:
        chunk = connection.recv(size - len(value))
        if not chunk:
            raise ConnectionError('FastCGI closed before confirmation')
        value += chunk
    return value


def native(headers, uri, content_type, body):
    path = urlsplit(uri)
    params = {
        'GATEWAY_INTERFACE':'CGI/1.1', 'SERVER_SOFTWARE':'nginx', 'SERVER_PROTOCOL':'HTTP/1.1',
        'REQUEST_METHOD':'POST', 'SCRIPT_FILENAME':'/home/xui/admin/post.php', 'SCRIPT_NAME':path.path,
        'DOCUMENT_URI':path.path, 'DOCUMENT_ROOT':'/home/xui/admin', 'REQUEST_URI':uri,
        'QUERY_STRING':path.query, 'CONTENT_TYPE':content_type, 'CONTENT_LENGTH':str(len(body)),
        'REMOTE_ADDR':headers['X-XUI-Client-IP'], 'REMOTE_PORT':headers.get('X-XUI-Client-Port','0'),
        'SERVER_ADDR':headers.get('X-XUI-Server-IP','127.0.0.1'), 'SERVER_PORT':headers.get('X-XUI-Server-Port','80'),
        'SERVER_NAME':headers.get('Host','localhost'), 'REDIRECT_STATUS':'200',
    }
    if headers.get('X-XUI-Scheme') == 'https':
        params['HTTPS'] = 'on'
    for key, value in headers.items():
        if key.lower() not in ('connection','content-type','content-length','transfer-encoding') and not key.lower().startswith('x-xui-'):
            params['HTTP_' + key.upper().replace('-','_')] = value
    encoded = b''
    for key, value in params.items():
        key, value = key.encode(), str(value).encode()
        encoded += length(len(key)) + length(len(value)) + key + value
    output = bytearray()
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
        connection.settimeout(120)
        connection.connect(read(ROOT / 'config.json')['php_socket'])
        connection.sendall(record(1, struct.pack('!HB5x', 1, 0)))
        for start in range(0, len(encoded), 65535):
            connection.sendall(record(4, encoded[start:start+65535]))
        connection.sendall(record(4, b''))
        for start in range(0, len(body), 65535):
            connection.sendall(record(5, body[start:start+65535]))
        connection.sendall(record(5, b''))
        while True:
            version, kind, request_id, size, padding, _ = struct.unpack('!BBHHBB', exact(connection, 8))
            if version != 1 or request_id != 1:
                raise ValueError('Invalid FastCGI response')
            chunk = exact(connection, size)
            if padding:
                exact(connection, padding)
            if kind == 6:
                output.extend(chunk)
                if len(output) > 1048576:
                    raise ValueError('FastCGI response too large')
            if kind == 3:
                break
    header, separator, response = bytes(output).partition(b'\r\n\r\n')
    if not separator:
        header, separator, response = bytes(output).partition(b'\n\n')
    if not separator:
        raise ValueError('Missing FastCGI headers')
    status, returned = 200, []
    for line in header.decode('latin1').splitlines():
        key, _, value = line.partition(':')
        if key.lower() == 'status':
            status = int(value.strip().split()[0])
        elif key.lower() not in ('content-length','transfer-encoding','connection'):
            returned.append((key, value.strip()))
    return status, returned, response


slots = threading.BoundedSemaphore(4)


class Handler(BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.1'

    def respond(self, status, body, headers=()):
        self.send_response(status)
        self.send_header('X-XUI-Mass-Guard', '1')
        for key, value in headers:
            self.send_header(key, value)
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control','no-store')
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def do_GET(self):
        self.respond(200 if self.path == '/health' else 404, b'xui-mass-queue-ok' if self.path == '/health' else b'')

    def do_POST(self):
        if not slots.acquire(blocking=False):
            self.respond(503, b'{"status":0,"result":false}')
            return
        dispatch = None
        try:
            self.connection.settimeout(15)
            config = read(ROOT / 'config.json')
            uri = self.headers.get('X-XUI-Original-URI','')
            route = urlsplit(uri)
            if not config.get('enabled') or not route.path.endswith('/post.php') or parse_qs(route.query).get('action') != ['stream_mass'] or not self.headers.get('X-XUI-Client-IP'):
                raise PermissionError('Invalid internal route')
            size = int(self.headers.get('Content-Length','0'))
            if not 0 < size <= 8388608:
                raise ValueError('Invalid body size')
            body = self.rfile.read(size)
            if len(body) != size:
                raise ValueError('Incomplete body')
            content_type = self.headers.get('Content-Type','')
            post = post_fields(content_type, body)
            if post.get('c_server_tree') and post.get('server_type') == 'SET':
                dispatch = reserve(self.headers.get('X-XUI-Mass-Token',''), self.headers.get('Cookie',''), post)
            status, headers, response = native(self.headers, uri, content_type, body)
            if dispatch:
                acknowledge(*dispatch, response, status)
            self.respond(status, response, headers)
        except PermissionError:
            self.respond(403, '{"status":0,"result":false,"message":"Atualize o Mass Edit para usar a fila de troca."}'.encode(), [('Content-Type','application/json')])
        except Exception as error:
            if dispatch:
                try:
                    acknowledge(*dispatch, b'', 502)
                except Exception:
                    pass
            print('mass queue proxy:', type(error).__name__, flush=True)
            self.respond(502, b'{"status":0,"result":false,"message":"O lote perdeu a confirmacao; consulte a fila."}', [('Content-Type','application/json')])
        finally:
            slots.release()

    def log_message(self, *_):
        pass


class Server(ThreadingHTTPServer):
    daemon_threads = True
    request_queue_size = 32


if __name__ == '__main__':
    Server(('127.0.0.1',19462),Handler).serve_forever()
