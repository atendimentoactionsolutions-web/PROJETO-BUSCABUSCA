import tempfile
import time
import unittest

import coverage_guardian


class CoverageGuardianTests(unittest.TestCase):
    def setUp(self):
        coverage_guardian._last_recovery_ts = 0.0

    def test_100_percent_monitored_returns_ok_without_recovery(self):
        with tempfile.TemporaryDirectory() as tmp:
            now_ms = time.time() * 1000.0
            report = coverage_guardian.evaluate_coverage(
                base_dir=tmp,
                today_str="25/11/2026",
                now_hour=11,
                wa_status={
                    "status": "CONNECTED",
                    "communitiesCount": 2,
                    "lastReceivedAt": now_ms - 120_000,
                    "capture": {"pending": 0, "failed": 0, "lastError": None},
                },
                participating_groups=[
                    {"id": "120363001@g.us", "subject": "ATACA APPLE", "isCommunity": True, "decision": {"allowed": True}},
                    {"id": "120363002@g.us", "subject": "FORNECEDOR VIP", "isCommunity": False, "decision": {"allowed": True}},
                    {"id": "120363999@g.us", "subject": "FAMILIA PESSOAL", "isCommunity": False, "decision": {"allowed": False}},
                    {"id": "120363888@g.us", "subject": "iFindz Bloqueado", "isCommunity": True, "blacklisted": True, "decision": {"allowed": False}},
                ],
                monitored_list=[
                    {"jid": "120363001@g.us", "name": "ATACA APPLE"},
                    {"jid": "120363002@g.us", "name": "FORNECEDOR VIP"},
                    {"jid": "120363555@newsletter", "name": "CAPITAO CEL"},
                    {"jid": "5511999990001@s.whatsapp.net", "name": "FORN A"},
                ],
                produtos_today=[
                    {"fornecedor": "FORN A", "origem": "ATACA APPLE", "telefone": "5511999990001", "data": "25/11/2026"},
                ],
                fornecedores=[
                    {"nome": "FORN A", "telefone": "5511999990001"},
                ],
                yesterday_suppliers={"FORN A"},
                raw_channels_today={"ATACA APPLE"},
            )
            self.assertEqual(report["status"], "OK")
            self.assertFalse(report["needsRecovery"])
            self.assertEqual(report["unmonitoredGroupsCount"], 0)
            self.assertEqual(report["monitoredGroups"], 2)
            self.assertEqual(report["monitoredChannels"], 1)
            self.assertEqual(report["monitoredPrivates"], 1)
            self.assertEqual(report["registeredActiveToday"], 1)
            self.assertEqual(report["coverageVsYesterdayPct"], 100.0)

    def test_detects_unmonitored_group_or_community_and_triggers_recovery_with_cooldown(self):
        with tempfile.TemporaryDirectory() as tmp:
            now_ms = time.time() * 1000.0
            posts = []

            def fake_get(path):
                if "status" in path:
                    return {
                        "status": "CONNECTED",
                        "communitiesCount": 2,
                        "lastReceivedAt": now_ms - 60_000,
                        "capture": {"pending": 0, "failed": 0},
                    }
                if "groups" in path:
                    return {
                        "ok": True,
                        "groups": [
                            {"id": "120363001@g.us", "subject": "COMUNIDADE NOVA", "isCommunity": True, "decision": {"allowed": False}},
                            {"id": "120363002@g.us", "subject": "GRUPO PESSOAL", "isCommunity": False, "decision": {"allowed": False}},
                            {"id": "120363003@g.us", "subject": "FORNECEDOR ESQUECIDO", "isCommunity": False, "decision": {"allowed": True}},
                        ],
                    }
                return {}

            def fake_post(path, payload):
                posts.append((path, payload))
                return {"ok": True, "monitoredCount": 3, "historyRequestsSent": 2}

            r1 = coverage_guardian.run_coverage_audit(
                base_dir=tmp,
                bridge_get_fn=fake_get,
                bridge_post_fn=fake_post,
                produtos_today=[],
                fornecedores=[],
                allow_recovery=True,
            )
            self.assertEqual(r1["status"], "ALERTA")
            self.assertEqual(r1["unmonitoredGroupsCount"], 2)
            self.assertTrue(r1["needsRecovery"])
            self.assertTrue(r1["recoveryTriggered"])
            self.assertEqual(len(posts), 1)
            self.assertEqual(posts[0][0], "/api/scan-archived")

            # Segunda chamada imediata respeita o cooldown de 30 min e não dispara scan repetido
            r2 = coverage_guardian.run_coverage_audit(
                base_dir=tmp,
                bridge_get_fn=fake_get,
                bridge_post_fn=fake_post,
                produtos_today=[],
                fornecedores=[],
                allow_recovery=True,
            )
            self.assertTrue(r2["needsRecovery"])
            self.assertFalse(r2["recoveryTriggered"])
            self.assertEqual(len(posts), 1)

    def test_detects_business_hours_silence_and_capture_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            old_ms = (time.time() - 40 * 60) * 1000.0
            report = coverage_guardian.evaluate_coverage(
                base_dir=tmp,
                today_str="25/11/2026",
                now_hour=10,
                wa_status={
                    "status": "CONNECTED",
                    "lastReceivedAt": old_ms,
                    "capture": {"pending": 2, "failed": 1, "lastError": "timeout"},
                },
                participating_groups=[],
                monitored_list=[],
                produtos_today=[],
                fornecedores=[],
                yesterday_suppliers=set(),
                raw_channels_today=set(),
            )
            self.assertEqual(report["status"], "ALERTA")
            self.assertTrue(report["needsRecovery"])
            self.assertGreaterEqual(len(report["issues"]), 2)


if __name__ == "__main__":
    unittest.main()
