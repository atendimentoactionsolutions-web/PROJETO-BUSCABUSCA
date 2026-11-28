import unittest
from unittest.mock import patch

import color_guardian
from parser_listas import extract_color
from price_guardian import validate_product


class ColorGuardianTests(unittest.TestCase):
    def test_brgnd_typo_resolves_to_burgundy(self):
        self.assertEqual(extract_color("BRGND R$ 8.750"), "BURGUNDY")

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
                {"modelo": "APPLE WATCH S12 46MM GPS + CELLULAR", "capacidade": "46MM", "cor": valid_cell, "preco": 4500, "condicao": "Novo / Lacrado"},
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

    def test_emoji_color_robot_resolves_official_colors_and_rejects_ambiguity(self):
        self.assertEqual(color_guardian.resolve_emoji_color("🤍1750", "IPHONE 13"), "ESTELAR")
        self.assertEqual(color_guardian.resolve_emoji_color("🖤1750", "IPHONE 13"), "MEIA-NOITE")
        self.assertEqual(color_guardian.resolve_emoji_color("💙1750", "IPHONE 13"), "AZUL")
        self.assertEqual(color_guardian.resolve_emoji_color("💜2250", "IPHONE 14 PRO"), "ROXO")
        self.assertEqual(color_guardian.resolve_emoji_color("🧡2250", "IPHONE 14 PRO"), "DOURADO")
        self.assertEqual(color_guardian.resolve_emoji_color("🖤4900", "IPHONE 16 PRO MAX"), "PRETO TITÂNIO")
        self.assertEqual(color_guardian.resolve_emoji_color("🩶4950", "IPHONE 16 PRO MAX"), "NATURAL TITÂNIO")
        self.assertEqual(color_guardian.resolve_emoji_color("🧡4950", "IPHONE 16 PRO MAX"), "DESERT TITÂNIO")
        self.assertEqual(color_guardian.resolve_emoji_color("🧡6400", "IPHONE 17 PRO MAX"), "LARANJA")
        self.assertEqual(color_guardian.resolve_emoji_color("💙6400", "IPHONE 17 PRO MAX"), "AZUL")
        self.assertEqual(color_guardian.resolve_emoji_color("🤍6400", "IPHONE 17 PRO MAX"), "PRATA")
        self.assertEqual(color_guardian.resolve_emoji_color("🖤 💰13,300.00💰", "IPHONE 18 PRO MAX"), "PRETO")
        self.assertEqual(color_guardian.resolve_emoji_color("♥️. 💰13,450.00💰", "IPHONE 18 PRO MAX"), "BURGUNDY")
        self.assertEqual(color_guardian.resolve_emoji_color("🩵 💰13,400.00💰", "IPHONE 18 PRO MAX"), "GLACIER")
        # Regras anti-erro: múltiplos emojis distintos, Watch sem CAIXA, AirPods Max ou cor inexistente
        self.assertIsNone(color_guardian.resolve_emoji_color("🤍🖤4100", "IPHONE 16 PRO"))
        self.assertIsNone(color_guardian.resolve_emoji_color("🤍🧡3190", "IPHONE 16"))
        self.assertIsNone(color_guardian.resolve_emoji_color("💚 4500", "IPHONE 17 AIR"))
        self.assertIsNone(color_guardian.resolve_emoji_color("🖤 3200", "APPLE WATCH S12 46MM GPS"))
        self.assertEqual(color_guardian.resolve_emoji_color("CAIXA 🖤 3200", "APPLE WATCH S12 46MM GPS"), "PRETO")
        self.assertIsNone(color_guardian.resolve_emoji_color("💙 3500", "AIRPODS MAX 2"))

    def test_jnb_seminovos_cell_zone_emoji_prices_and_wholesale_typos(self):
        import daily_review
        import parser_listas as parser

        jnb_msg = (
            "SEMINOVOS\n"
            "📲iPhone 12 128 85+\n"
            "🖤1400\n"
            "📲IPhone 13 128🇵🇸85+🔋\n"
            "🤍1750\n"
            "🖤1750\n"
            "💙1750\n"
            "📲16 pro 128 85+\n"
            "🤍🖤4100\n"
            "📲16 pro max 256Gb 85+🔋\n"
            "🖤4900\n"
            "🧡4950\n"
            "🤍4950\n"
            "🩶4950"
        )
        cell_zone_msg = (
            "LACRADOS\n"
            "18 pro max 1TB Esim\n"
            "🖤 💰13,300.00💰\n"
            "♥️. 💰13,450.00💰\n"
            "18 pro max 256gb esim\n"
            "🟣BurgundyR$:8750\n"
            "🤎BRUGUNDY R$8.780"
        )
        daytime = parser.time.struct_time((2026, 11, 27, 12, 0, 0, 4, 331, 0))
        with patch.object(parser.time, "localtime", return_value=daytime):
            jnb_rows = parser.parse_supplier_message(jnb_msg, "JNB 5554", "5511999990000", include_non_new=True)
            cz_rows = parser.parse_supplier_message(cell_zone_msg, "CELL ZONE 0938", "5511999990001", include_non_new=True)

        valid_jnb = []
        rejected_jnb = []
        for row in jnb_rows:
            ok, reason, healed = validate_product(dict(row), allow_seminovo=True, log_anomalies=False)
            if ok:
                valid_jnb.append(healed)
            else:
                rejected_jnb.append((row, reason))

        self.assertEqual(len(rejected_jnb), 1)
        self.assertEqual(rejected_jnb[0][0]["cor"], "-")
        self.assertEqual(rejected_jnb[0][1], "INCOMPLETO_SEM_COR")

        self.assertEqual(
            [(r["modelo"], r["capacidade"], r["cor"], r["preco"]) for r in valid_jnb],
            [
                ("IPHONE 13", "128GB", "ESTELAR", 1750.0),
                ("IPHONE 13", "128GB", "MEIA-NOITE", 1750.0),
                ("IPHONE 13", "128GB", "AZUL", 1750.0),
                ("IPHONE 16 PRO MAX", "256GB", "PRETO TITÂNIO", 4900.0),
                ("IPHONE 16 PRO MAX", "256GB", "DESERT TITÂNIO", 4950.0),
                ("IPHONE 16 PRO MAX", "256GB", "BRANCO TITÂNIO", 4950.0),
                ("IPHONE 16 PRO MAX", "256GB", "NATURAL TITÂNIO", 4950.0),
            ],
        )
        from collections import defaultdict
        jnb_lines = [parser.clean_line(l) for l in jnb_msg.splitlines()]
        jnb_pos = defaultdict(list)
        for idx, l in enumerate(jnb_lines):
            jnb_pos[l].append(idx)

        for row in valid_jnb:
            self.assertEqual(row["condicao"], "Seminovo")
            ev_idx = daily_review.evidence_block(row, jnb_pos, jnb_lines, jnb_msg.splitlines())
            self.assertIsNotNone(ev_idx, f"Evidence failed for {row}")

        self.assertEqual(
            [(r["modelo"], r["capacidade"], r["cor"], r["preco"]) for r in cz_rows],
            [
                ("IPHONE 18 PRO MAX", "1TB", "PRETO", 13300.0),
                ("IPHONE 18 PRO MAX", "1TB", "BURGUNDY", 13450.0),
                ("IPHONE 18 PRO MAX", "256GB", "BURGUNDY", 8750.0),
                ("IPHONE 18 PRO MAX", "256GB", "BURGUNDY", 8780.0),
            ],
        )
        cz_lines = [parser.clean_line(l) for l in cell_zone_msg.splitlines()]
        cz_pos = defaultdict(list)
        for idx, l in enumerate(cz_lines):
            cz_pos[l].append(idx)
        for row in cz_rows:
            ok, reason, _ = validate_product(dict(row), allow_seminovo=True, log_anomalies=False)
            self.assertTrue(ok, f"CELL ZONE row rejected: {row} -> {reason}")
            ev_idx = daily_review.evidence_block(row, cz_pos, cz_lines, cell_zone_msg.splitlines())
            self.assertIsNotNone(ev_idx, f"Evidence failed for {row}")


if __name__ == "__main__":
    unittest.main()
