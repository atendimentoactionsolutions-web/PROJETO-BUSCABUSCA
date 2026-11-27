# Memória Operacional e Guardrails — O Fornecedor (`PROJETO-BUSCABUSCA`)

Este arquivo é carregado automaticamente em **todas as sessões** e auditorias diárias deste projeto.
**Objetivo central:** proteger o negócio e os clientes pagantes contra erros de preço, divergências de catálogo, perda de fornecedores/comunidades, quedas de WhatsApp ou perda de dados de contas.

---

## 1. Regras Inegociáveis de Escopo e Deploy

- **Escopo exclusivo:** alterar **SOMENTE** `PROJETO-BUSCABUSCA` (`/opt/disparador-fornecedores` na VPS). **NUNCA** mexer em `mundo-apple-buscador`.
- **Fluxo obrigatório de deploy:** **SEMPRE `local → GitHub (commit + push) → VPS`**. Nunca editar código diretamente na VPS sem commitar no GitHub antes.
- **Segurança de segredos:** nunca commitar chaves, senhas, tokens, `api_v1_key.txt`, `session_secret.txt`, `usuarios_acesso.json`, `accounts.db` ou dados de runtime no GitHub.
- **Marca e domínio:** **O Fornecedor** — domínio principal `https://ofornecedor.tech/` (`www.ofornecedor.tech`, `ofornecedor.srv2058756.hstgr.cloud` e `buscabusca.srv2058756.hstgr.cloud` fazem redirect `301` para `https://ofornecedor.tech/`).
- **Banco de contas blindado:** o SQLite de usuários fica **fora** da pasta de código, em `/var/lib/ofornecedor-db/accounts.db` (via `OFORNECEDOR_DB_DIR=/var/lib/ofornecedor-db` em `/etc/systemd/system/disparador-pxt.service.d/accounts-db.conf`) com backups automáticos em `/var/lib/ofornecedor-db/backups/`. **NUNCA apagar, sobrescrever ou resetar este banco em deploys.**

---

## 2. Regras de Acesso, Login e Preço (DFU)

- **Master (`/admin`):** usuário `cledson` (`role: master`, **SEM DFU**). Único perfil com acesso ao painel `/admin` para criar, pausar/reativar, alterar senha, definir vencimento e excluir contas de clientes.
- **DFU exclusivo:** usuário `admin` (`role: user`, **ÚNICO login com DFU**). Bloqueado de acessar `/admin`.
- **Demais contas (clientes):** todas **SEM DFU**, recebem preço base puro, sem limite de dispositivos simultâneos, bloqueio automático no vencimento, visual limpo (somente buscador).
- **UI limpa:** **NUNCA** exibir selos, textos ou menções a "DFU" na interface do usuário nem poluir a tela de login.
- **Compatibilidade local:** chamadas diretas de `127.0.0.1` na VPS (sem `X-Forwarded-For` / `X-Real-IP` do Nginx) continuam recebendo `200` em `/api/products` com preço base para integrações locais.

---

## 3. Arquitetura dos Robôs (Prática, Ágil e Sem Pesar a VPS)

| Robô | Arquivo / Thread | Intervalo | Função |
| --- | --- | --- | --- |
| **Guardião de Cores + IA** | `color_guardian.py` / `color_guardian_worker` | `3 min` (`180s`) | Bloqueia cores inexistentes por modelo, corrige pulseira vs caixa e aprende novos modelos Apple via Gemini (`gemini-3.5-flash`). |
| **Vistoria Anti-Erros** | `price_guardian.py` / `guardian_sweep_worker` | `5 min` (`300s`) | Remove preços/modelos inválidos, move seminovos fora do lugar, limpa duplicatas e grava removidos na quarentena diária (`quarentena_guardiao.json`). |
| **Monitor de Catálogo** | `painel.py` / `catalog_monitor_worker` | `5 min` (`300s`) | Acompanha retenções e integridade geral do catálogo. |
| **Revisão / Atualização** | `daily_review.py` / `daily_review_worker` | `10 min` (`600s`) | Reprocessa listas do dia respeitando `quarentena_guardiao.json` para não ressuscitar erro já limpo. |
| **Auditor de Cobertura** | `coverage_guardian.py` / `coverage_guardian_worker` | `10 min` (`600s`) | Confere 100% dos grupos/comunidades (`@g.us`), canais (`@newsletter`) e contatos privados, fila de captura e silêncio > 25 min (`08h–17h`). Recuperação `/api/scan-archived` com cooldown de `30 min`. |

- **Performance obrigatória:** manter cache em memória de `/api/products` (separado `base` vs `dfu`, invalidado em `save_produtos()`), compressão `gzip`, `ETag`/`304 Not Modified`, atualização do navegador a cada `120s` (pausada em aba oculta) e botão manual **Atualizar**.

---

## 4. Histórico de Bugs Críticos Já Corrigidos (PROIBIDO REGREDIR)

1. **Queda falsa do WhatsApp em loop:** healthcheck `/usr/local/bin/healthcheck-buscador.sh` deve checar `/api/ping` (leve, `< 5ms`, timeout `15s`, 3 falhas seguidas antes de reiniciar) — nunca bater em endpoint pesado.
2. **Separação iPad Wi-Fi vs Wi-Fi + Cellular:** manter seções distintas e ordenadas em `web/catalog.js` e nomes canônicos em `parser_listas.py`.
3. **Watch Ultra 4 Burgundy (pulseira vs caixa) e Watch S12 GPS (4 cores Aluminum):** `BLACK TI BURGUNDY ALP LOOP` → caixa `PRETO` (`Black Titanium`); `WHITE GOLD` em Watch S12 → `DOURADO`.
4. **Cabo-de-guerra entre robôs (ex: `34 → 33 → 34` iPhones Novos):** `guardian_sweep_worker` grava removidos em `quarentena_guardiao.json` (por dia) e `daily_review_worker` nunca reinserirá item em quarentena.
5. **Hash de senha portável (macOS LibreSSL vs Linux):** usar `hashlib.pbkdf2_hmac("sha256", ..., 200_000)` em `access_control.py` (nunca `hashlib.scrypt`).
6. **Comunidades ignoradas no scan (`leaveBlockedMembership is not defined`):** manter `activeLeaveBlockedMembership` no escopo de módulo em `whatsapp_bridge.js` e `blacklisted` explícito em `/api/groups` para que `coverage_guardian.py` audite 100% dos grupos válidos (`0 fora do radar`) sem falsos alertas em itens da `GROUP_BLACKLIST`.
7. **Reset 00:00 + Janela 06:00 + Regra "na dúvida descarta" (mensagens, imagens OCR e 1ª linha):**
   - `00:00` BRT zera `produtos_dia.json` e `quarentena_guardiao.json` (arquivando o dia anterior).
   - `00:00–05:59` BRT: captura bloqueada; `06:00` BRT em diante (até `18:00`): captura texto + imagens (Gemini Vision + fallback OCR filtrado por `_is_clear_ocr_text`).
   - Qualquer oferta confusa, ambígua, incompleta, misturando Novo/Seminovo ou com sinal de não-original (`1ª linha`, `primeira linha`, `segunda linha`, `réplica`, `paralelo`, `similar`, `OEM`, `genérico`, `Foxconn`, `original china`, `clone`, `fake`) é descartada no parser e bloqueada em `price_guardian.py` (`NAO_ORIGINAL_BLOQUEADO`).

8. **Correções de fornecedor e evidência (27/11/2026):** preservar `messageId`, origem, horário e `editKey`; na releitura usar somente a versão vigente da mensagem, mesmo quando a edição elimina variantes ou fica sem oferta válida. Uma entrega antiga não pode ressuscitar a versão substituída. Não usar texto reescrito por IA como evidência de preço/configuração. RAM e SSD de Macs devem constar no bloco original.
9. **MacBook Pro M6:** modelo existe. Ficha Apple `https://support.apple.com/en-us/148300` e `https://www.apple.com/macbook-pro/liquid-retina-xdr-display/specs/` conferidas em 27/11/2026: M6 base, 14 polegadas, RAM 16/24/32GB, SSD 1/2/4TB. Não extrapolar configurações de outros chips; nunca preencher RAM ausente.
10. **Consenso de preços de iPhone:** comparar dia/modelo/capacidade/cor/condição/tipo de SIM, contando cada fornecedor uma vez. Reter desvio absoluto de pelo menos `max(R$ 800, 8%)` quando pelo menos três outros fornecedores e 75% dos pares concordarem dentro de 4% da mediana. Não substituir o preço pela mediana nem classificar como seminovo apenas pelo preço. Correção explícita passa novamente por todas as validações. Divergência por consenso nunca aciona reclassificação automática para seminovo; registros legados marcados automaticamente como seminovo precisam de evidência no texto original (mantida a regra histórica de modelos descontinuados).
11. **Faixa permitida de iPhones Seminovos (27/11/2026):** aceitar Seminovos de iPhone **estritamente a partir do iPhone 13 até no máximo o iPhone 17 Pro Max** (`13 <= gen <= 17`). Descartar em todas as camadas (captura WhatsApp `parser_listas.py`, guardião `price_guardian.py` e vitrine `web/catalog.js`) qualquer iPhone Seminovo abaixo do 13 (`7/8/X/XR/XS/SE/11/12`) ou acima do 17 (`18/18 Pro/18 Pro Max/Duo`). iPhone 18 e Duo seguem normalmente em Novos/Lacrados.

11. **Listas verticais (I Smart, 27/11/2026 às 10h04):** modelo, polegadas, RAM/SSD, CPU/GPU, código Axxxx, cores e preço podem estar em linhas separadas. Associar apenas especificações explícitas do mesmo bloco, manter todas as cores com preço compartilhado ou preços próprios e preservar as linhas originais para a releitura. Não inferir RAM nem atravessar o próximo produto. Caso real obrigatório: MacBook Pro M6 14”, 16/1TB, Silver/Black, R$ 16.750.
12. **Mensagens a fornecedores desativadas:** não oferecer campanhas, modelos de texto, envio individual ou endpoints de envio/recibos. Preservar arquivos históricos de configuração e envios sem alterá-los. A captura continua somente de entrada; monitoramento de convites e comunidades permanece separado.
13. **Auditoria de listas e cores/configurações reais (27/11/2026):**
    - `COLOR_ALIASES` em `parser_listas.py` inclui `GLACIAL`/`BORDÔ`/`BORDO`/`LILÁS` para não descartar variantes válidas de iPhone 18 Pro / 18 Pro Max / 14 nem quebrar `evidence_block()`.
    - `mac_mini_capacity()` + `_is_mac_model()` extraem RAM/SSD em qualquer MacBook/iMac/Mac Mini/Mac Studio (incluindo `TB`, `256GB/8` e `512GB ... 16GB`), preservando a exigência de RAM explícita no bloco original.
    - `canonicalize_apple_model()` normaliza sufixos ordinais `3a`/`4a`/`5a`/`11a` em Watch Ultra, AirPods e iPad 11 A16, além de variantes multi-linha `AirTag (4 pack)` e variantes `CHIP FÍSICO` vs `E-SIM` em `painel.offer_key()`.
    - `web/catalog.js` normaliza `iPad-11-Wi-Fi` e `iPad 11 A1611 Wi-Fi` como o mesmo modelo; capacidade e Wi-Fi + Cellular continuam variantes distintas.
14. **Robô de cor por emoji & ortografia de atacado (27/11/2026):**
    - `color_guardian.resolve_emoji_color(raw_line, model_name)` resolve cor apenas quando existe **1 único emoji de cor** na linha e ele mapeia de forma unívoca (`len(matches) == 1`) para a paleta oficial do modelo em `OFFICIAL_MODEL_COLOR_RULES`. Linhas com 2+ emojis distintos (`🤍🖤4100`), Watch sem indicação de `CAIXA`, ou AirPods Max são descartadas (`None`), mantendo a regra "na dúvida descarta".
    - `parser_listas.py` separa `R$`/`$` colado à cor (`BurgundyR$:8750`), reconhece preços inteiros isolados com emoji (`🤍1750`, `🖤2450(marcas de uso)`), normaliza cabeçalhos seminovos com porcentagem de bateria (`13 pro 256 85+`) e aceita erros comuns de digitação (`BURGANDY`, `BRUGUNDY`, `SIIVER`, `LARNAJA`, `LILAIS`, `LEILAS`, `VIOLETA`, `WIHTE`, `LAVAND`, `ULTRAMINE`, `NIGHT SHY`, `DK BRONSE`).
15. **Cache concorrente do catálogo:** uma reconstrução iniciada antes de `save_produtos()` não pode publicar uma versão antiga depois da invalidação; validar a versão do cache e refazer a leitura quando houver invalidação durante a reconstrução.
16. **Auditoria de cobertura sem dados de grupos:** se o WhatsApp estiver `CONNECTED`, uma falha/ausência da lista de `/api/groups` nunca pode ser tratada como cobertura vazia e status `OK`; registrar pendência e acionar recuperação com cooldown.
17. **Vitrine simplificada (27/11/2026):** não exibir resumo de quantidade de modelos/variações, campo de busca nem seletor de itens por página. Manter navegação por categorias e botão de carregamento progressivo.
18. **Central administrativa focada e robôs observáveis:** manter `/admin` voltado a contas, captura WhatsApp, grupos/comunidades, cobertura e estado dos robôs. Não carregar listas/históricos de mensagens no estado do painel; exibir falhas de consulta e execução explicitamente. Preservar os intervalos atuais dos robôs (3/5/10 min).

---

## 5. Checklist de Auditoria Diária Obrigatória

Sempre que rodar uma verificação diária (ou antes/depois de qualquer ajuste), conferir na VPS (`/opt/disparador-fornecedores`):
1. `systemctl is-active disparador-pxt.service` → `active` e `/api/ping` → `{"ok":true}`.
2. **WhatsApp:** `CONNECTED`, fila `capturePending == 0` e `captureFailed == 0`, última mensagem recente no horário comercial.
3. **Cobertura (`ultima_auditoria_cobertura.json`):** `status == "OK"`, `unmonitoredGroupsCount == 0`, fornecedores ativos hoje compatíveis com o histórico.
4. **Qualidade do catálogo (logs dos robôs):** `[ROBÔ DE CORES]` com `0 cores inexistentes` e `[VISTORIA 5MIN]` com `0 divergências` (ou correções limpas sem oscilação).
5. **Banco de contas:** `/var/lib/ofornecedor-db/accounts.db` íntegro + backup recente em `/var/lib/ofornecedor-db/backups/`.
6. **Testes:** `python3 -m unittest discover tests` e `node --test tests/catalog.test.cjs` 100% verdes antes de qualquer deploy.

> **Regra de atualização contínua:** sempre que um novo bug for corrigido ou uma nova regra de negócio for combinada com o usuário, registrar aqui em `AGENTS.md` e subir ao GitHub junto com o fix.
