"""Review existing quotes against retained raw supplier messages, without changing prices."""
import json
import sys
from collections import defaultdict, Counter
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from parser_listas import parse_supplier_message, condition_from_text, extract_color


def quote_key(p):
    return (str(p.get('fornecedor', '')).strip(), str(p.get('telefone', '')).strip(),
            str(p.get('modelo', '')).strip(), str(p.get('capacidade', '')).strip(), float(p.get('preco', 0)))


def review(products, messages):
    index = defaultdict(list)
    for message in messages:
        parsed = parse_supplier_message(message.get('text', ''), message.get('supplierName', ''),
                                        message.get('supplierPhone', ''), message.get('groupName', ''),
                                        include_non_new=True)
        for item in parsed:
            index[quote_key(item)].append(item)
    result, counts = [], Counter()
    for original in products:
        item = dict(original)
        candidates = index.get(quote_key(item), [])
        # Do not merge conflicting retained messages or infer condition from price.
        conditions = {p['condicao'] for p in candidates}
        colors = {p['cor'] for p in candidates if p.get('cor') not in ('-', 'PADRÃO', '')}
        inline = condition_from_text(item.get('linhaOriginal', '') + ' ' + item.get('modelo', ''))
        condition = next(iter(conditions)) if len(conditions) == 1 else (inline or 'Condição não informada')
        item['condicao'] = condition
        item['condicaoVerificada'] = True
        item['revisao'] = 'Mensagem original localizada' if candidates else 'Mensagem completa indisponível; revisão limitada à linha salva'
        if len(conditions) > 1:
            item['revisao'] = 'Mensagens com condições divergentes; confirmar com o fornecedor'
            item['condicao'] = 'Condição não informada'
        if candidates:
            item['contextoOriginal'] = '\n---\n'.join(dict.fromkeys(p['contextoOriginal'] for p in candidates))[:5000]
        if item.get('cor') in ('-', 'PADRÃO', '', None):
            if len(colors) == 1:
                item['cor'] = next(iter(colors))
                counts['cores_recuperadas'] += 1
            elif not candidates:
                # Use the actual price line, not synthetic model/color prefixes.
                color = extract_color(item.get('linhaOriginal', '').split(' - ')[-1])
                if color != '-':
                    item['cor'] = color
                    counts['cores_recuperadas'] += 1
        counts['com_mensagem' if candidates else 'sem_mensagem'] += 1
        counts[item['condicao']] += 1
        result.append(item)
    return result, dict(counts)

if __name__ == '__main__':
    products = json.loads(Path(sys.argv[1]).read_text())
    if isinstance(products, dict): products = products['products']
    messages = json.loads(Path(sys.argv[2]).read_text())
    result, counts = review(products, messages)
    Path(sys.argv[3]).write_text(json.dumps(result, ensure_ascii=False, indent=2))
    print(json.dumps(counts, ensure_ascii=False, indent=2))
