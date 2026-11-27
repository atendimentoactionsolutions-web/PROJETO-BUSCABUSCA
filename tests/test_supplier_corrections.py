import tempfile
import time
import unittest
from unittest.mock import patch

import daily_review
import painel
import parser_listas
import price_guardian


class SupplierCorrectionTests(unittest.TestCase):
    def setUp(self):
        self.ts = int(time.mktime((*time.localtime()[:3], 9, 0, 0, 0, 0, -1)))

    def message(self, text, **kwargs):
        return dict(timestamp=self.ts, text=text, supplierName='Loja', supplierPhone='123',
                    remoteJid='community@g.us', messageId='original', **kwargs)

    def test_edit_replaces_original_even_when_variant_removed_and_old_delivery_retries(self):
        with tempfile.TemporaryDirectory() as root:
            original = self.message('Mac mini M4 16/512GB R$ 6100\nMac mini M4 16/256GB R$ 5000')
            edited = dict(original, text='Mac mini M4 16/512GB R$ 7100', timestamp=self.ts + 60, editKey='edit')
            daily_review.save_message(root, original)
            daily_review.save_message(root, edited)
            self.assertIsNone(daily_review.save_message(root, original))
            self.assertFalse(daily_review.source_is_current(root, daily_review.message_origin(original), self.ts))
            self.assertTrue(daily_review.source_is_current(root, daily_review.message_origin(edited), self.ts + 60, True))
            for live in (True, False):
                rows = []
                result = daily_review.run(root, painel.offer_key, lambda items: rows.extend(items) or len(items), live=live)
                self.assertEqual(result['mensagens_relidas'], 1)
                self.assertEqual([(r['capacidade'], r['preco']) for r in rows], [('16/512GB', 7100)])
                self.assertEqual(rows[0]['mensagemOrigem'], daily_review.message_origin(original))

    def test_edit_withdraws_invalid_old_offer_but_preserves_other_message(self):
        with tempfile.TemporaryDirectory() as root, patch.object(painel, "PRODUTOS_FILE", root + "/products.json"):
            msg = self.message('original')
            first = self.iphone('first', 8200)
            other = self.iphone('other', 8200)
            daily_review.attach_message_source([first], msg)
            daily_review.attach_message_source([other], dict(msg, messageId='other'))
            painel.save_produtos([first, other])
            painel.apply_supplier_message([], dict(msg, timestamp=self.ts + 60, editKey='edit'))
            self.assertEqual([r['fornecedor'] for r in painel.load_produtos()], ['other'])

    def test_physical_sim_and_esim_are_not_price_peers(self):
        rows = [self.iphone(str(i), 8200, contextoOriginal='Com chip físico') for i in range(3)]
        rows.append(self.iphone('suspect', 7400, contextoOriginal='eSIM'))
        self.assertFalse(price_guardian.public_price_review(rows)[1])

    def test_edit_same_second_wins_over_original(self):
        with tempfile.TemporaryDirectory() as root:
            msg = self.message('Mac mini M4 16/512GB R$ 6100')
            daily_review.save_message(root, msg)
            daily_review.save_message(root, dict(msg, text=msg['text'].replace('6100', '7100'), editKey='edit'))
            rows = []
            daily_review.run(root, painel.offer_key, lambda items: rows.extend(items) or len(items))
            self.assertEqual([r['preco'] for r in rows], [7100])

    def test_ambiguous_edit_does_not_resurrect_original_in_review(self):
        with tempfile.TemporaryDirectory() as root:
            msg = self.message('Mac mini M4 16/512GB R$ 6100')
            daily_review.save_message(root, msg)
            daily_review.save_message(root, dict(msg, text='Preço errado. Aguarde confirmação.', timestamp=self.ts + 60, editKey='edit'))
            rows = []
            daily_review.run(root, painel.offer_key, lambda items: rows.extend(items) or len(items))
            self.assertEqual(rows, [])

    def test_no_generative_fallback_can_invent_configuration(self):
        with patch.dict('os.environ', {'GEMINI_API_KEY': 'test'}), patch.object(parser_listas, 'call_gemini_api') as ai:
            rows = parser_listas.parse_supplier_message('MacBook informação incompleta', 'Loja', '123', msg_timestamp=self.ts)
            self.assertEqual(rows, [])
            ai.assert_not_called()

    def test_mac_ram_must_match_supplier_and_official_m6_configuration(self):
        for cap, original_cap, expected in [('16/1TB', '16/1TB', True), ('24/2TB', '24/2TB', True),
                                            ('32/4TB', '32/4TB', True), ('48/1TB', '48/1TB', False),
                                            ('16/512GB', '16/512GB', False), ('24/1TB', '16/1TB', False),
                                            ('16/1TB', '1TB', False)]:
            with self.subTest(cap=cap, original=original_cap):
                row = dict(modelo='MACBOOK PRO M6 14"', capacidade=cap, cor='PRETO',
                           preco=12000, condicao='Novo / Lacrado', categoria='MacBook & iMac',
                           contextoOriginal=f'Novo\nMacBook Pro M6 14" {original_cap} Preto R$ 12000')
                ok, reason, _ = price_guardian.validate_product(row, log_anomalies=False)
                self.assertEqual(ok, expected, reason)

    def iphone(self, supplier, price, **kwargs):
        return dict(modelo='IPHONE 17 PRO MAX', capacidade='256GB', cor='PRATA',
                    preco=price, condicao='Novo / Lacrado', categoria='iPhones',
                    fornecedor=supplier, data=painel.get_today_str(), hora='09:00:00', **kwargs)

    def test_800_and_1000_difference_held_both_directions_without_changing_price(self):
        peers = [self.iphone(str(i), 8200) for i in range(3)]
        for price in (7200, 7400, 9000, 9200):
            with self.subTest(price=price):
                suspect = self.iphone('suspect', price)
                visible, held = price_guardian.public_price_review(peers + [suspect])
                self.assertEqual(len(visible), 3)
                self.assertEqual(held[0][0]['preco'], price)
                swept, report = price_guardian.run_guardian_sweep(peers + [suspect], log_anomalies=False)
                self.assertEqual(len(swept), 3)

    def test_consensus_never_invents_seminovo_for_unknown_condition(self):
        peers = [dict(self.iphone(str(i), 8200), condicao='Condição não informada') for i in range(3)]
        suspect = dict(self.iphone('suspect', 9200), condicao='Condição não informada')
        rows, report = price_guardian.run_guardian_sweep(peers + [suspect], log_anomalies=False)
        self.assertEqual(len(rows), 3)
        self.assertEqual(report['reclassified'], 0)

    def test_legacy_inferred_seminovo_without_source_evidence_is_rejected(self):
        row = self.iphone('suspect', 9200, is_seminovo=True,
                          contextoOriginal='Condição não informada\niPhone 17 Pro Max 256GB prata R$ 9200')
        row['condicao'] = 'Seminovo'
        ok, reason, _ = price_guardian.validate_product(row, allow_seminovo=True, log_anomalies=False)
        self.assertFalse(ok)
        self.assertEqual(reason, 'CONDICAO_SEMINOVO_SEM_EVIDENCIA')

    def test_no_consensus_no_automatic_price_claim(self):
        for peers in ([8200, 8200], [7000, 8200, 9400]):
            rows = [self.iphone(str(i), price) for i, price in enumerate(peers)]
            _, held = price_guardian.public_price_review(rows + [self.iphone('suspect', 7400)])
            self.assertFalse(held)

    def test_same_supplier_cannot_manufacture_consensus(self):
        rows = [self.iphone('same', 8200) for _ in range(5)] + [self.iphone('suspect', 7400)]
        self.assertFalse(price_guardian.public_price_review(rows)[1])
