"""Auditoria reproduzível de uma cópia das listas: nunca altera o catálogo.

Uso: python3 scripts/audit_supplier_lists.py DIRETORIO_DA_COPIA
A cópia deve conter revisao_listas/mensagens.sqlite3 e products.json.
Relatórios privados são escritos somente no diretório fornecido.
"""
import json
import sys
import time
from collections import Counter
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import daily_review
import painel
import parser_listas as parser
from price_guardian import public_price_review


def audit(root):
    root = Path(root)
    day = time.strftime('%Y-%m-%d')
    products = json.loads((root / 'products.json').read_text())
    visible, held = public_price_review(products)
    current = {painel.offer_key(row): row for row in products}
    public = {painel.offer_key(row) for row in visible}
    stats, reasons = Counter(), Counter()
    details, latest, unknown = [], {}, []
    # OCR já arquivado: não chamar IA nem fabricar transcrições na auditoria.
    def cached_image(db, day, key, encoded):
        row = db.execute('SELECT texto FROM transcricoes WHERE dia=? AND chave=?', (day, key)).fetchone()
        return row[0] if row else ''
    with daily_review.database(root) as db, patch.object(daily_review, 'image_text', cached_image):
        keys = daily_review.latest_message_keys(db, day)
        for key, raw in db.execute('SELECT chave,dados FROM mensagens WHERE dia=? ORDER BY rowid', (day,)):
            msg = json.loads(raw)
            stats['mensagens_arquivadas'] += 1
            origin = daily_review.message_origin(msg)
            if origin and keys[origin] != key:
                stats['versoes_substituidas'] += 1
                continue
            stats['mensagens_revisadas'] += 1
            pending, local = [], Counter()
            offers = daily_review.review_message(db, day, key, msg, painel.offer_key, local, pending)
            stats.update(local)
            for offer_key, row in offers.items():
                previous = latest.get(offer_key)
                if previous is None or msg['timestamp'] > previous[0]:
                    latest[offer_key] = (msg['timestamp'], row)
                elif msg['timestamp'] == previous[0] and (not row or previous[1] and previous[1]['preco'] != row['preco']):
                    latest[offer_key] = (msg['timestamp'], None)
            sources = {'lista': msg.get('text', ''), 'legenda': msg.get('text', '')}
            if msg.get('imageBase64'):
                sources['imagem'] = cached_image(db, day, key, msg['imageBase64'])
            unresolved = []
            for _, index, model, reason, source in pending:
                reasons[reason] += 1
                lines = [parser.clean_line(line) for line in sources.get(source, '').splitlines()]
                block = '\n'.join(lines[max(0,index-5):index+2]) if index is not None else ''
                entry = dict(line=index, model=model, reason=reason, source=source, block=block)
                unresolved.append(entry)
                if reason == 'linha_com_preco_a_conferir':
                    unknown.append(dict(message=key, supplier=msg.get('supplierName'), **entry))
            details.append(dict(message=key, supplier=msg.get('supplierName'),
                                hora=time.strftime('%H:%M:%S',time.localtime(msg['timestamp'])),
                                fontes=list(sources), ofertas=len(offers),
                                confirmadas=sum(bool(row) for row in offers.values()), pendencias=unresolved))
    missing = []
    for key, (_, row) in latest.items():
        if not row:
            continue
        stats['ofertas_vigentes_confirmadas'] += 1
        if key not in current:
            stats['confirmadas_ausentes_no_catalogo'] += 1
            missing.append(row)
        elif key not in public:
            stats['confirmadas_retidas_na_exibicao'] += 1
        else:
            stats['confirmadas_presentes_na_exibicao'] += 1
    result = dict(dia=day, horario=time.strftime('%H:%M:%S'), stats=dict(stats),
                  reasons=dict(reasons), ofertas_catalogo=len(products), ofertas_visiveis=len(visible),
                  ofertas_retidas=len(held), listas=details, confirmadas_ausentes=missing,
                  linhas_nao_extraidas=unknown)
    (root / 'audit.json').write_text(json.dumps(result,ensure_ascii=False,indent=2))
    print(json.dumps({key:value for key,value in result.items() if key not in ('listas','confirmadas_ausentes','linhas_nao_extraidas')},ensure_ascii=False,indent=2))
    return result


if __name__ == '__main__':
    audit(sys.argv[1])
