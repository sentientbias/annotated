"""Local forward proxy: Chrome -> 127.0.0.1:8899 -> upstream egress proxy (with preemptive Basic auth)."""
import socket, threading, base64

UPSTREAM_HOST = 'proxy.example.com'  # set from env PROXY_HOST
UPSTREAM_PORT = 8080                 # set from env PROXY_PORT
PROXY_USER = ''                      # set from env PROXY_USER
PROXY_PASS = ''                      # set from env PROXY_PASS
LISTEN_PORT = 8899

import os, urllib.parse
_hp = os.environ.get('https_proxy') or os.environ.get('HTTPS_PROXY', '')
_parsed = urllib.parse.urlparse(_hp)
UPSTREAM_HOST = _parsed.hostname or 'hatch-egress-proxy'
UPSTREAM_PORT = _parsed.port or 3128
PROXY_USER = urllib.parse.unquote(_parsed.username or '')
PROXY_PASS = urllib.parse.unquote(_parsed.password or '')
LISTEN_PORT = 8899

AUTH = base64.b64encode(f'{PROXY_USER}:{PROXY_PASS}'.encode()).decode()

def pipe(src, dst):
    try:
        while True:
            d = src.recv(65536)
            if not d:
                break
            dst.sendall(d)
    except OSError:
        pass

def handle(client):
    try:
        req = b''
        while b'\r\n\r\n' not in req:
            chunk = client.recv(4096)
            if not chunk:
                client.close(); return
            req += chunk
        line = req.split(b'\r\n', 1)[0].decode('latin1')
        parts = line.split(' ')
        if len(parts) < 2 or parts[0] != 'CONNECT':
            client.close(); return
        target = parts[1]
        up = socket.create_connection((UPSTREAM_HOST, UPSTREAM_PORT), timeout=20)
        up.sendall(f'CONNECT {target} HTTP/1.1\r\nHost: {target}\r\n'
                    f'Proxy-Authorization: Basic {AUTH}\r\n\r\n'.encode())
        resp = b''
        while b'\r\n\r\n' not in resp:
            chunk = up.recv(4096)
            if not chunk:
                break
            resp += chunk
        if b' 200' not in resp.split(b'\r\n', 1)[0]:
            client.close(); up.close(); return
        client.sendall(b'HTTP/1.1 200 Connection Established\r\n\r\n')
        t = threading.Thread(target=pipe, args=(up, client), daemon=True)
        t.start()
        pipe(client, up)
        t.join(timeout=1)
    except OSError:
        pass
    finally:
        try: client.close()
        except OSError: pass

def main():
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(('127.0.0.1', LISTEN_PORT))
    srv.listen(200)
    print(f'forward proxy on 127.0.0.1:{LISTEN_PORT} -> {UPSTREAM_HOST}:{UPSTREAM_PORT}', flush=True)
    while True:
        c, _ = srv.accept()
        threading.Thread(target=handle, args=(c,), daemon=True).start()

if __name__ == '__main__':
    main()
