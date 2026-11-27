#!/usr/bin/env python3
"""Read-only catalog preview: no imports from the application or WhatsApp motor."""
import argparse
import json
import time
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

WEB_DIR = Path(__file__).resolve().parents[1] / 'web'

class PreviewHandler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(WEB_DIR), **kwargs)

    def send_json(self, value):
        body = json.dumps(value, ensure_ascii=False).encode('utf-8')
        self.send_response(200)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Cache-Control', 'no-store')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        path = urlparse(self.path).path
        if path in ('/admin', '/admin.html'):
            html = (WEB_DIR / 'admin.html').read_text(encoding='utf-8')
            notice = (
                '<div style="padding:12px 20px;text-align:center;color:#684e08;'
                'background:#fff4d6;font:600 13px system-ui">'
                'PRÉVIA LOCAL — dados fictícios; sem conexão com contas, WhatsApp ou VPS. Gravações estão bloqueadas.'
                '</div>'
            )
            body = html.replace('<body>', f'<body>{notice}', 1).encode('utf-8')
            self.send_response(200)
            self.send_header('Content-Type', 'text/html; charset=utf-8')
            self.send_header('Cache-Control', 'no-store')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if path == '/api/accounts':
            return self.send_json({
                'ok': True,
                'accounts': [
                    {'username': 'cledson', 'name': 'Administrador', 'role': 'master', 'active': True,
                     'status': 'ativo', 'is_master': True, 'dfu_enabled': False, 'expires_at': None,
                     'last_login_at': '2026-11-27 12:40:00'},
                    {'username': 'cliente_demo', 'name': 'Loja Exemplo', 'role': 'user', 'active': True,
                     'status': 'ativo', 'is_master': False, 'dfu_enabled': False, 'expires_at': '2026-12-31',
                     'last_login_at': '2026-11-27 11:15:00'},
                ],
            })
        if path == '/api/panel-state':
            now_ms = int(time.time() * 1000)
            now_text = time.strftime('%Y-%m-%dT%H:%M:%S%z')
            return self.send_json({
                'updatedAt': now_text,
                'whatsapp': {
                    'status': 'CONNECTED',
                    'lastReceivedAt': now_ms - 45_000,
                    'pendingInvites': 1,
                    'capture': {
                        'pending': 0,
                        'failed': 0,
                        'lastProcessedAt': now_ms - 90_000,
                        'lastError': None,
                    },
                },
                'productsTodayCount': 128,
                'groups': [
                    {'id': 'grupo-demo@g.us', 'subject': 'Comunidade Fornecedores (exemplo)', 'size': 42,
                     'blacklisted': False, 'monitored': True, 'decision': {'allowed': True}},
                    {'id': 'grupo-pendente@g.us', 'subject': 'Grupo para revisar (exemplo)', 'size': 18,
                     'blacklisted': False, 'monitored': False, 'decision': {'allowed': False}},
                ],
                'groupsCheckedAt': now_text,
                'groupsError': None,
                'coverageAudit': {
                    'status': 'ALERTA',
                    'participatingGroups': 2,
                    'unmonitoredGroupsCount': 1,
                    'issues': ['1 comunidade requer confirmação de monitoramento'],
                },
                'robots': {
                    'colors': {'label': 'Guardião de cores + IA', 'intervalLabel': '3 min', 'status': 'OK',
                               'lastRun': now_text, 'detail': '128 ofertas verificadas'},
                    'price': {'label': 'Vistoria de preços e catálogo', 'intervalLabel': '5 min', 'status': 'OK',
                              'lastRun': now_text, 'detail': '128 verificados · 128 válidos'},
                    'catalog': {'label': 'Monitor de integridade do catálogo', 'intervalLabel': '5 min', 'status': 'OK',
                                'lastRun': now_text, 'detail': 'Sem divergências'},
                    'daily': {'label': 'Revisão e releitura de listas', 'intervalLabel': '10 min', 'status': 'EXECUTANDO',
                              'lastRun': now_text, 'detail': 'Revisão em andamento'},
                    'coverage': {'label': 'Auditor de comunidades e cobertura', 'intervalLabel': '10 min', 'status': 'ALERTA',
                                 'lastRun': now_text, 'detail': '1 comunidade requer confirmação'},
                },
            })
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
        if path == '/admin-preview':
            body = '<!doctype html><html lang="pt-BR"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Prévia local</title><body style="font-family:system-ui;max-width:640px;margin:80px auto;padding:20px;"><h1>Prévia local</h1><p>Esta prévia usa dados simulados. Não conecta o WhatsApp nem altera dados.</p><p><a href="/">Voltar ao catálogo</a></p><p><a href="/admin">Abrir a prévia da Central</a></p></body></html>'.encode()
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
