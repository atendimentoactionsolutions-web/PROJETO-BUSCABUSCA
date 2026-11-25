import unittest
from unittest.mock import patch

import color_guardian
from parser_listas import extract_color
from price_guardian import validate_product


class ColorGuardianTests(unittest.TestCase):
    def test_extract_color_ignores_watch_band_and_reads_titanium_case(self):
        self.assertEqual(extract_color("BLACK TI BURGUNDY ALP LOOP $5.350,00"), "PRETO")
        self.assertEqual(extract_color("CAIXA PRETA (BURGUNDY TRAIL LOOP M/L) R$5.300,00"), "PRETO")
        self.assertEqual(extract_color("NATURAL TI ORANGE OCEAN BAND $5.600"), "NATURAL TITÂNIO")
        self.assertEqual(extract_color("BURGUNDY ALP LOOP $5.350,00"), "-")

    def test_watch_ultra_4_rejects_burgundy_and_heals_black_titanium_case(self):
        # Caso real da ZOI CELL: caixa BLACK TI com pulseira BURGUNDY ALP LOOP -> auto-corrigido para PRETO
        zoi = {
            "modelo": "APPLE WATCH ULTRA 4 49MM GPS + CELLULAR",
            "capacidade": "49MM",
            "cor": "BURGUNDY",
            "preco": 5350.0,
            "condicao": "Novo / Lacrado",
            "fornecedor": "ZOI CELL 9959",
            "data": "25/11/2026",
            "hora": "11:00:00",
            "linhaOriginal": "APPLE WATCH ULTRA 4 49MM GPS + CELLULAR - (BURGUNDY) - BLACK TI BURGUNDY ALP LOOP $5.350,00",
        }
        ok, reason, healed = validate_product(dict(zoi), allow_seminovo=True, log_anomalies=False)
        self.assertTrue(ok, reason)
        self.assertEqual(healed["cor"], "PRETO")

        # Sem indicação de caixa oficial (apenas BURGUNDY) -> bloqueado!
        bad_ultra = {
            "modelo": "APPLE WATCH ULTRA 4 49MM GPS + CELLULAR",
            "capacidade": "49MM",
            "cor": "BURGUNDY",
            "preco": 5450.0,
            "condicao": "Novo / Lacrado",
            "fornecedor": "LOJA X",
            "data": "25/11/2026",
            "hora": "11:00:00",
            "linhaOriginal": "APPLE WATCH ULTRA 4 49MM - BURGUNDY $5.450",
        }
        ok_bad, reason_bad, _ = validate_product(dict(bad_ultra), allow_seminovo=True, log_anomalies=False)
        self.assertFalse(ok_bad)
        self.assertIn("COR_INEXISTENTE_NO_MODELO", reason_bad)

    def test_watch_s12_and_se3_enforce_official_colors_only(self):
        # S12 GPS (Alumínio): SOMENTE as 4 cores oficiais (Black, Space Gray, Dark Bronze, Light Gold)
        for valid_c in ("PRETO", "CINZA ESPACIAL", "BRONZE", "DOURADO"):
            ok, reason, _ = validate_product(
                {"modelo": "APPLE WATCH S12 46MM GPS", "capacidade": "46MM", "cor": valid_c, "preco": 3100, "condicao": "Novo / Lacrado"},
                allow_seminovo=True, log_anomalies=False,
            )
            self.assertTrue(ok, f"S12 GPS deveria aceitar {valid_c}: {reason}")
        for bad_c in ("BRANCO", "AZUL", "NATURAL TITÂNIO", "ROSA", "PRATA", "BURGUNDY", "VERMELHO"):
            ok, _, _ = validate_product(
                {"modelo": "APPLE WATCH S12 42MM GPS", "capacidade": "42MM", "cor": bad_c, "preco": 2700, "condicao": "Novo / Lacrado"},
                allow_seminovo=True, log_anomalies=False,
            )
            self.assertFalse(ok, f"S12 GPS não deve aceitar {bad_c}")

        # Caso real KING SHOP: "WHITE GOLD $ 2.690,00" -> auto-corrigido de BRANCO para DOURADO
        self.assertEqual(extract_color("WHITE GOLD $ 2.690,00"), "DOURADO")
        ok_wg, reason_wg, healed_wg = validate_product(
            {"modelo": "APPLE WATCH S12 42MM GPS", "capacidade": "42MM", "cor": "BRANCO", "preco": 2690,
             "condicao": "Novo / Lacrado", "linhaOriginal": "APPLE WATCH S12 42MM GPS - (BRANCO) - WHITE GOLD $ 2.690,00"},
            allow_seminovo=True, log_anomalies=False,
        )
        self.assertTrue(ok_wg, reason_wg)
        self.assertEqual(healed_wg["cor"], "DOURADO")

        # S12 GPS + Cellular aceita Titânio/Cerâmica além do Alumínio
        for valid_cell in ("NATURAL TITÂNIO", "BRANCO", "AZUL"):
            ok, reason, _ = validate_product(
                {"modelo": "APPLE WATCH S12 46MM GPS + CELLULAR", "capacidade": "46MM", "cor": valid_cell, "preco": 5500, "condicao": "Novo / Lacrado"},
                allow_seminovo=True, log_anomalies=False,
            )
            self.assertTrue(ok, f"S12 Cellular deveria aceitar {valid_cell}: {reason}")

        # SE 3 só tem Meia-Noite e Estelar (sem Prata!)
        ok_se3_bad, _, _ = validate_product(
            {"modelo": "APPLE WATCH SE 3 44MM GPS", "capacidade": "44MM", "cor": "PRATA", "preco": 1900, "condicao": "Novo / Lacrado"},
            allow_seminovo=True, log_anomalies=False,
        )
        self.assertFalse(ok_se3_bad)

    def test_color_guardian_sweep_heals_and_removes_invalid_colors(self):
        rows = [
            {
                "data": "25/11/2026", "fornecedor": "ZOI CELL 9959", "modelo": "APPLE WATCH ULTRA 4 49MM GPS + CELLULAR",
                "capacidade": "49MM", "cor": "BURGUNDY", "preco": 5350.0, "condicao": "Novo / Lacrado",
                "linhaOriginal": "APPLE WATCH ULTRA 4 49MM GPS + CELLULAR - (BURGUNDY) - BLACK TI BURGUNDY ALP LOOP $5.350,00",
            },
            {
                "data": "25/11/2026", "fornecedor": "LOJA Y", "modelo": "APPLE WATCH ULTRA 4 49MM GPS + CELLULAR",
                "capacidade": "49MM", "cor": "BURGUNDY", "preco": 5450.0, "condicao": "Novo / Lacrado",
                "linhaOriginal": "APPLE WATCH ULTRA 4 49MM - BURGUNDY ALPINE LOOP $5.450",
            },
            {
                "data": "25/11/2026", "fornecedor": "BOB CELL 0277", "modelo": "APPLE WATCH ULTRA 4 49MM GPS + CELLULAR",
                "capacidade": "49MM", "cor": "NATURAL TITÂNIO", "preco": 5599.0, "condicao": "Novo / Lacrado",
                "linhaOriginal": "NATURAL $5.599",
            },
        ]
        healed, report = color_guardian.run_color_guardian_sweep(rows, log_anomalies=False, allow_learn=False)
        self.assertEqual(report["corrected"], 1)
        self.assertEqual(report["removed"], 1)
        self.assertEqual(len(healed), 2)
        self.assertEqual(sorted(r["cor"] for r in healed), ["NATURAL TITÂNIO", "PRETO"])

    def test_robot_learns_new_model_colors_via_gemini(self):
        with patch.dict("os.environ", {"GEMINI_API_KEY": "fake-key"}), \
             patch("parser_listas.call_gemini_api", return_value='{"colors": ["Black", "Silver", "Starlight"]}'), \
             patch.object(color_guardian, "_save_learned_cache"):
            color_guardian._learned_cache = {}
            learned = color_guardian.learn_official_colors_for_model("IPHONE 19 PRO MAX")
            self.assertEqual(learned, {"PRETO", "PRATA", "ESTELAR"})
            # Cor oficial passa, cor inventada falha
            ok, _, _ = color_guardian.audit_and_heal_color(
                {"modelo": "IPHONE 19 PRO MAX", "cor": "PRETO"}, allow_learn=False
            )
            self.assertTrue(ok)
            ok2, reason2, _ = color_guardian.audit_and_heal_color(
                {"modelo": "IPHONE 19 PRO MAX", "cor": "LARANJA"}, allow_learn=False
            )
            self.assertFalse(ok2)
            self.assertIn("COR_INEXISTENTE_NO_MODELO", reason2)
            color_guardian._learned_cache = None


if __name__ == "__main__":
    unittest.main()
