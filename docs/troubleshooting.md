# Problemas e soluções

Comece pelo log: `sync.log` na raiz do projeto, quando roda pelo autostart, ou a saída de `python src/main.py` no terminal.

## Mensagens do log

| Mensagem | Significado | O que fazer |
|---|---|---|
| `sync: nada mudou` | Nem este PC nem o destino mudaram desde o último sync. | Nada. |
| `enviar: versão N (... arquivos), X enviados` | As mudanças deste PC viraram a versão N. | Nada. |
| `receber: N mudança(s) aplicada(s)` | Mudanças de outro PC foram aplicadas aqui. Itens com `-` na frente foram removidos (e estão em `.sync_trash/`). | Se vier MCP, plugin ou hook novo, veja [Depois de instalar](setup.md#depois-de-instalar). |
| `enviar: outro PC enviou antes; buscando de novo` | Dois PCs sincronizaram ao mesmo tempo. Este busca a versão nova e refaz o merge. | Nada. |
| `conflito em N arquivo(s): ficou a versão mais nova` | Os dois PCs mudaram o mesmo trecho, ou um tinha uma cópia mais velha. Ficou a versão mais nova (data de modificação), e a outra foi guardada. A mensagem diz de qual PC era a descartada. |
| `editado durante o sync, mantido como está (entra na próxima rodada)` | Você editou o arquivo enquanto a rodada rodava. O sync não gravou por cima. | Nada: a próxima rodada mescla a sua edição com o que veio do outro PC. | Se quiser a outra versão, ela está em `.sync_conflicts/<data>/`. Copie o que precisar e o próximo sync espalha. |
| `N arquivos sumiram deste PC desde o último sync ... --allow-delete` | A trava de remoção em massa disparou: mais de 20% dos arquivos (e mais de 10) sumiram de uma vez. | Confira se as junctions de `~/.claude` estão inteiras. Se a remoção foi de propósito, rode `python src/main.py --allow-delete` uma vez. |
| `não consegui decifrar: a SYNC_KEY deste PC é diferente da dos outros` | A `SYNC_KEY` do `.env` não bate. | Copie a `SYNC_KEY` certa de outro PC. |
| `os dados estão criptografados: defina a mesma SYNC_KEY dos outros PCs` | Falta a `SYNC_KEY` neste PC. | Acrescente-a ao `.env`. |
| `SYNC_KEY precisa ter 32 bytes em base64` | A chave foi copiada pela metade. | Copie a linha inteira. |
| `já existe uma sincronização rodando neste PC; saindo` | O autostart está no meio de uma rodada. | Normal em execução manual. Tente de novo em instantes. |
| `SERVER_URL não definida` / `GIT_REPO_URL não definida` | Falta o `.env` ou essa linha. | Crie o `.env` ([setup.md](setup.md#5-criar-o-env)). |
| `aviso: rode 'npm ci' em <pasta>` | Uma skill com `package.json` chegou sem `node_modules` (que não é sincronizado). | `cd <pasta>; npm ci` |
| `[aviso] <pasta> e <pasta> têm conteúdo: junte manualmente` | `~/.claude/<pasta>` é uma pasta real e a do sharing também tem conteúdo. | Mova os arquivos de `~/.claude/<pasta>` para `claude_memory_sharing/<pasta>`, apague a pasta vazia e rode de novo. |

## Códigos HTTP

| Código | Causa | Solução |
|---|---|---|
| `401` | Chave pública ausente ou errada em `CLIENT_PUBLIC_KEYS`, `CLIENT_ID` diferente do nome usado lá, ou relógio do PC errado em mais de 5 min. | Confira as três coisas. No Windows: Configurações → Hora → Sincronizar agora. |
| `413` | Envio maior que `MAX_UPLOAD_MB` (padrão 50). | Aumente a variável no server ou tire arquivos grandes do sharing. |
| `415` ou `formato antigo ou inválido` | Cliente e server em versões de protocolo diferentes. | Atualize os dois: `git pull` + reinício do sync, e deploy do server. |
| `429` | 10 falhas de autenticação em 15 min a partir deste IP. | Corrija o `.env` e espere 15 min. |
| `503` | O server já está processando 2 envios pesados. | Nada: a próxima rodada tenta de novo. |
| `não consegui falar com ...` | Server fora do ar, URL errada ou sem internet. | Confira a `SERVER_URL` e o serviço na Railway. Com o sleep ativo, o primeiro pedido pode demorar alguns segundos. |

O `409` não aparece como erro: o cliente trata sozinho, buscando de novo ou reenviando o que faltou.

## Modo Git

Erros do modo sem server (`git push falhou`, `git clone falhou`, `terminal prompts disabled` e outros): veja a tabela em [git-backend.md](git-backend.md#problemas-comuns).

## Situações

**Apaguei um arquivo e ele sumiu dos outros PCs também. Quero de volta.**
Em cada PC que recebeu a remoção, ele está em `.sync_trash/<data>/<caminho>`. Copie de volta para `~/.claude/claude_memory_sharing/<caminho>`: o próximo sync espalha para os outros.

**Um arquivo voltou depois de apagado.**
Outro PC editou o arquivo depois que você o apagou (edição vence remoção). Apague de novo: se ninguém mexer nele, a remoção se propaga.

**Uma mudança de outro PC não chegou.**
O outro PC precisa enviar primeiro, e depois este precisa receber. Rode `python src/main.py` no outro PC e depois neste. No log do outro PC deve aparecer `enviar: versão N`.

**Quero refazer a base do zero.**
Apague `SHARING_MEMORY/` e rode `python src/main.py`. Sem base, nada é removido: o que só existe de um lado entra no outro, e arquivo diferente nos dois lados fica com a versão mais nova (a outra vai para `.sync_conflicts/`).

**Uma edição minha foi trocada por uma versão antiga.**
A versão descartada está em `.sync_conflicts/<data>/` (no PC que a descartou): copie de volta. Confira também se o relógio dos dois PCs está certo, já que o "mais novo" é decidido pela data de modificação.

### Trocar a SYNC_KEY

Use quando perdeu a chave ou quando um PC foi comprometido.
1. Pare o sync em todos os PCs.
2. Gere uma nova chave: `python src/components/remote_client.py synckey`.
3. Ponha a nova `SYNC_KEY` no `.env` de todos os PCs.
4. Esvazie o destino:
   - **server:** `TRUNCATE cms_versions, cms_blobs;` no Postgres;
   - **Git:** apague e recrie o repositório.
5. Rode `python src/main.py` em um PC (ele envia tudo, com a chave nova) e depois nos outros.

As bases (`SHARING_MEMORY/`) continuam valendo: os PCs só mesclam o que mudou.

**Um merge estragou o `settings.json` ou o `~/.claude.json`.**
Antes de mudar esses arquivos, o sync guarda o anterior em `settings.json.bak-sync` e `.claude.json.bak-sync`. O mesmo vale para `CLAUDE.md.bak-sync`.

**Um MCP não conecta depois do sync.**
Quase sempre ele veio de outro PC e aponta para um programa que não existe aqui. Veja o `command` em `~/.claude.json` → `mcpServers` e instale o programa. Não apague a entrada: a remoção se propagaria para o outro PC, que usa o MCP.

**Mudei o código e o sync continua igual.**
O autostart mantém o processo antigo carregado. Reinicie: `restart_sync.bat` (Windows) ou `./install_autostart.sh --restart` (macOS/Linux).

**Testar sem afetar os outros PCs.**
- `python src/main.py --no-upload` recebe e aplica sem enviar. As mudanças locais saem na próxima rodada normal.
- `python src/main.py --offline` só gera a `NEW_MEMORY`, sem rede.
