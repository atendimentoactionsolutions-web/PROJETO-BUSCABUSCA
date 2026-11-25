# BuscaBusca — fornecedores

Projeto independente de comparação de custos e captura de listas Apple pelo WhatsApp.

## Executar

Requisitos: Python 3.9+ e Node.js 20+.

```sh
npm ci
npm start
```

- Catálogo: http://localhost:3050/
- Central: http://localhost:3050/admin
- Motor WhatsApp: somente em `127.0.0.1:3051`.

A primeira conexão exige leitura do QR em WhatsApp → Aparelhos conectados. Credenciais ficam em `auth_session/`; reiniciar o serviço preserva o vínculo. Credenciais revogadas são arquivadas para recuperação em vez de apagadas. Uma revogação no celular exige novo QR. Não execute dois motores com a mesma sessão.

A captura não inicia disparos. Mensagens só são enviadas pelos comandos explícitos do painel. O catálogo mantém a regra existente: somente ofertas do dia após 06h, no fuso `America/Sao_Paulo` (sobrescrevível com `TZ`). Não são publicadas ofertas antigas ou dados de demonstração.

## Validação

```sh
npm test
python3 -m py_compile painel.py parser_listas.py price_guardian.py
node --check whatsapp_bridge.js
```

## VPS existente

Destino exclusivo: `/opt/disparador-fornecedores`, serviço `disparador-pxt`, portas 3050 e 3051. O sistema da porta 80 é independente e não integra este projeto.

Antes de publicar, criar backup restrito fora do diretório da aplicação. Copiar apenas código e `web/`; preservar os JSONs de operação, fornecedores, configurações, histórico e `auth_session/`. Instalar dependências com `npm ci --omit=dev`, validar sintaxe e reiniciar apenas `disparador-pxt`. Verificar catálogo, `/api/products` e status interno WhatsApp. O backup contém dados privados e deve permanecer restrito ao administrador.

Dados operacionais, sessões e arquivos `.env` não devem entrar no Git. Scripts legados de disparo e limpeza destrutiva foram removidos; o processamento e validação ativos estão em Python.

## Recuperação da conexão e captura de listas

As listas elegíveis são gravadas em `capturas_pendentes/` antes de chamar o painel. Falhas de rede, download de imagens ou resposta do painel mantêm a lista para nova tentativa, com intervalo crescente até 60 segundos. A fila retoma após reinícios e deduplica mensagens pelo identificador do WhatsApp. Registros ilegíveis e rejeições definitivas ficam preservados para conferência; o painel mostra pendências, erros e o horário do último processamento. Preservar esse diretório em todos os deploys.

A conexão permanece ativa durante o dia inteiro. A janela atual de entrada de listas é **06h até antes de 18h, em America/Sao_Paulo**. Listas fora dessa janela não entram no catálogo. Uma lista antiga sincronizada posteriormente não substitui um preço mais recente do mesmo fornecedor.

O arquivamento de grupos não bloqueia a entrega das listas ao painel. Repetições da mesma mensagem não repetem o arquivamento. Uma vistoria de histórico interrompida pela desconexão não continua usando a sessão nova. A verificação periódica recupera conexões que não terminam de abrir em dois minutos e ignora falhas de uma sessão já substituída. Revogação no celular continua exigindo novo QR; conflito com outra instância exige resolver a duplicidade.

`npm ci` executa `scripts/patch-baileys.cjs` para corrigir a leitura de participantes de grupos no Baileys **7.0.0-rc14**, versão fixada pelo projeto. A correção trata identificadores simples e objetos JSON, conforme o problema relatado em https://github.com/WhiskeySockets/Baileys/issues/2714. O script recusa versões ou formatos inesperados. Caso a instalação use `--ignore-scripts`, execute `node scripts/patch-baileys.cjs` antes de iniciar o motor. Uma atualização futura do Baileys exige revisar essa compatibilidade.

## Histórico e continuidade dos contatos

O histórico de envios é permanente. O endpoint de zerar histórico está bloqueado. Envios são mesclados com o estado anterior, com cópia `.bak` e registro de alterações em `historico_eventos.jsonl`. Um histórico ilegível bloqueia envios.

A fila inicial exclui contatos enviados, respondidos ou com envio sem confirmação. Telefones com e sem nono dígito são conciliados. A reserva `ENVIANDO` é gravada antes do envio; falhas de confirmação ficam `INCERTO`, sem repetição automática. Cada contato com erro é tentado uma vez por rodada.

Respostas diretas, inclusive mensagens curtas, são registradas separadamente em `respostas_fornecedores.json`. Mensagens do histórico são processadas quando disponibilizadas pelo WhatsApp. O filtro “Sem resposta registrada” não é prova de ausência de resposta: aguarde sincronização e revise as conversas antes de uma campanha futura. A segunda campanha não inicia automaticamente.

Ofertas retiradas na virada do dia ou na limpeza do catálogo são arquivadas em `catalogo_arquivo/`. Limpar o catálogo não afeta o histórico de envios nem as respostas. Todos esses arquivos operacionais devem ser preservados nos deploys.

## Convites de fornecedores

Entradas automáticas ficam desligadas por padrão (`BUSCABUSCA_AUTO_JOIN=0`). A captura de links continua, mas entrar exige comando explícito. A ativação de `BUSCABUSCA_AUTO_JOIN=1` exige autorização do usuário e continua sujeita à validação de escopo.

Antes de aceitar um convite, o motor confere a relação com fornecedores e rejeita grupos de desapego, feira do rolo e vendas gerais. Comunidades não identificadas ficam para revisão. Links enviados pela própria conta e links antigos salvos não concedem autorização para entrada. `convites_bloqueados.json` mantém bloqueios explícitos por código e grupo; deve ser preservado nos deploys. O motor sai desses grupos se uma solicitação antiga vier a ser aprovada. Não existe garantia de cancelamento de pedidos pendentes apenas por ausência na lista de participantes.

Convites enviados por fornecedores cadastrados são capturados em mensagens diretas, mensagens de grupos quando o remetente é um fornecedor conhecido e no histórico sincronizado. Mensagens próprias, remetentes desconhecidos e links apenas citados em respostas não acionam entrada. São aceitos links em texto, legendas e convites nativos com código.

A fila `fila_convites.json` é persistida antes da tentativa e processa um convite por vez quando a operação automática estiver explicitamente habilitada. Elimina duplicatas e tenta novamente erros transitórios até cinco vezes. Convites expirados, restrições, grupos fora de escopo e pedidos de aprovação ficam identificados para conferência, sem tentativas contínuas. Saídas automáticas limitam-se aos grupos explicitamente bloqueados pelo usuário. Entradas confirmadas passam ao monitoramento de listas; o arquivamento só acontece com uma mensagem real disponível. Preservar a fila e os bloqueios em todo deploy.

## Restrição da conta e conferência de envios

Com `BUSCABUSCA_PAUSED=1`, o painel continua acessível, o motor WhatsApp não inicia e os endpoints de envio, entrada em comunidades e sincronização retornam 423. A pausa administrativa não pode ser retirada pela interface. O deploy deve preservar esse ambiente enquanto a conta estiver restrita.

Há até cinco modelos editáveis, escolhidos por fornecedor. São opções de redação, sem promessa de evitar bloqueios. Intervalos antigos de 8–15 segundos passam a 300–600 segundos; o intervalo mínimo também se aplica a comandos individuais. Limite padrão de cinco tentativas por rodada. Um contato precisa estar marcado como autorizado/esperado; essa marcação exige uma base real e não concede autorização por si só. Nenhum limite garante conformidade ou ausência de restrição.

Cada novo envio guarda texto, destino, ID e horário. Uma mensagem criada sem confirmação não aparece como enviada; a rodada pausa para conferência. Confirmações do WhatsApp avançam de pendente para servidor, entregue e lida, quando disponibilizadas. A confirmação do servidor ou entrega ao destinatário não comprova sincronização com o celular do remetente. Sem ID válido, o envio fica incerto e não é repetido automaticamente.

Conversas diretas de fornecedores deixam de ser arquivadas automaticamente pelo sistema. Conversas que já estavam arquivadas não são movidas nem apagadas. Conferir também a pasta de conversas arquivadas no celular. Registros antigos continuam preservados, sem inventar IDs ou recibos ausentes.

Para operação comercial futura, avaliar a WhatsApp Business Platform oficial e as permissões de contato aplicáveis antes de retomar automações. Não reiniciar disparos enquanto a restrição estiver ativa.

## Endereço HTTPS

- Catálogo: https://buscabusca.srv2058756.hstgr.cloud/
- Central: https://buscabusca.srv2058756.hstgr.cloud/admin

O hostname exclusivo resolve para a VPS pelo DNS já existente da Hostinger, sem modificar domínios, registros de email ou sites de outros projetos. `deploy/buscabusca.conf` contém o virtual host Nginx que encaminha para a porta 3050. O certificado exclusivo é renovado pelo Certbot via webroot `/var/www/buscabusca-acme`, com recarga validada do Nginx após renovação. Nenhuma chave privada é armazenada no repositório. O acesso HTTPS não altera a pausa administrativa do WhatsApp.

## Prévia local do novo catálogo

```sh
npm run preview
```

Abra **http://localhost:3054/**. No VS Code, use `Simple Browser: Show` e cole o endereço, ou execute a tarefa `BuscaBusca: prévia local do catálogo`.

A prévia começa e permanece sem produtos, com `/api/products` retornando uma lista vazia. Não há arquivo de exemplos ou produtos de demonstração. Não importa o painel, não conecta o WhatsApp, não escreve no banco e não chama a VPS. O catálogo real continua consumindo sua própria API de produtos.

O catálogo agrupa ofertas por modelo normalizado, capacidade e condição. Grafias conhecidas como PROMAX/PRO MAX e cores equivalentes são reunidas; eSIM e LL/A ficam identificados no nome original da oferta na consulta de fornecedores. Ativados e CPO permanecem separados. Termos desconhecidos são preservados para evitar unir especificações distintas.

A grade usa seis colunas em telas amplas, reduzindo até uma coluna no celular. Os modelos seguem geração e versão, com capacidades decrescentes; as cores mostram o menor custo e são ordenadas por preço. O olho do cabeçalho abre todas as ofertas, e o olho de cada cor filtra essa seleção. A consulta preserva as especificações originais e ordena os fornecedores por preço. A comparação não representa uma pesquisa completa do mercado. Não há calculadora, exportação nem funções de cartão.

Arquivos de interface: `web/index.html`, `web/catalog.css`, `web/catalog.js` e `web/catalog-ui.js`. O servidor Python serve uma lista explícita desses recursos; o deploy copia apenas HTML, CSS e JS, sem arquivos de demonstração. Esta alteração está disponível para revisão local; a VPS permanece na versão publicada anteriormente e suas automações continuam pausadas.
