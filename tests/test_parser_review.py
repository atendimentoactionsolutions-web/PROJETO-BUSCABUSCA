import unittest
from unittest.mock import patch
import parser_listas as parser
from scripts.review_catalog import review

class ReviewTests(unittest.TestCase):
    def parse(self, text, **kw):
        with patch.object(parser.time, 'localtime', return_value=parser.time.struct_time((2026,11,13,12,0,0,4,317,0))):
            return parser.parse_supplier_message(text, 'Fornecedor', '5511000000000', **kw)

    def test_supplier_amounts_preserve_cents_and_review_payment_options(self):
        from price_guardian import review_quoted_price
        for text, amount, review in [
                ("R$ 4.599,90", 4599.90, False), ("$4599.90", 4599.90, False),
                ("$4,599.90", 4599.90, False), ("De R$ 5.000 por 4.500", 4500, False),
                ("12x de R$ 450,00", 450, True), ("R$ 4.500 / 4.700", 4500, True)]:
            with self.subTest(text=text):
                rows = self.parse("LACRADOS\nIPHONE 16 128GB PRETO " + text)
                self.assertEqual([row["preco"] for row in rows], [amount])
                self.assertEqual(bool(review_quoted_price(rows[0])), review)

    def test_cable_prices_do_not_overwrite_mouse_colors(self):
        text = ("👑 MAGIC MOUSE 2 (USB-C)\n✔️PRETO\nR$. 700\n✔️BRANCO\nR$. 650\n"
                "👑 CABO USB-C LIGHTNING (1M) - ORIGINAL\nR$. 80\n"
                "👑 CABO USB-C LIGHTNING (2M) - ORIGINAL\nR$. 140\n"
                "OUTRO ARTIGO\nR$. 199")
        rows = self.parse(text)
        self.assertEqual([(r["cor"],r["preco"]) for r in rows if "MOUSE" in r["modelo"]],
                         [("PRETO",700),("BRANCO",650)])
        self.assertEqual(len(rows), 2)

    def test_power_accessories_do_not_replace_a_device_quote(self):
        msg = ("iPhone 16 128GB preto com cabo original R$ 4.500\n"
               "Kit fonte e cabo para iPhone 16\nR$ 100\n"
               "Apple 20W USB-C Power Adapter R$ 120\n"
               "iPhone 16 256GB preto sem fonte R$ 5.000")
        self.assertEqual([(r["modelo"], r["preco"]) for r in self.parse(msg)],
                         [("IPHONE 16", 4500), ("IPHONE 16", 5000)])

    def test_first_quality_section_ends_at_originals_heading(self):
        text = ("AirPods 4 R$ 950\n*_Acessórios 1ªLinha:_*\n"
                "•AIRPODS PRO 2 *_R$90_*\n*_Acessórios Originais:_*\n"
                "•AIRPODS PRO 2 *_R$1,300_*")
        self.assertEqual([(r["modelo"], r["preco"]) for r in self.parse(text)],
                         [("AIRPODS 4", 950), ("AIRPODS PRO 2", 1300)])

    def test_watch_prices_do_not_cross_series_or_sizes(self):
        msg = ("⌚️S12 46mm\nR$3200\nPreto\nDark bronze\n"
               "⌚️SE 3° 44MM\nR$1950\nPreto\nStarlight R$1900\n"
               "⌚️SE 3° 40MM\nR$1750\nPreto\nStarlight R$1800")
        self.assertEqual([(r["modelo"], r["preco"]) for r in self.parse(msg)],
                         [("APPLE WATCH S12 46MM GPS", 3200)] * 2 +
                         [("APPLE WATCH SE 3 44MM GPS", v) for v in (1950,1900)] +
                         [("APPLE WATCH SE 3 40MM GPS", v) for v in (1750,1800)])

    def test_mac_screen_memory_and_price_are_independent(self):
        self.assertEqual(parser.extract_capacity("512GB SSD 32GB RAM R$ 8.128"), "32/512GB")
        self.assertEqual(parser.extract_capacity("R$ 512"), "-")
        self.assertEqual(parser.normalize_model_name("MacBook Pro M5 16 RAM 512 GB"), "MACBOOK PRO M5")
        self.assertEqual(parser.normalize_model_name('MacBook M5 16/512GB 13"'), "")
        self.assertEqual(parser.normalize_model_name("MacBook Air M5 16/512GB"), "MACBOOK AIR M5")

    def test_mini_keeps_each_price_with_its_configuration(self):
        from apple_reference import model_status
        from price_guardian import validate_product
        rows = self.parse("Mac mini M4\n512GB • 16GB — R$ 6.199,00\n256GB • 16GB — R$ 5.799,00\n"
                          "Mac mini M4 Pro 24GB RAM 512GB\n12C CPU | 16C GPU\nR$ 11.200")
        self.assertEqual([(r["capacidade"], r["preco"]) for r in rows],
                         [("16/512GB", 6199), ("16/256GB", 5799), ("24/512GB", 11200)])
        for row in rows:
            self.assertTrue(model_status(row["modelo"]))
            self.assertTrue(validate_product(row, log_anomalies=False)[0])
        conflict = dict(rows[-1], contextoOriginal="12C CPU / 12C GPU")
        self.assertFalse(validate_product(conflict, log_anomalies=False)[0])

    def test_standalone_color_is_kept_for_following_price(self):
        items=self.parse('LACRADOS\nIPHONE 17 PRO MAX 256GB\nLARANJA\nR$ 7.300')
        self.assertEqual(items[0]['cor'], 'LARANJA')
        self.assertEqual(items[0]['condicao'], 'Novo / Lacrado')

    def test_mixed_asis_section_is_not_new(self):
        text='LACRADOS\nIPHONE 17 PRO MAX 256GB\nLARANJA\nR$ 7.300\n___ASIS___\nIPHONE 17 PRO MAX 256GB\nLARANJA\n6.600'
        self.assertEqual(len(self.parse(text)),1)
        reviewed=self.parse(text,include_non_new=True)
        self.assertEqual(reviewed[-1]['condicao'],'ASIS')
        self.assertEqual(reviewed[-1]['cor'],'LARANJA')

    def test_unknown_is_not_automatically_sealed(self):
        items=self.parse('IPHONE 17 PRO MAX 256GB\nR$ 7.300')
        self.assertEqual(items[0]['condicao'],'Condição não informada')
        self.assertEqual(items[0]['cor'],'-')

    def test_negation_and_unsealed(self):
        self.assertIsNone(parser.condition_from_text('NÃO ATIVADO'))
        self.assertEqual(parser.condition_from_text('NOVO DESLACRADO'),'Deslacrado')
        self.assertEqual(parser.condition_from_text('ASIS++, NÃO ATIVADO'),'ASIS')

    def test_new_model_does_not_inherit_previous_color(self):
        items=self.parse('LACRADOS\nIPHONE 17 PRO MAX 256GB\nLARANJA\n7.300\nIPHONE 18 PRO 256GB\n8.000')
        self.assertEqual(items[-1]['cor'],'-')

    def test_new_sealed_section_clears_previous_header(self):
        items=self.parse('ASIS\nIPHONE 17 PRO MAX 256GB\n6.600\nLACRADOS\n7.300',include_non_new=True)
        self.assertEqual(len(items),1)

    def test_absent_message_keeps_unknown_not_sealed(self):
        result,_=review([dict(modelo='IPHONE 17',capacidade='256GB',preco=5500,condicao='Novo / Lacrado',cor='-',linhaOriginal='IPHONE 17 256GB R$5500')],[])
        self.assertEqual(result[0]['condicao'],'Condição não informada')

    def test_warranty_activation_disclaimer_not_ativado(self):
        text=(
            'a garantia de fábrica está começando a contar do momento em que sai da fábrica. '
            'Não a partir do momento em que foi ativado.\n'
            'LACRADOS\n'
            'MACBOOK AIR M4 13 16/256GB ESTELAR R$ 5.800'
        )
        items=self.parse(text)
        self.assertEqual(len(items),1)
        self.assertEqual(items[0]['condicao'],'Novo / Lacrado')

    def test_cpo_does_not_bleed_onto_iphone_17_18_duo_and_non_new_blocked(self):
        text=(
            'CPO\n'
            'IPHONE 16 PRO MAX 256GB NATURAL R$ 6.800\n'
            'IPHONE 18 PRO MAX 256GB BLACK R$ 7.800\n'
            'IPHONE 18 DUO 512GB PRETO R$ 15.600\n'
            'OPEN BOX\n'
            'IPHONE 17 PRO MAX 256GB LARANJA R$ 7.500'
        )
        items=self.parse(text)
        models={i['modelo']:i for i in items}
        self.assertIn('IPHONE 16 PRO MAX',models)
        self.assertEqual(models['IPHONE 16 PRO MAX']['condicao'],'CPO')
        self.assertIn('IPHONE 18 PRO MAX',models)
        self.assertNotEqual(models['IPHONE 18 PRO MAX']['condicao'],'CPO')
        self.assertIn('IPHONE DUO',models)
        self.assertNotIn('IPHONE 18 DUO',models)
        self.assertNotEqual(models['IPHONE DUO']['condicao'],'CPO')
        self.assertNotIn('IPHONE 17 PRO MAX',models)

    def test_price_guardian_canonicalizes_duo_and_blocks_ativado_and_pelle(self):
        from price_guardian import validate_product
        ok,reason,_=validate_product({
            'modelo':'IPHONE 17 PRO MAX - YO PELLE AN 2',
            'capacidade':'512GB',
            'preco':8500,
            'condicao':'Novo / Lacrado',
            'cor':'PRETO',
        })
        self.assertFalse(ok)
        ok_open,_,_=validate_product({
            'modelo':'IPHONE 17 PRO MAX',
            'capacidade':'256GB',
            'preco':7800,
            'condicao':'Open Box',
            'cor':'LARANJA',
        })
        self.assertFalse(ok_open)
        ok_duo,_,duo_prod=validate_product({
            'modelo':'IPHONE 18 DUO LLA',
            'capacidade':'512GB',
            'preco':16300,
            'condicao':'Novo / Lacrado',
            'cor':'PRETO',
        })
        self.assertTrue(ok_duo)
        self.assertEqual(duo_prod['modelo'],'IPHONE DUO')

    def test_shorthand_multi_qualifier_and_underscores_never_bleed_prices(self):
        rosa_text=(
            'LACRADOS\n'
            '🟪*18 PRO MAX 256GB LL/A* 🇺🇸\n'
            '🟪PRETO💰8900💰\n'
            '🟪*18 PRO 256 LL*🇺🇸\n'
            '🟪PRETO💰8100💰\n'
            '🟪*17 PRO MAX 256GB LL* E-SIM\n'
            '🟪SILVER💰7800💰🇺🇸\n'
            '🟪*IPAD 11 A16 128GB WI-FI 2025*\n'
            '🟪SILVER💰2950💰\n'
            '💻*MAC BOOK NEO 8/256 13 2026 LL*\n'
            '🟪ÍNDIGO 💰5100💰\n'
        )
        items=self.parse(rosa_text)
        by_key={(i['modelo'],i['capacidade']):i for i in items}
        self.assertEqual(by_key[('IPHONE 18 PRO MAX','256GB')]['preco'],8900.0)
        self.assertEqual(by_key[('IPHONE 18 PRO','256GB')]['preco'],8100.0)
        self.assertEqual(by_key[('IPHONE 17 PRO MAX','256GB')]['preco'],7800.0)
        self.assertEqual(by_key[('IPAD 11 A16 WI-FI','128GB')]['preco'],2950.0)
        self.assertEqual(by_key[('MACBOOK NEO 13"','8/256GB')]['preco'],5100.0)

        pro_digital=(
            '📲 _iPhone DUO 256GB_\n'
            '🤍_Star white R$15.000_\n'
            '📲_18 PRO 1TB_\n'
            '🩵_Glacial 💰R$11.050_\n'
        )
        pd_items=self.parse(pro_digital)
        pd_map={i['modelo']:i for i in pd_items}
        self.assertEqual(pd_map['IPHONE DUO']['preco'],15000.0)
        self.assertEqual(pd_map['IPHONE 18 PRO']['preco'],11050.0)
        self.assertEqual(pd_map['IPHONE 18 PRO']['capacidade'],'1TB')

    def test_battery_health_and_partial_apple_warranty_rejected_as_seminovo(self):
        royal=(
            '*iPhone 17 pro Max - 256gb*\n'
            'Laranja\n'
            'Bateria 94% 🔋\n'
            'COM CAIXA 📦\n'
            'GARANTIA APPLE ATÉ 09/01/27 ✅\n'
            '*R$6.399,00*'
        )
        tio_sam=(
            '📲17 PRO 512GB\n'
            '🧡ORANGE\n'
            '🔋100%\n'
            '♻️garantia Apple até 11/26\n'
            '💰6.900'
        )
        self.assertEqual(self.parse(royal),[])
        self.assertEqual(self.parse(tio_sam),[])

    def test_accessory_header_cannot_turn_another_product_into_an_airtag(self):
        from price_guardian import validate_product
        base = dict(modelo="AIRTAG 4 PACK", cor="BRANCO", preco=610, capacidade="-")
        for extra in [dict(capacidade="64GB"), dict(linhaOriginal="ITEL A50CS 64/4"),
                      dict(linhaOriginal="1 Pack R$ 199"), dict(linhaOriginal="BRANCO/VERDE")]:
            with self.subTest(extra=extra):
                self.assertFalse(validate_product({**base, **extra}, log_anomalies=False)[0])
        self.assertTrue(validate_product(base, log_anomalies=False)[0])

    def test_battery_detail_keeps_quote_in_its_own_condition(self):
        rows = self.parse('iPhone 17 Pro Max 512\n🔋100%(90 cyclus )\nLaranja 7000\n'
                          'iPhone 17 Pro Max 256\nPreto 8000', include_non_new=True)
        self.assertEqual([(r["capacidade"], r["preco"], r["condicao"]) for r in rows],
                         [("512GB", 7000, "Seminovo"), ("256GB", 8000, "Condição não informada")])

    def test_public_price_review_uses_only_independent_comparable_quotes(self):
        from price_guardian import public_price_review
        base = dict(modelo="IPHONE 17 PRO MAX", capacidade="512GB", cor="LARANJA", condicao="Lacrado")
        peers = [dict(base, fornecedor=str(n), preco=p) for n,p in enumerate((8700,8750,8800))]
        low = dict(base, fornecedor="A", preco=7000)
        high = dict(base, fornecedor="B", preco=12000)
        used = dict(low, condicao="Seminovo")
        unknown = dict(low, condicao="Condição não informada")
        visible, held = public_price_review(peers + [low, high, used, unknown])
        self.assertEqual(visible, peers + [used])
        self.assertEqual([r for r,_ in held], [low, high, unknown])
        self.assertFalse(public_price_review([low, peers[0], peers[0], peers[1]])[1])

    def test_variant_consensus_requires_independent_matching_suppliers(self):
        from price_guardian import equalize_catalog_prices
        base = dict(modelo="IPHONE 17", capacidade="256GB", cor="PRETO", condicao="Lacrado")
        a, b, expensive = [dict(base, fornecedor=name, preco=price)
                           for name, price in [("A", 5000), ("B", 5100), ("C", 6400)]]
        other_color = dict(expensive, cor="AZUL")
        kept, dropped = equalize_catalog_prices([expensive, a, b, other_color], False)
        self.assertFalse(dropped)
        self.assertTrue(expensive["alertaPreco"])
        self.assertFalse(other_color["alertaPreco"])
        self.assertIn(other_color, kept)
        _, dropped = equalize_catalog_prices([a, dict(a), expensive], False)
        self.assertFalse(dropped)

    def test_equalize_catalog_prices_flags_but_preserves_extreme_market_outliers(self):
        from price_guardian import equalize_catalog_prices
        items=[
            {'modelo':'MACBOOK PRO M5 PRO 16"','capacidade':'24/1TB','fornecedor':'A','preco':19800},
            {'modelo':'MACBOOK PRO M5 PRO 16"','capacidade':'1TB','fornecedor':'B','preco':20000},
            {'modelo':'MACBOOK PRO M5 PRO 16"','capacidade':'1TB','fornecedor':'C','preco':20500},
            {'modelo':'MACBOOK PRO M5 PRO 16"','capacidade':'1TB','fornecedor':'HMX','preco':12350},
            {'modelo':'IPHONE 17 AIR','capacidade':'256GB','fornecedor':'A','preco':5450},
            {'modelo':'IPHONE 17 AIR','capacidade':'256GB','fornecedor':'B','preco':5550},
            {'modelo':'IPHONE 17 AIR','capacidade':'256GB','fornecedor':'C','preco':5600},
            {'modelo':'IPHONE 17 AIR','capacidade':'256GB','fornecedor':'FACE','preco':9050},
        ]
        # MACBOOK B/C/HMX share 1TB while A is 24/1TB -> 3 distinct suppliers on 1TB isolate HMX (12350 < 20000*0.85)
        kept,dropped=equalize_catalog_prices(items)
        self.assertFalse(dropped)
        self.assertEqual({x["fornecedor"] for x in kept if x["alertaPreco"]}, {"HMX", "FACE"})
        self.assertEqual(len(kept),8)

    def test_completeness_lock_rejects_missing_color_ram_storage_or_non_apple_watch(self):
        from price_guardian import validate_product
        bad_cases=[
            {'modelo':'CABO IPHONE USB 2 METRO','capacidade':'-','cor':'BRANCO','preco':130},
            {'modelo':'IPHONE 18 PRO MAX','capacidade':'-','cor':'PRETO','preco':9000},
            {'modelo':'IPHONE 18 PRO MAX','capacidade':'256GB','cor':'-','preco':8900},
            {'modelo':'MACBOOK PRO M5 14"','capacidade':'1TB','cor':'PRETO','preco':13600},
            {'modelo':'MACBOOK PRO M5','capacidade':'16/1TB','cor':'PRETO','preco':13600},
            {'modelo':'MACBOOK PRO M5 14"','capacidade':'16/1TB','cor':'-','preco':13600},
            {'modelo':'WATCH S4','capacidade':'-','cor':'PRETO','preco':830},
            {'modelo':'APPLE WATCH S11 GPS','capacidade':'-','cor':'PRETO','preco':2300},
            {'modelo':'IPAD PRO M4','capacidade':'256GB','cor':'PRATA','preco':7500},
            {'modelo':'IPAD 11 12GB','capacidade':'-','cor':'AZUL','preco':2900},
        ]
        for case in bad_cases:
            ok,_,_=validate_product({**case,'condicao':'Novo / Lacrado'})
            self.assertFalse(ok,f'Expected rejection for {case}')
        good_cases=[
            {'modelo':'MACBOOK PRO M5 14"','capacidade':'16/1TB','cor':'PRETO','preco':13600},
            {'modelo':'AIRPODS 4','capacidade':'-','cor':'-','preco':890},
            {'modelo':'MAGIC KEYBOARD IPAD PRO 11" M4/M5','capacidade':'-','cor':'PRETO','preco':2300},
            {'modelo':'IPAD 11TH WIFI','capacidade':'128GB','cor':'AZUL','preco':2999},
            {'modelo':'IPAD AIR M3 WIFI 11','capacidade':'128GB','cor':'CINZA ESPACIAL','preco':4600},
            {'modelo':'IPAD PRO 11 INCH M4 WIFI','capacidade':'256GB','cor':'PRATA','preco':7600},
        ]
        validated=[]
        for case in good_cases:
            ok,reason,item=validate_product({**case,'condicao':'Novo / Lacrado'})
            self.assertTrue(ok,f'Expected pass for {case}, got {reason}')
            validated.append(item)
        self.assertEqual(validated[1]['cor'],'BRANCO')

    def test_model_color_lock_and_airpods5(self):
        from price_guardian import validate_product
        invalid_colors=[
            {'modelo':'IPHONE 18 PRO MAX','capacidade':'512GB','cor':'LARANJA','preco':10300},
            {'modelo':'IPHONE 18 PRO','capacidade':'256GB','cor':'ROXO','preco':8000},
            {'modelo':'IPHONE 16','capacidade':'128GB','cor':'LAVANDA','preco':5500},
            {'modelo':'IPHONE 16','capacidade':'128GB','cor':'PRATA','preco':4600},
            {'modelo':'IPHONE 17 AIR','capacidade':'256GB','cor':'PRATA','preco':5499},
            {'modelo':'IPHONE 13','capacidade':'128GB','cor':'GRAFITE','preco':3100},
            {'modelo':'IPAD 11 WI-FI','capacidade':'128GB','cor':'PRETO','preco':3000},
            {'modelo':'APPLE WATCH ULTRA 2 49MM GPS + CELLULAR','capacidade':'-','cor':'LARANJA','preco':4350},
        ]
        for case in invalid_colors:
            ok,reason,_=validate_product({**case,'condicao':'Novo / Lacrado'})
            self.assertFalse(ok,f'Expected color rejection for {case}, got {reason}')
            self.assertTrue(reason.startswith('COR_INEXISTENTE_NO_MODELO'),reason)

        ok_16,_,c_16=validate_product({'modelo':'IPHONE 16','capacidade':'128GB','cor':'VERDE','preco':4500,'condicao':'Novo / Lacrado'})
        self.assertTrue(ok_16)
        self.assertEqual(c_16['cor'],'VERDE ACINZENTADO')

        ok_14p,_,c_14p=validate_product({'modelo':'IPHONE 14','capacidade':'128GB','cor':'PRETO','preco':3400,'condicao':'Novo / Lacrado'})
        ok_14m,_,c_14m=validate_product({'modelo':'IPHONE 14','capacidade':'128GB','cor':'MIDNIGHT','preco':3400,'condicao':'Novo / Lacrado'})
        self.assertTrue(ok_14p and ok_14m)
        self.assertEqual(c_14p['cor'],c_14m['cor'])

        ok_u3,_,c_u3=validate_product({
            'modelo':'APPLE WATCH ULTRA 3 49MM GPS + CELLULAR','capacidade':'-','cor':'AZUL',
            'linhaOriginal':'APPLE WATCH ULTRA 3 49MM - Black / Indigo Alpine loop $4.600','preco':4600,'condicao':'Novo / Lacrado'
        })
        self.assertTrue(ok_u3)
        self.assertEqual(c_u3['cor'],'PRETO')

        parsed_ap5=self.parse('AIRPODS 5 R$ 1.050\nAIRPODS 5 (CASE SEM FIO) R$ 1.150\nAIRPODS 4 R$ 800')
        models={p['modelo'] for p in parsed_ap5}
        self.assertEqual(models,{'AIRPODS 5','AIRPODS 5 ANC','AIRPODS 4'})
        for item in parsed_ap5:
            ok,reason,_=validate_product(dict(item))
            self.assertTrue(ok,f'Expected valid AirPods, got {reason}')

    def test_future_apple_generations_not_blindly_blocked_while_fake_prices_are(self):
        from price_guardian import validate_product
        future_valid=[
            {'modelo':'AIRPODS 6','capacidade':'-','cor':'-','preco':1250},
            {'modelo':'IPHONE 19 PRO MAX','capacidade':'256GB','cor':'PRETO','preco':9500},
            {'modelo':'APPLE WATCH ULTRA 5 49MM','capacidade':'-','cor':'PRETO','preco':5400},
        ]
        for case in future_valid:
            ok,reason,_=validate_product({**case,'condicao':'Novo / Lacrado'},log_anomalies=False)
            self.assertTrue(ok,f'Expected future Apple product allowed, got {reason}')
        ok_fake,_,flagged=validate_product({'modelo':'AIRPODS PRO 4','capacidade':'-','cor':'BRANCO','preco':60,'condicao':'Novo / Lacrado'},log_anomalies=False)
        self.assertFalse(ok_fake)
        self.assertIsNone(flagged)
        self.assertEqual(self.parse("AirPods Pro 4 (ANC)\nR$1,100"), [])

    def test_stale_header_date_rejected_and_dynamic_price_alerts_for_all_categories(self):
        from price_guardian import equalize_catalog_prices
        stale_msg="PETRA CELL\nBom Dia\n10/11/2026\nIPHONE 16 128GB ROSA R$ 4.350"
        self.assertEqual(self.parse(stale_msg),[])

        parsed_pro3=self.parse("AIRPODS PRO 3GER R$ 1.550")
        self.assertEqual([p['modelo'] for p in parsed_pro3],['AIRPODS PRO 3'])

        items=[
            {'categoria':'Acessórios Apple','modelo':'APPLE PENCIL USB-C','capacidade':'-','fornecedor':'A','preco':530},
            {'categoria':'Acessórios Apple','modelo':'APPLE PENCIL USB-C','capacidade':'-','fornecedor':'B','preco':790},
            {'categoria':'iPhones','modelo':'IPHONE 18 PRO MAX','capacidade':'256GB','fornecedor':'A','preco':8850},
            {'categoria':'iPhones','modelo':'IPHONE 18 PRO MAX','capacidade':'256GB','fornecedor':'B','preco':8900},
        ]
        kept,_=equalize_catalog_prices(items,log_anomalies=False)
        pencil_a=next(x for x in kept if x['modelo']=='APPLE PENCIL USB-C' and x['fornecedor']=='A')
        iphone_a=next(x for x in kept if x['modelo']=='IPHONE 18 PRO MAX' and x['fornecedor']=='A')
        self.assertTrue(pencil_a.get('alertaPreco'))
        self.assertIn('abaixo da mediana',pencil_a.get('observacao',''))
        self.assertFalse(iphone_a.get('alertaPreco'))

    def test_blocks_linha_premium_airpods_unicode_bold_seminovo_discontinued_and_17e(self):
        from price_guardian import validate_product, public_price_review
        melk = (
            "⭐ LINHA PREMIUM ⭐\n"
            "🔹 AirPods Pro 2 — R$ 58\n"
            "🔹 AirPods Pro 3 — R$ 58\n"
            "🔹 AirPods Max — R$ 85\n"
        )
        self.assertEqual(self.parse(melk, include_non_new=True), [])
        self.assertFalse(validate_product({'modelo': 'AIRPODS PRO 2', 'capacidade': '-', 'cor': 'BRANCO', 'preco': 58, 'condicao': 'Condição não informada'}, allow_seminovo=True, log_anomalies=False)[0])
        self.assertFalse(validate_product({'modelo': 'EARPODS LIGHTNING', 'capacidade': '-', 'cor': 'BRANCO', 'preco': 60, 'condicao': 'Condição não informada'}, allow_seminovo=True, log_anomalies=False)[0])

        zaza = (
            "━━━━━ 𝗶𝗣𝗛𝗢𝗡𝗘 𝗗𝗨𝗕𝗔𝗜🇦🇪 (𝗕𝗔𝗧𝗘𝗥𝗜𝗔 𝟵𝟮%--𝟵𝟱%) ━━━━━\n"
            "🛡️ _30 Dias Garantia com Fornecedor_\n"
            "📱 16 | 128GB🇦🇪\n"
            "⚪Branco ⚫Preto\n"
            "💰 R$ 3.580,00\n"
        )
        zaza_rows = self.parse(zaza, include_non_new=True)
        self.assertTrue(zaza_rows)
        self.assertTrue(all(r['condicao'] == 'Seminovo' for r in zaza_rows))

        atr = (
            "*📱LISTA ATUALIZADA IPHONE LACRADO 1 ANO DE GARANTIA & SEMI NOVOS GARANTIA PELA LOJA📱*\n"
            "〽️IPHONE 17 PRO MAX 256GB\n"
            "💠LARANJA 💰R$7650\n"
            "* IPHONE SEMI NOVO AMERICANO GRADE A+🇺🇸 GARANTIA APPLE*\n"
            "〽️IPHONE *17 PRO MAX 256GB*\n"
            "💠LARANJA 💰R$6950\n"
        )
        atr_rows = self.parse(atr, include_non_new=True)
        self.assertEqual([(r['preco'], r['condicao']) for r in atr_rows], [(7650.0, 'Novo / Lacrado'), (6950.0, 'Seminovo')])

        shopcell = (
            "⚠️ *Aparelho globais lacrado da Xiaomi sem garantia:*\n"
            "*IPHONES CPO*\n"
            "*Iphone 13 pro 512 gb*\n"
            "R$3520 SilveR\n"
        )
        sc_rows = self.parse(shopcell, include_non_new=True)
        self.assertEqual([(r['modelo'], r['condicao']) for r in sc_rows], [('IPHONE 13 PRO', 'CPO')])

        mega = "LACRADOS\n♻️ *Iphone 17 e 256gb* - 4420,00💸\n⚪️branco\n"
        mega_rows = self.parse(mega)
        self.assertEqual(mega_rows[0]['modelo'], 'IPHONE 17E')

        ok_15_low, _, item_15_low = validate_product(
            {'modelo': 'IPHONE 15', 'capacidade': '128GB', 'cor': 'ROSA', 'preco': 2450, 'condicao': 'Condição não informada'},
            allow_seminovo=True, log_anomalies=False,
        )
        self.assertTrue(ok_15_low)
        self.assertEqual(item_15_low['condicao'], 'Seminovo')

        ok_13, _, item_13 = validate_product(
            {'modelo': 'IPHONE 13 PRO', 'capacidade': '256GB', 'cor': 'GRAFITE', 'preco': 2650, 'condicao': 'Condição não informada'},
            allow_seminovo=True, log_anomalies=False,
        )
        self.assertTrue(ok_13)
        self.assertEqual(item_13['condicao'], 'Seminovo')

        ok_12, reason_12, _ = validate_product(
            {'modelo': 'IPHONE 12 PRO', 'capacidade': '256GB', 'cor': 'GRAFITE', 'preco': 1750, 'condicao': 'Condição não informada'},
            allow_seminovo=True, log_anomalies=False,
        )
        self.assertFalse(ok_12)
        self.assertEqual(reason_12, 'SEMINOVO_FORA_DA_FAIXA')

        self.assertFalse(validate_product(
            {'modelo': 'IPHONE DUO PRO MAX', 'capacidade': '256GB', 'cor': 'PRETO', 'preco': 8850, 'condicao': 'Novo / Lacrado'},
            allow_seminovo=True, log_anomalies=False,
        )[0])

        # Consenso entre cores bloqueia preço muito abaixo na aba de Novos
        peers = [
            {'data': '24/11/2026', 'modelo': 'IPHONE 15', 'capacidade': '128GB', 'cor': 'AZUL', 'condicao': 'Novo / Lacrado', 'fornecedor': 'A', 'preco': 4050},
            {'data': '24/11/2026', 'modelo': 'IPHONE 15', 'capacidade': '128GB', 'cor': 'PRETO', 'condicao': 'Novo / Lacrado', 'fornecedor': 'B', 'preco': 4100},
            {'data': '24/11/2026', 'modelo': 'IPHONE 15', 'capacidade': '128GB', 'cor': 'VERDE', 'condicao': 'Novo / Lacrado', 'fornecedor': 'C', 'preco': 4000},
            {'data': '24/11/2026', 'modelo': 'IPHONE 15', 'capacidade': '128GB', 'cor': 'ROSA', 'condicao': 'Condição não informada', 'fornecedor': 'D', 'preco': 2450},
        ]
        vis, held = public_price_review(peers)
        self.assertEqual(len(held), 1)
        self.assertEqual(held[0][0]['fornecedor'], 'D')

    def test_ipad_wifi_and_cellular_canonical_separation(self):
        import apple_reference
        from parser_listas import canonicalize_apple_model
        from price_guardian import validate_product

        cases = [
            ('IPAD PRO 13 INCH M5 WIFI', 'IPAD PRO M5 13" WI-FI'),
            ('IPAD PRO M5 T13', 'IPAD PRO M5 13" WI-FI'),
            ('IPAD PRO 13 INCH M5 WIFI+CELLULAR 2025 5G', 'IPAD PRO M5 13" WI-FI + CELLULAR'),
            ('IPAD PRO 13 INCH M5 WIFI + CELULAR 2025 5G', 'IPAD PRO M5 13" WI-FI + CELLULAR'),
            ('IPAD PRO 11 INCH M5 WIFI', 'IPAD PRO M5 11" WI-FI'),
            ('IPAD PRO 11 INCH M5 WIFI+CELLULAR 2025 5G', 'IPAD PRO M5 11" WI-FI + CELLULAR'),
            ('IPAD AIR 13 INCH M4 WIFI', 'IPAD AIR M4 13" WI-FI'),
            ('IPAD AIR 13 INCH M4 WIFI + CELLULAR 2025 5G', 'IPAD AIR M4 13" WI-FI + CELLULAR'),
            ('IPAD AIR 11 INCH M4 WIFI', 'IPAD AIR M4 11" WI-FI'),
            ('IPAD AIR 11 INCH M4 WIFI + CELLULAR 2025 5G', 'IPAD AIR M4 11" WI-FI + CELLULAR'),
            ('IPAD 11TH WIFI', 'IPAD 11 A16 WI-FI'),
            ('IPAD 11TH WIFI + CELLULAR', 'IPAD 11 A16 WI-FI + CELLULAR'),
            ('IPAD MINI 7 WIFI', 'IPAD MINI 7 WI-FI'),
            ('IPAD MINI 7 5G', 'IPAD MINI 7 WI-FI + CELLULAR'),
        ]
        for raw, expected in cases:
            got = canonicalize_apple_model(raw)
            self.assertEqual(got, expected, f'raw={raw}')
            self.assertIsNot(apple_reference.model_status(got), False, f'apple_reference rejected {got}')
            ok, reason, _ = validate_product(
                {'modelo': got, 'capacidade': '256GB', 'cor': 'AZUL' if 'PRO' not in got else 'PRATA', 'preco': 6500 if 'PRO' in got else 4200, 'condicao': 'Novo / Lacrado'},
                allow_seminovo=True, log_anomalies=False,
            )
            self.assertTrue(ok, f'validate_product failed for {got}: {reason}')
