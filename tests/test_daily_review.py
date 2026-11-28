import subprocess
import tempfile
import time
import unittest
from contextlib import closing
from unittest.mock import patch
import daily_review
import painel


class ReviewCycleTests(unittest.TestCase):
    def test_duplicate_deliveries_latest_price_day_and_variant(self):
        with tempfile.TemporaryDirectory() as folder:
            now = time.time()
            day = time.localtime(now)
            morning = time.mktime((*day[:3], 9, 0, 0, *day[6:]))
            def capture(ts, text):
                daily_review.save_message(folder, {"timestamp": ts, "text": text,
                                          "supplierName": "Loja", "supplierPhone": "123"})
            first = 'Mac mini M4\n512GB • 16GB — R$ 6.199\n256GB • 16GB — R$ 5.799'
            capture(morning - 86400, first)
            capture(morning, first)
            capture(morning, first)
            capture(morning + 1, 'Mac mini M4 16/512GB R$ 6.100')
            recovered = []
            result = daily_review.run(folder, painel.offer_key, lambda items: recovered.extend(items) or len(items))
            self.assertEqual(result["mensagens_relidas"], 2)
            self.assertEqual({r["capacidade"]: r["preco"] for r in recovered}, {"16/512GB": 6100, "16/256GB": 5799})

    def test_live_retry_restart_duplicates_and_new_price(self):
        with tempfile.TemporaryDirectory() as folder:
            day = time.localtime()
            morning = time.mktime((*day[:3], 9, 0, 0, *day[6:]))
            msg = {"timestamp": morning, "text": "Mac mini M4 16/512GB R$ 6.100",
                   "supplierName": "Loja", "supplierPhone": "123"}
            daily_review.save_message(folder, msg)
            with self.assertRaises(OSError):
                daily_review.run(folder, painel.offer_key, lambda _: (_ for _ in ()).throw(OSError()), live=True)
            daily_review.save_message(folder, msg)
            prices = []
            def apply(items):
                prices.extend(row["preco"] for row in items)
                return len(items)
            result = daily_review.run(folder, painel.offer_key, apply, live=True)
            self.assertEqual(result["mensagens_relidas"], 1)
            self.assertIsNone(daily_review.run(folder, painel.offer_key, apply, live=True))
            daily_review.save_message(folder, dict(msg, timestamp=morning + 2, text=msg["text"].replace("6.100", "6.050")))
            daily_review.run(folder, painel.offer_key, apply, live=True)
            self.assertEqual(prices, [6100, 6050])

    def test_noon_update_survives_delayed_delivery_and_full_review(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(painel, "PRODUTOS_FILE", folder + "/offers.json"):
            day = time.localtime()
            morning = time.mktime((*day[:3], 9, 0, 0, *day[6:]))
            for hour, price, expected in [(0, 6200, 6200), (3, 6050, 6050), (0, 6200, 6050),
                                          (4, 6300, 6300), (3, 6050, 6300)]:
                timestamp = morning + hour * 3600
                text = f"Mac mini M4 16/512GB R$ {price}"
                daily_review.save_message(folder, {"timestamp": timestamp, "text": text,
                                          "supplierName": "Loja", "supplierPhone": "123"})
                rows = painel.parse_supplier_message(text, "Loja", "123", msg_timestamp=timestamp)
                painel.upsert_parsed_products(rows)
                daily_review.run(folder, painel.offer_key, painel.apply_reviewed_offers, live=True)
                daily_review.run(folder, painel.offer_key, painel.apply_reviewed_offers)
                self.assertEqual([r["preco"] for r in painel.load_produtos()], [expected])

    def test_media_sources_are_isolated_in_capture_and_replay(self):
        day = time.localtime()
        morning = time.mktime((*day[:3], 9, 0, 0, *day[6:]))
        model = "Mac mini M4 16/512GB"
        quote = model + " R$ 6.100"
        cases = [
            (model, "R$ 6.100", [], "imagem_ocr_confuso"),
            (quote, "", ["legenda"], "imagem_sem_texto_legivel"),
            ("", quote, ["imagem"], None),
            (quote, quote, ["imagem"], None),
            (quote, model + " R$ 6.200", [], "precos_divergentes_na_mensagem"),
        ]
        for caption, transcription, expected, issue in cases:
            with self.subTest(caption=caption, transcription=transcription), tempfile.TemporaryDirectory() as root:
                msg = dict(timestamp=morning, supplierPhone="123", supplierName="Loja",
                           text=caption, imageBase64="image")
                identity = daily_review.save_message(root, msg)
                with patch.object(daily_review, "image_text", return_value=transcription):
                    rows = daily_review.media_offers(root, identity, msg, painel.offer_key)
                    replay = []
                    report = daily_review.run(root, painel.offer_key, lambda items: replay.extend(items) or 0)
                self.assertEqual(rows, replay)
                self.assertEqual([r["fonteCaptura"] for r in rows], expected)
                if issue:
                    self.assertIn((issue, "imagem"), [(p[3], p[4]) for p in report["pendencias"]])

    def test_image_ocr_chooses_the_mode_with_more_validated_offers(self):
        psm6 = ("IPHONE 17 PRO MAX (256 GB)\nCOSMIC ORANGE {7.350,00}\n"
                "DEEP BLUE & 17.550,00)\nSILVER 8 17790,00)")
        psm11 = ("IPHONE 17 PRO MAX (256 GB)\nCOSMIC ORANGE\n& {7.350,00}\n"
                 "@DEEP BLUE\n& {7.550,00}\nSILVER\n8 {7.790,00}")
        results = [
            subprocess.CompletedProcess([], 0, psm6.encode(), b""),
            subprocess.CompletedProcess([], 0, psm11.encode(), b""),
        ]
        with tempfile.TemporaryDirectory() as folder:
            with closing(daily_review.database(folder)) as db, patch.object(
                    daily_review.subprocess, "run", side_effect=results):
                now = time.localtime()
                timestamp = time.mktime((*now[:3], 10, 0, 0, *now[6:]))
                text = daily_review.image_text(db, "2026-11-27", "message", "YQ==", timestamp)
        self.assertEqual(text, psm11)

    def test_image_ocr_unclear_text_is_held_without_parsing(self):
        timestamp = time.time()
        msg = {"timestamp": timestamp, "supplierPhone": "123", "supplierName": "Loja",
               "imageBase64": "image", "hasImage": True}
        with tempfile.TemporaryDirectory() as root:
            daily_review.save_message(root, msg)
            unclear = "IPHONE 17 PRO MAX ####@@ R$ 8.200 !!!"
            with patch.object(daily_review, "image_text", return_value=unclear):
                report = daily_review.run(root, painel.offer_key, lambda _: 0)
            self.assertEqual(report["ofertas_confirmadas"], 0)
            self.assertIn(("imagem_ocr_confuso", "imagem"),
                          [(pending[3], pending[4]) for pending in report["pendencias"]])

    def test_color_siblings_with_shared_or_individual_prices(self):
        for header, variants, target, price, expected in [
            ("iPhone 12 128GB $ 1500", ["Preto", "Azul"], "Azul", 1500, 0),
            ("iPhone 17 Pro Max 512GB", ["laranja 7000", "azul 7200"], "azul 7200", 7200, 2),
            ("iPhone 17 Pro Max 512GB", ["laranja 7000", "azul 7200"], "azul 7200", 7000, None),
        ]:
            row = {"contextoOriginal": f"Novo\n{header}\n{target}", "preco": price}
            self.assertEqual(daily_review.evidence_block(row, {header: [0]}, [header, *variants]), expected)

    def test_price_evidence_must_match_the_parsed_color_variant(self):
        lines = ["IPHONE 18 PRO MAX 256GB", "GLACIER R$ 8.750", "BRGND R$ 8.750"]
        row = {"contextoOriginal": "Novo\nIPHONE 18 PRO MAX 256GB\nBRGND R$ 8.750",
               "modelo": "IPHONE 18 PRO MAX", "cor": "BURGUNDY", "preco": 8750}
        self.assertEqual(daily_review.evidence_block(row, {lines[0]: [0]}, lines), 2)
        wrong_variant = dict(row, contextoOriginal="Novo\nIPHONE 18 PRO MAX 256GB\nGLACIER R$ 8.750")
        self.assertIsNone(daily_review.evidence_block(wrong_variant, {lines[0]: [0]}, lines))

    def test_price_from_another_block_cannot_confirm_offer(self):
        row = {"contextoOriginal": "Novo\nMac mini M4\nR$ 6100", "preco": 6100}
        self.assertIsNone(daily_review.evidence_block(row, {"Mac mini M4": [0]},
                          ["Mac mini M4", "MacBook Air M4", "R$ 6100"]))

    def test_replay_does_not_replace_newer_or_unchanged_offers(self):
        row = dict(data=painel.get_today_str(), hora="12:00:00", fornecedor="X", modelo="MAC MINI M4", preco=6100)
        with patch.object(painel, "load_produtos", return_value=[row]), patch.object(painel, "upsert_parsed_products") as save:
            painel.apply_reviewed_offers([row, dict(row, hora="11:00:00", preco=7000)])
            save.assert_called_once_with([])
