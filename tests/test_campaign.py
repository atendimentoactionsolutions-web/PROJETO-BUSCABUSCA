import concurrent.futures
import json
import os
import tempfile
import time
import unittest
from unittest.mock import patch
import painel

class CampaignTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.patches = []
        for name, filename in [('HISTORICO_FILE','history.json'), ('HISTORY_EVENTS_FILE','events.jsonl'), ('RESPOSTAS_FILE','replies.json'), ('FORNECEDORES_FILE','suppliers.json')]:
            item = patch.object(painel, name, os.path.join(self.folder.name, filename))
            item.start(); self.patches.append(item)
        self.phone = '5511999991234'
        self.supplier = {'nome':'Supplier','telefone':self.phone,'contato_autorizado':True}
        painel.save_fornecedores([self.supplier])

    def tearDown(self):
        for item in self.patches: item.stop()
        self.folder.cleanup()

    def sent(self):
        return {'status':'ENVIADO','horario':'2026-11-11 08:00:00','nome':'Supplier'}

    def test_saving_empty_history_never_erases_sent(self):
        painel.save_historico({self.phone:self.sent()})
        painel.save_historico({})
        self.assertEqual(painel.load_historico()[self.phone]['status'],'ENVIADO')

    def test_stale_failure_never_replaces_success(self):
        painel.save_historico({self.phone:self.sent()})
        painel.save_historico({self.phone:{'status':'ERRO'}})
        self.assertEqual(painel.load_historico()[self.phone]['status'],'ENVIADO')

    def test_phone_with_and_without_ninth_digit_match(self):
        painel.save_historico({'551199991234':self.sent()})
        self.assertFalse(painel.pending_supplier(self.supplier,painel.load_historico(),{}))

    def test_failed_history_read_blocks_send(self):
        with open(painel.HISTORICO_FILE,'w') as stream: stream.write('{broken')
        with patch('painel.bridge_post') as bridge:
            with self.assertRaises(ValueError): painel.deliver_supplier(self.supplier,'Hello')
            bridge.assert_not_called()

    def test_delivered_contact_is_not_sent_again(self):
        painel.save_historico({self.phone:self.sent()})
        with patch('painel.bridge_post') as bridge:
            result = painel.deliver_supplier(self.supplier,'Hello')
            self.assertTrue(result['skipped']); bridge.assert_not_called()

    def test_ambiguous_result_requires_manual_review(self):
        with patch('painel.bridge_post',side_effect=TimeoutError()):
            self.assertFalse(painel.deliver_supplier(self.supplier,'Hello')['ok'])
        self.assertEqual(painel.load_historico()[self.phone]['status'],'INCERTO')
        with patch('painel.bridge_post') as bridge:
            self.assertTrue(painel.deliver_supplier(self.supplier,'Hello')['skipped'])
            bridge.assert_not_called()

    def test_concurrent_requests_send_once(self):
        with patch('painel.bridge_post',return_value={'ok':True,'jid':self.phone+'@s.whatsapp.net','messageId':'ID','ack':2}) as bridge:
            with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
                list(pool.map(lambda _:painel.deliver_supplier(self.supplier,'Hello'),range(4)))
            self.assertEqual(bridge.call_count,1)

    def test_short_reply_is_stored_separately_from_sent_history(self):
        painel.save_historico({self.phone:self.sent()})
        self.assertTrue(painel.record_supplier_reply(self.phone,time.time(),'message-id'))
        self.assertEqual(painel.load_historico()[self.phone]['status'],'ENVIADO')
        self.assertIn(painel.canonical_phone(self.phone),painel.load_respostas())

    def test_reply_before_outreach_is_not_counted(self):
        painel.save_historico({self.phone:self.sent()})
        self.assertFalse(painel.record_supplier_reply(self.phone,1,'old-message'))

    def test_responded_supplier_is_excluded_from_first_outreach(self):
        painel.record_supplier_reply(self.phone,time.time(),'id')
        self.assertFalse(painel.pending_supplier(self.supplier,{},painel.load_respostas()))

    def test_existing_utf8_bom_history_loads(self):
        with open(painel.HISTORICO_FILE,'w',encoding='utf-8-sig') as stream:
            json.dump({self.phone:self.sent()},stream)
        self.assertEqual(len(painel.load_historico()),1)

    def test_previous_state_and_event_log_are_preserved(self):
        painel.save_historico({self.phone:self.sent()})
        painel.save_historico({'5511888888888':self.sent()})
        with open(painel.HISTORICO_FILE+'.bak') as stream:
            self.assertIn(self.phone,json.load(stream))
        with open(painel.HISTORY_EVENTS_FILE) as stream:
            self.assertEqual(len(stream.readlines()),2)

    def test_sending_without_contact_authorization_is_blocked(self):
        supplier = {**self.supplier, 'contato_autorizado':False}
        with patch('painel.bridge_post') as bridge:
            self.assertFalse(painel.deliver_supplier(supplier,'Hello')['ok'])
            bridge.assert_not_called()

    def test_account_pause_prevents_send(self):
        with patch.dict(os.environ, {'BUSCABUSCA_PAUSED':'1'}), patch('painel.bridge_post') as bridge:
            self.assertFalse(painel.deliver_supplier(self.supplier,'Hello')['ok'])
            bridge.assert_not_called()

    def test_created_message_without_ack_stays_unconfirmed(self):
        with patch('painel.bridge_post', return_value={'ok':True,'jid':self.phone+'@s.whatsapp.net','messageId':'ID','ack':1}):
            response = painel.deliver_supplier(self.supplier,'Hello')
        self.assertFalse(response['confirmed'])
        self.assertEqual(painel.load_historico()[self.phone]['status'],'AGUARDANDO_CONFIRMACAO')
        self.assertFalse(painel.pending_supplier(self.supplier,painel.load_historico(),{}))
        self.assertEqual(painel.load_historico()[self.phone]['texto_enviado'],'Hello')

    def test_motor_success_without_id_is_not_success(self):
        with patch('painel.bridge_post', return_value={'ok':True,'jid':self.phone+'@s.whatsapp.net'}):
            self.assertFalse(painel.deliver_supplier(self.supplier,'Hello')['ok'])
        self.assertEqual(painel.load_historico()[self.phone]['status'],'INCERTO')

    def test_receipts_advance_without_regression(self):
        with patch('painel.bridge_post', return_value={'ok':True,'jid':self.phone+'@s.whatsapp.net','messageId':'ID','ack':1}):
            painel.deliver_supplier(self.supplier,'Hello')
        self.assertTrue(painel.record_message_ack(self.phone,'ID',2))
        self.assertEqual(painel.load_historico()[self.phone]['status'],'ENVIADO')
        self.assertTrue(painel.record_message_ack(self.phone,'ID',3))
        self.assertEqual(painel.load_historico()[self.phone]['confirmacao'],'ENTREGUE')
        self.assertTrue(painel.record_message_ack(self.phone,'ID',4))
        self.assertFalse(painel.record_message_ack(self.phone,'ID',2))
        self.assertEqual(painel.load_historico()[self.phone]['confirmacao'],'LIDA')

    def test_receipts_do_not_update_a_different_message(self):
        painel.save_historico({self.phone:{'telefone':self.phone,'status':'AGUARDANDO_CONFIRMACAO','message_id':'EXPECTED','ack':1}})
        self.assertFalse(painel.record_message_ack(self.phone,'OTHER',3))
        self.assertEqual(painel.load_historico()[self.phone]['ack'],1)

    def test_stale_send_response_keeps_early_ack(self):
        painel.save_historico({self.phone:{'status':'ENVIADO','ack':3,'confirmacao':'ENTREGUE','message_id':'ID'}})
        painel.save_historico({self.phone:{'status':'AGUARDANDO_CONFIRMACAO','ack':1,'confirmacao':'PENDENTE'}})
        self.assertEqual(painel.load_historico()[self.phone]['confirmacao'],'ENTREGUE')

    def test_cooldown_applies_to_manual_sends_as_well(self):
        painel.save_historico({'OTHER':{'status':'ENVIADO','tentativa_timestamp':time.time()}})
        with patch('painel.bridge_post') as bridge:
            self.assertFalse(painel.deliver_supplier(self.supplier,'Hello')['ok'])
            bridge.assert_not_called()

    def test_legacy_short_intervals_are_replaced_with_five_to_ten_minutes(self):
        cfg = painel.normalized_messaging_config({'delay_min':8,'delay_max':15})
        self.assertEqual((cfg['delay_min'],cfg['delay_max']),(300,600))
        self.assertEqual(len(cfg['mensagens']),5)

    def test_supplier_can_select_an_editable_message(self):
        cfg = painel.normalized_messaging_config({'mensagens':['Oi, {nome}!','Tabela, {nome}?']})
        self.assertEqual(painel.supplier_message(cfg,{**self.supplier,'modelo_mensagem':1}),'Tabela, Supplier?')
        with self.assertRaises(ValueError):
            painel.normalized_messaging_config({'mensagens':['ok','']})
