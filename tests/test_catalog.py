import concurrent.futures
import json
import os
import tempfile
import threading
import unittest
from unittest.mock import patch
import painel
from price_guardian import validate_product

class CatalogTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.folder.name, 'products.json')
        self.patch = patch.object(painel, 'PRODUTOS_FILE', self.path)
        self.patch.start()
        self.archive_patch = patch.object(painel, "CATALOG_ARCHIVE_DIR", os.path.join(self.folder.name, "archive"))
        self.archive_patch.start()
        painel._guardian_quarantine["date"] = ""
        painel._guardian_quarantine["sigs"] = set()

    def tearDown(self):
        self.patch.stop()
        self.archive_patch.stop()
        self.folder.cleanup()

    def product(self, supplier='Fornecedor'):
        return dict(modelo='IPHONE 16', categoria='iPhones', preco=5000,
                    capacidade='128GB', cor='PRETO', condicao='Novo / Lacrado',
                    fornecedor=supplier, data=painel.get_today_str(), hora='10:00:00')

    def test_nested_daily_reset_does_not_deadlock(self):
        def reset():
            with painel.products_lock:
                painel.save_produtos([])
        thread = threading.Thread(target=reset, daemon=True)
        thread.start()
        thread.join(2)
        self.assertFalse(thread.is_alive())
        self.assertEqual(painel.load_produtos(), [])

    def test_concurrent_ingestion_keeps_every_supplier(self):
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
            list(pool.map(lambda i: painel.upsert_parsed_products([self.product(str(i))]), range(24)))
        self.assertEqual(len(painel.load_produtos()), 24)

    def test_existing_quote_is_updated(self):
        product = self.product()
        painel.upsert_parsed_products([product])
        product['preco'] = 5100
        painel.upsert_parsed_products([product])
        saved = painel.load_produtos()
        self.assertEqual(len(saved), 1)
        self.assertEqual(saved[0]['preco'], 5100)

    def test_new_promotion_replaces_quote_but_ambiguous_update_preserves_it(self):
        item = self.product()
        painel.upsert_parsed_products([item])
        updated = dict(item, hora="11:00:00", preco=4500,
                       contextoOriginal="IPHONE 16 128GB PRETO\nDe R$ 5.000 por R$ 4.500")
        self.assertEqual(painel.upsert_parsed_products([updated]), 1)
        ambiguous = dict(updated, hora="12:00:00", preco=4100,
                         contextoOriginal="IPHONE 16 128GB PRETO\nR$ 4.300 / R$ 4.100")
        with patch.object(painel, "log_anomaly") as audit:
            self.assertEqual(painel.upsert_parsed_products([ambiguous]), 0)
            self.assertTrue(audit.called)
        self.assertEqual(painel.load_produtos()[0]["preco"], 4500)

    def test_monitor_reports_duplicate_and_source_conflict_without_mutation(self):
        row = dict(self.product(), contextoOriginal="PRETO R$ 4.800")
        report = painel.audit_catalog([row, dict(row)])
        self.assertEqual(report["issues"]["Oferta duplicada do fornecedor"], 1)
        self.assertEqual(report["issues"]["PRECO_DIVERGE_DA_MENSAGEM"], 2)
        self.assertEqual(row["preco"], 5000)

    def test_ingestion_preserves_unreadable_catalog(self):
        with open(self.path, "w") as stream:
            stream.write("{")
        with self.assertRaises(ValueError):
            painel.upsert_parsed_products([self.product()])
        with open(self.path) as stream:
            self.assertEqual(stream.read(), "{")

    def test_only_current_day_after_six_is_visible(self):
        older = self.product('Older'); older['data'] = '01/01/2000'
        early = self.product('Early'); early['hora'] = '05:59:59'
        painel.save_produtos([older, early, self.product()])
        self.assertEqual(len(painel.load_produtos()), 1)

    def test_delayed_history_never_overwrites_a_newer_price(self):
        latest = {**self.product(), 'hora': '14:30:00', 'preco': 5100}
        older = {**self.product(), 'hora': '10:00:00', 'preco': 5000}
        painel.upsert_parsed_products([latest])
        self.assertEqual(painel.upsert_parsed_products([older]), 0)
        self.assertEqual(painel.load_produtos()[0]['preco'], 5100)

    def test_non_finite_prices_rejected(self):
        for price in (float('nan'), float('inf'), -float('inf')):
            with patch('price_guardian.log_anomaly'):
                self.assertFalse(validate_product({**self.product(), 'preco': price})[0])

    def test_catalog_clear_archives_products(self):
        painel.save_produtos([self.product()])
        painel.save_produtos([])
        self.assertEqual(painel.load_produtos(), [])
        with open(os.path.join(painel.CATALOG_ARCHIVE_DIR, painel.time.strftime("%Y%m%d") + '.json')) as stream:
            self.assertEqual(len(json.load(stream)), 1)

    def test_save_during_products_cache_rebuild_cannot_restore_stale_products(self):
        old_product = self.product()
        new_product = dict(old_product, preco=5200, hora="10:05:00")
        painel.save_produtos([old_product])
        started = threading.Event()
        release = threading.Event()
        loaded = []

        def delayed_load():
            if not loaded:
                loaded.append(True)
                started.set()
                if not release.wait(2):
                    raise TimeoutError("Cache rebuild was not released")
                return [old_product]
            return [new_product]

        result, errors = [], []

        def rebuild():
            try:
                result.append(painel._get_products_http_payload(None))
            except Exception as exc:
                errors.append(exc)

        with patch.object(painel, "load_produtos", side_effect=delayed_load), \
             patch.object(painel, "bridge_status_lite", return_value={"status": "CONNECTED"}):
            builder = threading.Thread(target=rebuild)
            builder.start()
            try:
                self.assertTrue(started.wait(2))
                painel.save_produtos([new_product])
            finally:
                release.set()
                builder.join(2)

        self.assertFalse(builder.is_alive())
        self.assertEqual(errors, [])
        self.assertEqual(len(result), 1)
        payload = json.loads(result[0][1])
        self.assertEqual(payload["products"][0]["preco"], 5200)

    def test_failed_replace_preserves_previous_data(self):
        painel.save_produtos([self.product()])
        with patch('painel.os.replace', side_effect=OSError('disk unavailable')):
            with self.assertRaises(OSError):
                painel.save_produtos([])
        self.assertEqual(len(painel.load_produtos()), 1)
        self.assertFalse(any(name.endswith('.tmp') for directory, _, files in os.walk(self.folder.name) for name in files))

    def test_guardian_sweep_auto_corrects_fake_airpods_discontinued_and_divergences(self):
        today = painel.get_today_str()
        seed = [
            # Fake AirPods R$ 58 -> deve ser removido
            dict(modelo='AIRPODS PRO 2', categoria='AirPods', capacidade='-', cor='BRANCO', preco=58, condicao='Condição não informada', fornecedor='Melk', data=today, hora='10:00:00'),
            # iPhone 13 Pro como Novo -> deve ser reclassificado para Seminovo (13..17 permitido)
            dict(modelo='IPHONE 13 PRO', categoria='iPhones', capacidade='256GB', cor='GRAFITE', preco=2650, condicao='Condição não informada', fornecedor='MeuCelular', data=today, hora='10:00:00'),
            # iPhone 12 Pro -> abaixo do iPhone 13 em Seminovos -> deve ser removido
            dict(modelo='IPHONE 12 PRO', categoria='iPhones', capacidade='256GB', cor='GRAFITE', preco=1750, condicao='Seminovo', fornecedor='MeuCelular', data=today, hora='10:00:00'),
            # Consenso de iPhone 15 128GB Novo (~R$ 4000) + 1 oferta sem rótulo a R$ 2450 -> deve virar Seminovo
            dict(modelo='IPHONE 15', categoria='iPhones', capacidade='128GB', cor='PRETO', preco=3950, condicao='Novo / Lacrado', fornecedor='F1', data=today, hora='10:00:00'),
            dict(modelo='IPHONE 15', categoria='iPhones', capacidade='128GB', cor='AZUL', preco=4000, condicao='Novo / Lacrado', fornecedor='F2', data=today, hora='10:00:00'),
            dict(modelo='IPHONE 15', categoria='iPhones', capacidade='128GB', cor='VERDE', preco=4050, condicao='Novo / Lacrado', fornecedor='F3', data=today, hora='10:00:00'),
            dict(modelo='IPHONE 15', categoria='iPhones', capacidade='128GB', cor='ROSA', preco=2450, condicao='Condição não informada', fornecedor='F4', data=today, hora='10:00:00'),
            # Inversão de capacidade: iPhone 17 Pro 256GB mediana R$ 7700, mas 512GB declarado Lacrado por R$ 6500 -> deve ser removido
            dict(modelo='IPHONE 17 PRO', categoria='iPhones', capacidade='256GB', cor='AZUL', preco=7650, condicao='Novo / Lacrado', fornecedor='G1', data=today, hora='10:00:00'),
            dict(modelo='IPHONE 17 PRO', categoria='iPhones', capacidade='256GB', cor='AZUL', preco=7750, condicao='Novo / Lacrado', fornecedor='G2', data=today, hora='10:00:00'),
            dict(modelo='IPHONE 17 PRO', categoria='iPhones', capacidade='512GB', cor='LARANJA', preco=6500, condicao='Novo / Lacrado', fornecedor='G3', data=today, hora='10:00:00'),
        ]
        painel.save_produtos(seed)
        with patch.object(painel, 'BASE_DIR', self.folder.name):
            report = painel.perform_guardian_sweep(log_anomalies=False)
        self.assertTrue(report['changed'])
        self.assertEqual(report['removed'], 3)       # AirPods R$ 58 + iPhone 12 Pro (<13) + iPhone 17 Pro 512GB invertido
        self.assertEqual(report['reclassified'], 2)  # iPhone 13 Pro + iPhone 15 Rosa R$ 2450
        healed = painel.load_produtos()
        self.assertFalse(any(p['modelo'] == 'AIRPODS PRO 2' for p in healed))
        self.assertFalse(any(p['modelo'] == 'IPHONE 12 PRO' for p in healed))
        self.assertFalse(any(p['modelo'] == 'IPHONE 17 PRO' and p['capacidade'] == '512GB' for p in healed))
        ip15_rosa = next(p for p in healed if p['modelo'] == 'IPHONE 15' and p['cor'] == 'ROSA')
        self.assertEqual(ip15_rosa['condicao'], 'Seminovo')
        ip13_pro = next(p for p in healed if p['modelo'] == 'IPHONE 13 PRO')
        self.assertEqual(ip13_pro['condicao'], 'Seminovo')

        # Releitura (daily_review) NÃO pode reinserir exatamente a mesma oferta removida pela Vistoria 5min (fim do vai-e-volta 34->33)
        with patch.object(painel, 'BASE_DIR', self.folder.name):
            reinsert_same = painel.upsert_parsed_products([
                dict(modelo='IPHONE 17 PRO', categoria='iPhones', capacidade='512GB', cor='LARANJA', preco=6500, condicao='Novo / Lacrado', fornecedor='G3', data=today, hora='10:00:00'),
            ])
            self.assertEqual(reinsert_same, 0)
            # Mas se o fornecedor mandar um preço NOVO corrigido (R$ 8600), entra normalmente!
            reinsert_fixed = painel.upsert_parsed_products([
                dict(modelo='IPHONE 17 PRO', categoria='iPhones', capacidade='512GB', cor='LARANJA', preco=8600, condicao='Novo / Lacrado', fornecedor='G3', data=today, hora='10:05:00'),
            ])
            self.assertEqual(reinsert_fixed, 1)

    def test_api_v1_feed_applies_dfu_rules_and_includes_suppliers(self):
        today = painel.get_today_str()
        seed = [
            dict(modelo='IPHONE 17 PRO MAX', categoria='iPhones', capacidade='256GB', cor='LARANJA', preco=7500, condicao='Novo / Lacrado', fornecedor='Fornecedor A', telefone='11999990001', data=today, hora='10:00:00'),
            dict(modelo='MACBOOK AIR M5 13"', categoria='MacBook & iMac', capacidade='16/512GB', cor='MEIA-NOITE', preco=8000, condicao='Novo / Lacrado', fornecedor='Fornecedor B', telefone='5511999990002', data=today, hora='10:05:00'),
            dict(modelo='IPAD 11 A16 WI-FI', categoria='iPad', capacidade='128GB', cor='AZUL', preco=3000, condicao='Novo / Lacrado', fornecedor='Fornecedor B', telefone='5511999990002', data=today, hora='10:05:00'),
            dict(modelo='APPLE WATCH S12 46MM GPS', categoria='Apple Watch', capacidade='46MM', cor='PRETO', preco=3000, condicao='Novo / Lacrado', fornecedor='Fornecedor C', telefone='11999990003', data=today, hora='10:10:00'),
            dict(modelo='AIRPODS 4', categoria='AirPods', capacidade='-', cor='BRANCO', preco=780, condicao='Novo / Lacrado', fornecedor='Fornecedor C', telefone='11999990003', data=today, hora='10:10:00'),
            dict(modelo='IPHONE 15', categoria='iPhones', capacidade='128GB', cor='ROSA', preco=2500, condicao='Seminovo', fornecedor='Fornecedor D', telefone='11999990004', data=today, hora='10:15:00'),
        ]
        painel.save_produtos(seed)
        with patch.object(painel, 'bridge_status_lite', return_value={'status': 'CONNECTED'}):
            feed = painel.build_api_v1_feed()
            sups = painel.build_api_v1_suppliers()
        self.assertTrue(feed['ok'] and feed['dfuActive'] and feed['appleOnly'])
        self.assertEqual(feed['totalOffers'], 6)
        self.assertEqual(sups['totalActiveToday'], 4)
        by_mod = {o['modelo']: o for o in feed['offers']}
        self.assertEqual((by_mod['IPHONE 17 PRO MAX']['precoBase'], by_mod['IPHONE 17 PRO MAX']['dfu'], by_mod['IPHONE 17 PRO MAX']['preco'], by_mod['IPHONE 17 PRO MAX']['vitrineCategory']), (7500.0, 100, 7600.0, 'IPH'))
        self.assertEqual(by_mod['IPHONE 17 PRO MAX']['telefone'], '5511999990001')
        self.assertEqual((by_mod['MACBOOK AIR M5 13"']['dfu'], by_mod['MACBOOK AIR M5 13"']['preco'], by_mod['MACBOOK AIR M5 13"']['vitrineCategory']), (200, 8200.0, 'MCB'))
        self.assertEqual((by_mod['IPAD 11 A16 WI-FI']['dfu'], by_mod['IPAD 11 A16 WI-FI']['preco'], by_mod['IPAD 11 A16 WI-FI']['vitrineCategory']), (50, 3050.0, 'IPAD'))
        self.assertEqual((by_mod['APPLE WATCH S12 46MM GPS']['dfu'], by_mod['APPLE WATCH S12 46MM GPS']['preco'], by_mod['APPLE WATCH S12 46MM GPS']['vitrineCategory']), (100, 3100.0, 'RLG'))
        self.assertEqual((by_mod['AIRPODS 4']['dfu'], by_mod['AIRPODS 4']['preco'], by_mod['AIRPODS 4']['vitrineCategory']), (0, 780.0, 'PODS'))
        self.assertEqual((by_mod['IPHONE 15']['dfu'], by_mod['IPHONE 15']['preco'], by_mod['IPHONE 15']['vitrineCategory'], by_mod['IPHONE 15']['isSeminovo']), (0, 2500.0, 'SEMI', True))

if __name__ == '__main__':
    unittest.main()
