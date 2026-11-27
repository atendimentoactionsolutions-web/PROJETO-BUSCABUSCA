import unittest
from datetime import datetime

import parser_listas
import price_guardian


class StrictCaptureGuardTests(unittest.TestCase):
    def test_blocks_non_original_and_first_line_in_guardian(self):
        cases = [
            ("AIRPODS PRO 2", "AirPods Pro 2 1ª linha lacrado R$ 1200", 1200),
            ("AIRPODS MAX", "AirPods Max OEM Preto R$ 2600", 2600),
            ("APPLE WATCH ULTRA 3 49MM", "Watch Ultra 3 49mm Similar Titânio R$ 4100", 4100),
            ("AIRPODS 4", "AirPods 4 Foxconn Branco R$ 600", 600),
            ("IPHONE 16", "iPhone 16 128GB Preto Original China R$ 4200", 4200),
            ("AIRPODS PRO 3", "AirPods Pro 3 Segunda Linha R$ 1300", 1300),
            ("AIRPODS PRO 2", "AirPods Pro 2 Réplica AAA+ R$ 1150", 1150),
        ]
        for model, raw_line, price in cases:
            prod = {
                "modelo": model,
                "capacidade": "128GB" if "IPHONE" in model else "-",
                "cor": "PRETO" if "IPHONE" in model or "MAX" in model else "BRANCO",
                "preco": price,
                "condicao": "Novo / Lacrado",
                "linhaOriginal": raw_line,
                "fornecedor": "TESTE",
            }
            ok, reason, _ = price_guardian.validate_product(prod, log_anomalies=False)
            self.assertFalse(ok, f"Deveria bloquear item não original: {raw_line}")
            self.assertEqual(reason, "NAO_ORIGINAL_BLOQUEADO")

    def test_ocr_clarity_gate_discards_noisy_ambiguous_or_fake_images(self):
        self.assertFalse(parser_listas._is_clear_ocr_text(""))
        self.assertFalse(parser_listas._is_clear_ocr_text("###@@@*** !!! ??? 123"))
        self.assertFalse(parser_listas._is_clear_ocr_text("Bom dia pessoal tudo bem com vocês hoje"))
        # Mistura ambígua de Novo Lacrado + Seminovo/Bateria no mesmo OCR → descarta
        self.assertFalse(
            parser_listas._is_clear_ocr_text("NOVO LACRADO\nIPHONE 16 128GB PRETO R$ 4300\nSEMINOVO BATERIA 89%")
        )
        # Item 1ª linha / similar no OCR → descarta
        self.assertFalse(
            parser_listas._is_clear_ocr_text("AIRPODS PRO 2 PRIMEIRA LINHA BRANCO R$ 1200")
        )
        # Tabela limpa e inequívoca → aceita
        self.assertTrue(
            parser_listas._is_clear_ocr_text("LACRADOS\nIPHONE 17 PRO MAX 256GB PRATA R$ 8200")
        )

    def test_strict_0600_capture_window_blocks_before_6am(self):
        today = datetime.now()
        ts_0559 = int(today.replace(hour=5, minute=59, second=0, microsecond=0).timestamp())
        ts_0600 = int(today.replace(hour=6, minute=0, second=0, microsecond=0).timestamp())
        sample_msg = "LACRADOS\niPhone 17 Pro Max 256GB Prata R$ 8200"

        before = parser_listas.parse_supplier_message(sample_msg, "FORN TESTE", "5511999990000", msg_timestamp=ts_0559)
        self.assertEqual(before, [], "Não pode capturar ofertas antes das 06:00")

        after = parser_listas.parse_supplier_message(sample_msg, "FORN TESTE", "5511999990000", msg_timestamp=ts_0600)
        self.assertGreaterEqual(len(after), 1, "Deve capturar normalmente a partir das 06:00")

    def test_seminovo_iphone_allowed_only_from_13_to_17_pro_max(self):
        today = datetime.now()
        ts_1000 = int(today.replace(hour=10, minute=0, second=0, microsecond=0).timestamp())

        # Parser do WhatsApp (include_non_new=True): bloqueia < 13 e > 17 (18 / Duo), aceita 13..17 Pro Max
        msg = (
            "SEMINOVOS\n"
            "iPhone XR 64GB Preto R$ 1100\n"
            "iPhone 11 128GB Preto R$ 1450\n"
            "iPhone 12 Pro Max 256GB Grafite R$ 2400\n"
            "iPhone 13 128GB Meia-Noite R$ 2100\n"
            "iPhone 17 Pro Max 256GB Laranja R$ 6900\n"
            "iPhone 18 Pro Max 256GB Preto R$ 7800\n"
            "iPhone Duo 256GB Preto R$ 12500\n"
        )
        parsed = parser_listas.parse_supplier_message(msg, "FORN SEMI", "5511999990000", msg_timestamp=ts_1000, include_non_new=True)
        models = {p["modelo"] for p in parsed}
        self.assertIn("IPHONE 13", models)
        self.assertIn("IPHONE 17 PRO MAX", models)
        for blocked in ("IPHONE XR", "IPHONE 11", "IPHONE 12 PRO MAX", "IPHONE 18 PRO MAX", "IPHONE DUO"):
            self.assertNotIn(blocked, models)

        # Guardião (validate_product com allow_seminovo=True)
        for bad_model, bad_price in [
            ("IPHONE 11", 1400),
            ("IPHONE 12 PRO MAX", 2400),
            ("IPHONE XR", 1000),
            ("IPHONE 18 PRO", 7200),
            ("IPHONE 18 PRO MAX", 8000),
            ("IPHONE DUO", 12000),
        ]:
            ok, reason, _ = price_guardian.validate_product(
                {"modelo": bad_model, "capacidade": "256GB", "cor": "PRETO", "preco": bad_price, "condicao": "Seminovo", "fornecedor": "TESTE"},
                log_anomalies=False,
                allow_seminovo=True,
            )
            self.assertFalse(ok, f"Deveria rejeitar seminovo fora da faixa: {bad_model}")
            self.assertEqual(reason, "SEMINOVO_FORA_DA_FAIXA")

        # iPhone 13 e 17 Pro Max Seminovos aceitos; iPhone 18 Pro Max Novo Lacrado continua aceito!
        for ok_model, ok_cond, ok_color, ok_price in [
            ("IPHONE 13", "Seminovo", "MEIA-NOITE", 2100),
            ("IPHONE 17 PRO MAX", "Seminovo", "LARANJA", 6900),
            ("IPHONE 18 PRO MAX", "Novo / Lacrado", "PRETO", 8500),
        ]:
            ok, reason, _ = price_guardian.validate_product(
                {"modelo": ok_model, "capacidade": "256GB", "cor": ok_color, "preco": ok_price, "condicao": ok_cond, "fornecedor": "TESTE"},
                log_anomalies=False,
                allow_seminovo=True,
            )
            self.assertTrue(ok, f"Deveria aceitar {ok_model} ({ok_cond}): {reason}")


if __name__ == "__main__":
    unittest.main()
