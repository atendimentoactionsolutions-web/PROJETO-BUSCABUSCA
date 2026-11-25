#!/usr/bin/env python3
"""Read-only catalog preview: no imports from the application or WhatsApp motor."""
import argparse
import json
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

WEB_DIR = Path(__file__).resolve().parents[1] / 'web'

class PreviewHandler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(WEB_DIR), **kwargs)

    def do_GET(self):
        path = urlparse(self.path).path
        if path == '/api/products':
            products = []
            prod_file = WEB_DIR.parent / 'produtos_dia.json'
            if prod_file.exists():
                try:
                    data = json.loads(prod_file.read_text(encoding='utf-8-sig'))
                    if isinstance(data, list):
                        products = data
                except Exception:
                    products = []
            body = json.dumps({'products': products, 'preview': True}, ensure_ascii=False).encode('utf-8')
            self.send_response(200)
            self.send_header('Content-Type', 'application/json; charset=utf-8')
            self.send_header('Cache-Control', 'no-store')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if path == '/admin':
            body = '<!doctype html><html lang="pt-BR"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Prévia local</title><body style="font-family:system-ui;max-width:640px;margin:80px auto;padding:20px;"><h1>Prévia local do catálogo</h1><p>Esta prévia é somente leitura. Não conecta o WhatsApp nem altera os dados do sistema.</p><p><a href="/">Voltar ao catálogo</a></p><p><a href="https://buscabusca.srv2058756.hstgr.cloud/admin" target="_blank" rel="noopener">Abrir a central na VPS ↗</a></p></body></html>'.encode()
            self.send_response(200)
            self.send_header('Content-Type', 'text/html; charset=utf-8')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        super().do_GET()

    def do_POST(self):
        self.send_error(405, 'Prévia somente leitura')

    def end_headers(self):
        self.send_header('X-Content-Type-Options', 'nosniff')
        super().end_headers()

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port', type=int, default=3054)
    args = parser.parse_args()
    server = ThreadingHTTPServer(('127.0.0.1', args.port), PreviewHandler)
    print(f'Prévia local: http://localhost:{args.port}/ — catálogo vazio, sem WhatsApp.', flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        server.server_close()
