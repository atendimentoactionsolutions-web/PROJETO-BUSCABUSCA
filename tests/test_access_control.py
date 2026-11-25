import gzip
import http.client
import json
import os
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer
from unittest.mock import patch

import access_control
import painel
from price_guardian import compute_dfu_delta


class AccessControlTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.db_dir = os.path.join(self.folder.name, "db_isolado")
        self.prod_file = os.path.join(self.folder.name, "produtos_dia.json")
        self.patches = [
            patch.dict(os.environ, {"OFORNECEDOR_DB_DIR": self.db_dir}),
            patch.object(access_control, "LEGACY_ACCOUNTS_FILE", os.path.join(self.folder.name, "legacy_none.json")),
            patch.object(access_control, "LEGACY_SECRET_FILE", os.path.join(self.folder.name, "legacy_sec_none.txt")),
            patch.object(access_control, "_secret_cache", None),
            patch.object(painel, "PRODUTOS_FILE", self.prod_file),
        ]
        for p in self.patches:
            p.start()

    def test_sqlite_persistence_survives_restart_and_never_overwrites_passwords(self):
        access_control.init_db()
        access_control.create_account("cliente1", "abc123", name="Cliente Um", expires_at="2099-05-01")
        access_control.change_account_password("cledson", "nova_master_99")
        # Simula novo deploy / reinício completo do processo (init_db + load_accounts novamente)
        access_control._secret_cache = None
        access_control.init_db()
        accounts = {a["username"]: a for a in access_control.list_accounts()}
        self.assertIn("cliente1", accounts)
        self.assertEqual(accounts["cliente1"]["expires_at"], "2099-05-01")
        # Senha alterada do master permanece preservada (não volta para "cledson")
        self.assertFalse(access_control.authenticate("cledson", "cledson")[0])
        self.assertTrue(access_control.authenticate("cledson", "nova_master_99")[0])
        # Backup automático foi gerado na pasta isolada
        info = access_control.db_info()
        self.assertEqual(info["engine"], "sqlite3")
        self.assertTrue(os.path.exists(info["path"]))
        self.assertGreaterEqual(info["backupsCount"], 2)

    def tearDown(self):
        for p in self.patches:
            p.stop()
        self.folder.cleanup()

    def test_defaults_and_exclusive_dfu_rule(self):
        accounts = {a["username"]: a for a in access_control.list_accounts()}
        self.assertIn("cledson", accounts)
        self.assertIn("admin", accounts)
        self.assertEqual(accounts["cledson"]["role"], "master")
        self.assertFalse(accounts["cledson"]["dfu_enabled"])
        self.assertEqual(accounts["admin"]["role"], "user")
        self.assertTrue(accounts["admin"]["dfu_enabled"])

        # Novo login criado sempre entra SEM DFU e sem permissão master
        client = access_control.create_account("joao", "1234", name="Loja do João", expires_at="2099-12-31")
        self.assertEqual(client["role"], "user")
        self.assertFalse(client["dfu_enabled"])
        self.assertEqual(client["status"], "ativo")

        # Pricing: ONLY admin recebe DFU; cledson e joao recebem preço puro
        sample = [
            {"modelo": "IPHONE 17 PRO MAX", "categoria": "iPhones", "condicao": "Novo / Lacrado", "preco": 7500},
            {"modelo": "MACBOOK AIR M5 13\"", "categoria": "MacBook & iMac", "condicao": "Novo / Lacrado", "preco": 8000},
            {"modelo": "IPHONE 15", "categoria": "iPhones", "condicao": "Seminovo", "preco": 2500},
        ]
        p_admin = access_control.apply_user_pricing(sample, True, compute_dfu_delta)
        p_cledson = access_control.apply_user_pricing(sample, False, compute_dfu_delta)
        self.assertEqual([x["preco"] for x in p_admin], [7600.0, 8200.0, 2500.0])
        self.assertEqual([x["preco"] for x in p_cledson], [7500, 8000, 2500])

    def test_pause_password_change_expiry_and_delete(self):
        access_control.create_account("maria", "senha1", name="Maria", expires_at="2099-12-31")
        ok, _, _, tok1 = access_control.authenticate("maria", "senha1")
        ok2, _, _, tok2 = access_control.authenticate("maria", "senha1")
        self.assertTrue(ok and ok2)
        # Múltiplas sessões simultâneas funcionam (sem trava de tela)
        self.assertIsNotNone(access_control.verify_session_token(tok1))
        self.assertIsNotNone(access_control.verify_session_token(tok2))

        # Pausar corta sessões imediatamente e impede novo login
        access_control.set_account_active("maria", False)
        self.assertIsNone(access_control.verify_session_token(tok1))
        ok_p, err_p, _, _ = access_control.authenticate("maria", "senha1")
        self.assertFalse(ok_p)
        self.assertIn("pausada", err_p.lower())

        # Reativar e trocar senha invalida token antigo
        access_control.set_account_active("maria", True)
        access_control.change_account_password("maria", "nova99")
        self.assertIsNone(access_control.verify_session_token(tok1))
        ok_old, _, _, _ = access_control.authenticate("maria", "senha1")
        self.assertFalse(ok_old)
        ok_new, _, _, tok3 = access_control.authenticate("maria", "nova99")
        self.assertTrue(ok_new)
        self.assertIsNotNone(access_control.verify_session_token(tok3))

        # Vencimento no passado bloqueia automaticamente
        access_control.update_account_expiry("maria", "2020-01-01")
        self.assertIsNone(access_control.verify_session_token(tok3))
        ok_exp, err_exp, _, _ = access_control.authenticate("maria", "nova99")
        self.assertFalse(ok_exp)
        self.assertIn("vencido", err_exp.lower())

        # Remover cliente funciona; remover master ou admin exclusivo é protegido
        self.assertTrue(access_control.delete_account("maria"))
        with self.assertRaises(ValueError):
            access_control.delete_account("cledson")
        with self.assertRaises(ValueError):
            access_control.delete_account("admin")

    def test_http_login_routes_role_isolation_and_per_user_dfu(self):
        today = painel.get_today_str()
        painel.save_produtos([
            dict(modelo="IPHONE 17 PRO MAX", categoria="iPhones", capacidade="256GB", cor="LARANJA",
                 preco=7500, condicao="Novo / Lacrado", fornecedor="F1", telefone="5511999990001",
                 data=today, hora="10:00:00"),
        ])
        server = ThreadingHTTPServer(("127.0.0.1", 0), painel.PanelHandler)
        port = server.server_address[1]
        t = threading.Thread(target=server.serve_forever, daemon=True)
        t.start()

        ext_headers = {"Host": "buscabusca.srv2058756.hstgr.cloud", "X-Forwarded-For": "203.0.113.10"}

        def req(method, path, body=None, headers=None):
            conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
            h = dict(headers or {})
            payload = None
            if body is not None:
                payload = json.dumps(body).encode("utf-8")
                h["Content-Type"] = "application/json"
            conn.request(method, path, body=payload, headers=h)
            resp = conn.getresponse()
            raw = resp.read()
            hdrs = dict(resp.getheaders())
            if hdrs.get("Content-Encoding") == "gzip":
                raw = gzip.decompress(raw)
            data = raw.decode("utf-8")
            conn.close()
            return resp.status, hdrs, data

        try:
            with patch.object(painel, "bridge_status_lite", return_value={"status": "CONNECTED", "communitiesCount": 10}):
                # 1. Visitante externo sem login -> redireciona para /login e bloqueia /api/products com 401
                st, hdrs, _ = req("GET", "/", headers=ext_headers)
                self.assertEqual(st, 302)
                self.assertEqual(hdrs.get("Location"), "/login")
                st_api, _, _ = req("GET", "/api/products", headers=ext_headers)
                self.assertEqual(st_api, 401)

                # 2. Chamada local direta 127.0.0.1 (sem proxy, ex: integração VPS) continua 200 com preço base
                st_loc, _, raw_loc = req("GET", "/api/products", headers={"Host": f"127.0.0.1:{port}"})
                self.assertEqual(st_loc, 200)
                self.assertEqual(json.loads(raw_loc)["products"][0]["preco"], 7500)

                # 3. Login como `admin` -> vê COM DFU (+100 = 7600) e NÃO acessa /admin nem /api/accounts
                st_la, hdrs_la, _ = req("POST", "/api/auth/login", {"username": "admin", "password": "admin"}, headers=ext_headers)
                self.assertEqual(st_la, 200)
                cookie_admin = hdrs_la.get("Set-Cookie", "").split(";", 1)[0]
                st_pa, _, raw_pa = req("GET", "/api/products", headers={**ext_headers, "Cookie": cookie_admin})
                self.assertEqual(st_pa, 200)
                data_pa = json.loads(raw_pa)
                self.assertTrue(data_pa["dfuApplied"])
                self.assertEqual(data_pa["products"][0]["preco"], 7600.0)
                st_adm_deny, hdrs_adm_deny, _ = req("GET", "/admin", headers={**ext_headers, "Cookie": cookie_admin})
                self.assertEqual(st_adm_deny, 302)
                self.assertEqual(hdrs_adm_deny.get("Location"), "/")
                st_acc_deny, _, _ = req("GET", "/api/accounts", headers={**ext_headers, "Cookie": cookie_admin})
                self.assertEqual(st_acc_deny, 403)

                # 4. Login como `cledson` (master) -> vê SEM DFU (7500), acessa /admin e cria cliente `lucas`
                st_lm, hdrs_lm, _ = req("POST", "/api/auth/login", {"username": "cledson", "password": "cledson"}, headers=ext_headers)
                self.assertEqual(st_lm, 200)
                cookie_master = hdrs_lm.get("Set-Cookie", "").split(";", 1)[0]
                st_pm, _, raw_pm = req("GET", "/api/products", headers={**ext_headers, "Cookie": cookie_master})
                self.assertEqual(st_pm, 200)
                self.assertEqual(json.loads(raw_pm)["products"][0]["preco"], 7500)
                st_adm_ok, _, _ = req("GET", "/admin", headers={**ext_headers, "Cookie": cookie_master})
                self.assertEqual(st_adm_ok, 200)
                st_cr, _, raw_cr = req("POST", "/api/accounts/create",
                                       {"username": "lucas", "password": "123", "name": "Lucas"},
                                       headers={**ext_headers, "Cookie": cookie_master})
                self.assertEqual(st_cr, 200, raw_cr)

                # 5. Cliente `lucas` loga -> vê SEM DFU (7500) e NÃO acessa /admin
                st_lc, hdrs_lc, _ = req("POST", "/api/auth/login", {"username": "lucas", "password": "123"}, headers=ext_headers)
                self.assertEqual(st_lc, 200)
                cookie_lucas = hdrs_lc.get("Set-Cookie", "").split(";", 1)[0]
                st_pc, _, raw_pc = req("GET", "/api/products", headers={**ext_headers, "Cookie": cookie_lucas})
                self.assertEqual(st_pc, 200)
                self.assertEqual(json.loads(raw_pc)["products"][0]["preco"], 7500)
                st_c_adm, _, _ = req("GET", "/admin", headers={**ext_headers, "Cookie": cookie_lucas})
                self.assertEqual(st_c_adm, 302)

                # 6. Cache ETag (304 Not Modified), Gzip e invalidação instantânea ao atualizar preço
                st_g, hdrs_g, _ = req("GET", "/api/products", headers={**ext_headers, "Cookie": cookie_lucas, "Accept-Encoding": "gzip"})
                self.assertEqual(st_g, 200)
                self.assertEqual(hdrs_g.get("Content-Encoding"), "gzip")
                etag1 = hdrs_g.get("ETag")
                self.assertTrue(etag1)
                st_304, _, _ = req("GET", "/api/products", headers={**ext_headers, "Cookie": cookie_lucas, "If-None-Match": etag1})
                self.assertEqual(st_304, 304)
                # Atualiza preço -> invalida cache na hora e devolve novo valor para lucas (7400) e admin (7500)
                painel.save_produtos([
                    dict(modelo="IPHONE 17 PRO MAX", categoria="iPhones", capacidade="256GB", cor="LARANJA",
                         preco=7400, condicao="Novo / Lacrado", fornecedor="F1", telefone="5511999990001",
                         data=today, hora="10:05:00"),
                ])
                st_new_l, hdrs_new_l, raw_new_l = req("GET", "/api/products", headers={**ext_headers, "Cookie": cookie_lucas, "If-None-Match": etag1})
                self.assertEqual(st_new_l, 200)
                self.assertNotEqual(hdrs_new_l.get("ETag"), etag1)
                self.assertEqual(json.loads(raw_new_l)["products"][0]["preco"], 7400)
                st_new_a, _, raw_new_a = req("GET", "/api/products", headers={**ext_headers, "Cookie": cookie_admin})
                self.assertEqual(st_new_a, 200)
                self.assertEqual(json.loads(raw_new_a)["products"][0]["preco"], 7500.0)
        finally:
            server.shutdown()
            server.server_close()


if __name__ == "__main__":
    unittest.main()
