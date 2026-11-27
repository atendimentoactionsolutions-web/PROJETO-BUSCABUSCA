import http.client
import json
import threading
import time
import unittest
from http.server import ThreadingHTTPServer
from unittest.mock import patch

import painel


class AdminMonitoringTests(unittest.TestCase):
    def setUp(self):
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), painel.PanelHandler)
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)

    def request(self, method, path, body=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        payload = json.dumps(body).encode("utf-8") if body is not None else None
        headers = {"Host": f"127.0.0.1:{self.port}"}
        if payload is not None:
            headers["Content-Type"] = "application/json"
        conn.request(method, path, body=payload, headers=headers)
        response = conn.getresponse()
        result = response.read().decode("utf-8")
        status = response.status
        conn.close()
        return status, result

    def test_panel_state_contains_only_live_monitoring_data(self):
        state = {
            "status": "CONNECTED",
            "capture": {"pending": 2, "failed": 1},
            "lastReceivedAt": 1_800_000_000_000,
        }
        groups = [{"id": "supplier@g.us", "subject": "Fornecedor", "monitored": True}]
        with patch.object(painel, "bridge_get", return_value=state), \
                patch.object(painel, "panel_groups_snapshot", return_value=(groups, "2026-11-27T12:00:00-0300", None)), \
                patch.object(painel, "load_produtos", return_value=[{}, {}]), \
                patch.object(painel, "load_fornecedores", side_effect=AssertionError("supplier registry should not be loaded")), \
                patch.object(painel, "robot_dashboard_state", return_value={"coverage": {"status": "OK"}}):
            status, body = self.request("GET", "/api/panel-state")

        self.assertEqual(status, 200)
        payload = json.loads(body)
        self.assertEqual(payload["whatsapp"]["capture"]["pending"], 2)
        self.assertEqual(payload["productsTodayCount"], 2)
        self.assertEqual(payload["groups"], groups)
        self.assertEqual(payload["robots"]["coverage"]["status"], "OK")
        self.assertFalse({"config", "fornecedores", "historico", "campaign", "logs"} & payload.keys())

    def test_outbound_and_campaign_endpoints_are_removed(self):
        removed = (
            "/api/send",
            "/api/send-single",
            "/api/start-campaign",
            "/api/stop-campaign",
            "/api/save-config",
            "/api/supplier-messaging",
            "/api/internal/message-ack",
            "/api/internal/supplier-reply",
        )
        for endpoint in removed:
            with self.subTest(endpoint=endpoint):
                status, _ = self.request("POST", endpoint, {})
                self.assertEqual(status, 404)

    def test_admin_page_and_assets_are_served(self):
        with patch.object(painel.PanelHandler, "_current_user", return_value={"role": "master"}):
            status, html = self.request("GET", "/admin")
        self.assertEqual(status, 200)
        self.assertIn('href="/admin.css"', html)
        self.assertIn('src="/admin.js"', html)
        for asset in ("/admin.css", "/admin.js"):
            with self.subTest(asset=asset):
                status, body = self.request("GET", asset)
                self.assertEqual(status, 200)
                self.assertTrue(body)

    def test_robot_dashboard_surfaces_worker_errors(self):
        with patch.dict(painel._robot_health, {}, clear=True):
            painel.record_robot_health("colors", error=RuntimeError("audit failed"))
            robot = painel.robot_dashboard_state()["colors"]
        self.assertEqual(robot["status"], "ERRO")
        self.assertIn("audit failed", robot["lastError"])

    def test_group_list_is_cached_and_refresh_errors_are_explicit(self):
        groups = [{"id": "supplier@g.us", "monitored": True}]
        with patch.dict(painel._panel_groups_cache, {"at": 0.0, "groups": []}), \
                patch.object(painel, "bridge_get", return_value={"ok": True, "groups": groups}) as bridge:
            first = painel.panel_groups_snapshot()
            second = painel.panel_groups_snapshot()
        self.assertEqual(first[0], groups)
        self.assertEqual(second[0], groups)
        bridge.assert_called_once_with("/api/groups", timeout=25)

        old_cache = {"at": time.time() - 120, "groups": groups}
        with patch.dict(painel._panel_groups_cache, old_cache), \
                patch.object(painel, "bridge_get", side_effect=TimeoutError("groups timed out")):
            stale_groups, checked_at, error = painel.panel_groups_snapshot()
        self.assertEqual(stale_groups, groups)
        self.assertIsNotNone(checked_at)
        self.assertIn("groups timed out", error)

    def test_stalled_robot_is_reported_as_late(self):
        with patch.dict(painel._robot_health, {
            "colors": {
                "status": "EXECUTANDO",
                "inProgress": True,
                "startedAtEpoch": time.time() - 361,
            },
        }, clear=True):
            robot = painel.robot_dashboard_state()["colors"]
        self.assertEqual(robot["status"], "ATRASADO")
