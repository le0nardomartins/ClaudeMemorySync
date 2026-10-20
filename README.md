# Claude Memory Sync

**Português** · [English](README.en.md)

Mantém a configuração do **Claude Code** igual em todos os seus computadores: skills, agentes, slash commands, hooks, output styles, o `CLAUDE.md` global, o `settings.json`, os plugins ativos e os servidores MCP.

Você trabalha normalmente em qualquer PC. A cada 15 minutos cada PC envia o que mudou e recebe o que os outros mudaram. Edições, inclusões e **remoções** se propagam, com merge de 3 vias, como o Git faz.

> **Este repositório é o cliente** (Python, roda em cada PC). Ele sincroniza por um de dois destinos:
> - **Server próprio** (padrão): [ClaudeMemorySync-Server](https://github.com/le0nardomartins/ClaudeMemorySync-Server), em Node + Postgres, feito para a Railway.
> - **Repositório Git privado**, sem server nenhum: basta um repo vazio no GitHub, GitLab ou similar.

> [!WARNING]
> A sincronização inclui as **chaves de API** do `settings.json` e dos MCPs. Defina uma `SYNC_KEY` para tudo ser **criptografado no PC** antes de sair (veja [Segurança](#segurança)). Nunca publique `NEW_MEMORY/`, `SHARING_MEMORY/` ou o `.env`.

---

## Sumário

- [Por que existe](#por-que-existe)
- [Como funciona](#como-funciona)
- [Dois destinos: server ou repositório Git](#dois-destinos-server-ou-repositório-git)
- [Requisitos](#requisitos)
- [Início rápido](#início-rápido)
- [Adicionar outro PC](#adicionar-outro-pc)
- [O que é sincronizado](#o-que-é-sincronizado)
- [Como o merge funciona](#como-o-merge-funciona)
- [Comandos](#comandos)
- [Segurança](#segurança)
- [Limitações](#limitações)
- [Problemas comuns](#problemas-comuns)
- [Estrutura do projeto](#estrutura-do-projeto)
- [Documentação completa e skill para o Claude](#documentação-completa-e-skill-para-o-claude)

---

## Por que existe

O Claude Code guarda tudo em `~/.claude` e `~/.claude.json`, e cada computador tem a própria cópia. Criou uma skill no notebook? Ela não existe no desktop. Configurou um MCP no trabalho? Precisa refazer em casa.

Copiar a pasta inteira não resolve: ela tem caminhos absolutos (`C:\Users\fulano\...`), dados de sessão, logins e arquivos que só fazem sentido em uma máquina. Este projeto sincroniza **só o que é portátil**, troca os caminhos por tokens neutros e **mescla** as mudanças de vários PCs em vez de sobrescrever uma com a outra.

## Como funciona

```
 Cada PC (a cada 15 min)
 ───────────────────────
 1. PREPARAR    ~/.claude/claude_memory_sharing → NEW_MEMORY/      (estado LOCAL, caminhos portáteis)

 2. BUSCAR      destino → manifesto da versão mais nova            (REMOTO)

 3. MESCLAR     BASE (SHARING_MEMORY/, último sync) × LOCAL × REMOTO
                - mudou só de um lado      → entra (inclusive remoção)
                - mudou dos dois lados     → merge por linha / chave JSON
                - conflito real            → ganha o arquivo MAIS NOVO; o outro vai para .sync_conflicts/
                - cópia desatualizada      → nunca volta por cima de uma versão mais nova

 4. ENVIAR      resultado cifrado + "me baseei na versão N"
                destino já está na N+1?    → busca de novo, refaz o merge (passo 2)

 5. APLICAR     resultado → ~/.claude/claude_memory_sharing, settings.json, ~/.claude.json, CLAUDE.md
                arquivo removido → .sync_trash/ (nunca apagado de vez)
```

- **O que viaja:** um **manifesto** (caminho → hash e tamanho de cada arquivo) e só o conteúdo que o destino ainda não tem. Alterar uma linha de uma skill gera um envio de poucos KB.
- **O destino é só um armazém.** Ele guarda versões e conteúdo selado. Quem faz o merge é cada PC, então o destino não precisa ler nada, e com `SYNC_KEY` não consegue.

| Pasta (na raiz do projeto, exceto a primeira) | Papel |
|---|---|
| `~/.claude/claude_memory_sharing/` | A memória de verdade. `~/.claude/skills`, `agents`, `commands`, `hooks`, `output-styles`, `design-references` e `mods` passam a ser *junctions* (links de pasta) apontando para cá. |
| `NEW_MEMORY/` | Estado **local** deste PC, em formato portátil. |
| `SHARING_MEMORY/` | **Base**: a versão em que este PC e o destino concordaram no último sync. É o que permite saber quem mudou o quê. |
| `.sync_conflicts/` | Versões que perderam um conflito, por data. |
| `.sync_trash/` | Arquivos removidos por outro PC, por data. |

Detalhes em [docs/architecture.md](docs/architecture.md).

## Dois destinos: server ou repositório Git

O fluxo acima é igual nos dois. Muda só **onde as versões ficam guardadas**.

| | Server (padrão) | Repositório Git (sem server) |
|---|---|---|
| Onde fica | Node + Postgres na Railway (ou outro host) | Um repositório **privado** no GitHub, GitLab, Gitea... |
| Autenticação | Chave Ed25519 por PC | As credenciais do Git que você já usa (SSH ou HTTPS) |
| Concorrência | O server recusa envio baseado em versão velha (409) | O Git recusa o push |
| Custo | Plano da Railway | Grátis |
| Trabalho para subir | Deploy + banco + variáveis | Criar um repo vazio |
| Histórico | 3 últimas versões | Todo o histórico do Git |

Para escolher o modo Git, use no `.env`:

```ini
SYNC_BACKEND=git
GIT_REPO_URL=git@github.com:voce/claude-memory-data.git
```

Passo a passo em [docs/git-backend.md](docs/git-backend.md).

## Requisitos

| | |
|---|---|
| Sistema | Windows 10/11, macOS ou Linux. O autostart usa a tarefa agendada (Windows), o launchd (macOS) ou o systemd de usuário/cron (Linux). |
| Python | 3.10 ou mais novo, com `pip install -r requirements.txt` (só `cryptography`). |
| Destino | Uma instância do [server](https://github.com/le0nardomartins/ClaudeMemorySync-Server) no ar **ou** um repositório Git privado e vazio, com o `git` instalado em cada PC. |
| Claude Code | Instalado em cada PC, com `~/.claude` já criado. |

## Início rápido

> Para usar um repositório Git em vez do server, siga [docs/git-backend.md](docs/git-backend.md): os passos 1, 3 e 5 mudam e o resto é igual.

**1. Suba o server.** Siga o README do [server](https://github.com/le0nardomartins/ClaudeMemorySync-Server). Ao final você terá uma URL como `https://seu-app.up.railway.app`.

**2. Clone este repositório e instale a dependência:**

```bash
git clone https://github.com/le0nardomartins/ClaudeMemorySync.git
cd ClaudeMemorySync
python -m venv .venv
.venv/Scripts/pip install -r requirements.txt     # macOS/Linux: .venv/bin/pip
```

**3. Gere o par de chaves deste PC:**

```bash
.venv/Scripts/python src/components/remote_client.py keygen pc-notebook-x7k2
```

A saída tem duas partes:
- `CLIENT_ID` e `CLIENT_PRIVATE_KEY`: vão para o `.env` deste PC. São privadas.
- `pc-notebook-x7k2:<base64>`: vai para a variável `CLIENT_PUBLIC_KEYS` do server. Separe os PCs com vírgula. Essa linha pode ser pública.

**4. Gere a chave de criptografia, uma única vez para todos os PCs:**

```bash
.venv/Scripts/python src/components/remote_client.py synckey
```

A linha `SYNC_KEY=...` vai **igual** no `.env` de todos os seus PCs. Guarde-a num gerenciador de senhas: sem ela, ninguém (nem você) lê o que está no destino.

**5. Crie o `.env`** na raiz do projeto:

```ini
SERVER_URL=https://seu-app.up.railway.app
CLIENT_ID=pc-notebook-x7k2
CLIENT_PRIVATE_KEY=...
SYNC_KEY=...
```

**6. Rode uma vez e confira a saída:**

```bash
.venv/Scripts/python src/main.py
```

Na primeira execução o sync faz três coisas:
- cria `~/.claude/claude_memory_sharing`;
- move `skills`, `agents` e as outras pastas portáteis para lá e cria links no lugar delas;
- gera os templates de settings e MCP e envia tudo, cifrado.

**7. Deixe rodando sozinho:**

| Sistema | Comando |
|---|---|
| Windows | `powershell -ExecutionPolicy Bypass -File install_autostart.ps1` |
| macOS / Linux | `./install_autostart.sh` |

O log fica em `sync.log`. Para remover, use `-Uninstall` (Windows) ou `--uninstall` (macOS/Linux).

## Adicionar outro PC

Repita os passos 2, 3, 5, 6 e 7 no outro computador, com um nome de PC diferente e a **mesma** `SYNC_KEY`, e acrescente a chave pública dele em `CLIENT_PUBLIC_KEYS` no server.

Há um jeito mais simples: abra o Claude Code no outro PC e peça para ele usar a skill deste projeto (veja abaixo). Ela conduz a instalação, os backups e a verificação dos MCPs e plugins que vieram do primeiro PC. O passo a passo manual está em [docs/setup.md](docs/setup.md).

## O que é sincronizado

| Sincronizado | Não sincronizado |
|---|---|
| `skills/`, `agents/`, `commands/`, `hooks/`, `output-styles/`, `design-references/`, `mods/` | `node_modules/`: reinstale com `npm ci` (o log avisa quais skills precisam) |
| `~/.claude/CLAUDE.md` global | `skills/synced/`: gerenciada pelo app Claude Desktop em cada PC |
| `~/.claude/settings.json`: permissões, hooks, env, plugins ativos | `.git/` e `__pycache__/` |
| `mcpServers` do `~/.claude.json` (só os globais) | Login do Claude (`.credentials.json`), histórico e sessões |
| Comandos para reinstalar os plugins (`plugins-install.txt`) | MCPs de projeto (`.mcp.json`) |

A sincronização leva a **configuração** de MCPs e plugins, mas não o programa. Se um MCP aponta para um executável que só existe no outro PC, ele precisa ser instalado neste. A skill do projeto faz essa verificação.

## Como o merge funciona

O merge é de **3 vias**: cada lado é comparado com a **base** (a versão do último sync), então dá para saber quem mudou o quê.

| Situação | Resultado |
|---|---|
| Só um PC mudou um trecho | Entra a mudança. Editar uma linha **substitui** a linha (não duplica). |
| Só um PC removeu uma linha, chave ou arquivo | A remoção se propaga. |
| Os dois PCs mudaram trechos diferentes do mesmo arquivo | Entram as duas mudanças. |
| Os dois acrescentaram linhas no mesmo ponto (ex.: seção nova no fim) | Entram os dois blocos inteiros, um depois do outro. |
| Os dois mudaram o mesmo trecho de forma diferente | **Conflito**: fica o trecho do arquivo **mais novo** (data de modificação); a outra versão é guardada em `.sync_conflicts/` e aparece no log. |
| Um PC "mudou" o arquivo para um conteúdo mais **velho** que o do outro (cópia desatualizada, base perdida) | Fica a versão mais nova. Uma cópia antiga nunca volta por cima de uma edição. |
| O arquivo existe nos dois PCs, diferente, e não há base (PC novo) | Fica o arquivo mais novo inteiro; o outro vai para `.sync_conflicts/`. |
| Você edita um arquivo enquanto o sync está rodando | A edição é mantida; o sync não grava por cima e mescla na rodada seguinte. |
| Um PC removeu o arquivo e o outro o editou | A edição vence: o arquivo fica. |

Detalhes por tipo de arquivo:
- **JSON** é mesclado por chave, e listas por item. É assim com `settings.json` (permissões, hooks, env) e com os `mcpServers` do `~/.claude.json`, que recebem só a diferença, com backup `.bak-sync`.
- **`CLAUDE.md`** global passa pelo mesmo merge de texto, também com backup `.bak-sync`.
- **Binário** mudado dos dois lados conta como conflito no arquivo inteiro.
- **"Mais novo" é a data de modificação do arquivo**, que viaja junto com ele. Arquivo recebido mantém a data da edição original. Por isso os relógios dos PCs precisam estar certos (sincronização automática de horário ligada).

**Trava contra remoção em massa:** se mais de 20% dos arquivos sumirem de um PC de uma vez (e mais de 10), por exemplo por uma junction quebrada, o sync para e pede confirmação (`--allow-delete`) em vez de apagar tudo nos outros PCs. Arquivo removido por outro PC vai para `.sync_trash/`, nunca é apagado de vez.

## Comandos

| Comando | O que faz |
|---|---|
| `python src/main.py` | Uma rodada completa: preparar → buscar → mesclar → enviar → aplicar. |
| `python src/main.py --loop` | Roda para sempre, a cada 15 minutos (`--interval <segundos>` para mudar). |
| `python src/main.py --no-upload` | Só recebe e aplica. O que mudou aqui é enviado na próxima rodada normal. |
| `python src/main.py --offline` | Só prepara a `NEW_MEMORY`, sem falar com o destino. |
| `python src/main.py --allow-delete` | Libera uma remoção grande feita de propósito. |
| `python src/components/remote_client.py keygen <pc>` | Gera as chaves Ed25519 de um PC (modo server). |
| `python src/components/remote_client.py synckey` | Gera a `SYNC_KEY` (uma vez, para todos os PCs). |
| `install_autostart.ps1 [-Interval 900] [-Uninstall]` | Autostart no Windows (tarefa agendada). |
| `install_autostart.sh [--interval 900] [--restart] [--uninstall]` | Autostart no macOS (launchd) e no Linux (systemd ou cron). |
| `restart_sync.bat` | Reinicia a tarefa no Windows (use depois de um `git pull`). |

Só uma sincronização roda por vez em cada PC (`.sync.lock`). Uma segunda execução encerra sozinha.

## Segurança

- **Criptografia no PC (`SYNC_KEY`).** O conteúdo e o manifesto são cifrados com AES-256-GCM antes de sair. O id de cada arquivo no destino é um HMAC, que não revela o conteúdo nem permite confirmar se um arquivo conhecido está lá. Nem o nome dos arquivos aparece. Um vazamento do banco ou do repositório não expõe suas chaves de API.
- **Sem senha compartilhada no server.** Cada PC tem um par de chaves Ed25519. A chave privada fica só no `.env` do PC, e o server guarda apenas as públicas.
- **Toda requisição é assinada.** A assinatura cobre o método, a rota, o horário e o hash do corpo. O server recusa assinatura reenviada, corpo alterado e pedido com mais de 5 minutos.
- **HTTPS obrigatório.** O cliente recusa `http://`, exceto para `localhost`.
- **Integridade.** Todo conteúdo recebido é conferido (GCM e sha256), e caminhos como `../` são recusados.
- **Revogar um PC:** apague a linha dele em `CLIENT_PUBLIC_KEYS` (server) ou tire o acesso dele ao repositório (Git). Se a máquina foi comprometida, troque também a `SYNC_KEY` em todos os PCs ([como](docs/troubleshooting.md#trocar-a-sync_key)).

Sem `SYNC_KEY`, o sync funciona, mas o conteúdo vai só compactado, sem sigilo.

## Limitações

- **Histórico curto (server).** O server guarda só as 3 últimas versões. Não é backup, mas `.sync_trash/` e `.sync_conflicts/` guardam o que saiu de cada PC.
- **Programas não viajam.** MCPs com executável, plugins e logins (OAuth do Figma, por exemplo) precisam ser instalados ou autenticados em cada PC.
- **Relógio.** Conflitos são decididos pela data de modificação: um PC com o relógio muito adiantado ou atrasado faz suas edições parecerem mais novas ou mais velhas do que são. A versão descartada sempre fica em `.sync_conflicts/`.
- **Histórico do modo Git cresce para sempre.** Cada sync é um commit; para configs isso é pouco, mas dá para recomeçar o histórico ([como](docs/git-backend.md#tamanho-do-repositório)).
- **A `SYNC_KEY` precisa ser igual em todos os PCs.** Perdeu a chave? Gere outra e recomece o destino ([como](docs/troubleshooting.md#trocar-a-sync_key)): os PCs reenviam tudo.

## Problemas comuns

| Sintoma | Causa provável |
|---|---|
| `não consegui decifrar: a SYNC_KEY deste PC é diferente` | A `SYNC_KEY` do `.env` não é a mesma dos outros PCs. |
| `os dados estão criptografados: defina a mesma SYNC_KEY` | Falta a `SYNC_KEY` neste PC. |
| `N arquivos sumiram deste PC ... --allow-delete` | A trava de remoção em massa disparou. Confira as junctions; se a remoção foi de propósito, rode com `--allow-delete`. |
| `conflito em N arquivo(s): ficou a versão mais nova` | Dois PCs mudaram o mesmo trecho, ou um tinha uma cópia velha. A versão descartada está em `.sync_conflicts/`. |
| `editado durante o sync, mantido como está` | Você editou o arquivo enquanto a rodada rodava. Nada foi sobrescrito; a próxima rodada mescla. |
| `HTTP 401` | A chave pública deste PC não está em `CLIENT_PUBLIC_KEYS`, o `CLIENT_ID` não bate ou o relógio está muito errado (±5 min). |
| `HTTP 415` / `formato antigo` | Cliente e server em versões diferentes. Atualize os dois (`git pull`) e reinicie o sync. |
| `git push falhou` / `git clone falhou` (modo Git) | O `git` deste PC não consegue acessar o `GIT_REPO_URL`. Teste com `git ls-remote <url>`. |
| `aviso: rode 'npm ci' em ...` | Uma skill com `package.json` chegou sem `node_modules`. Rode `npm ci` na pasta indicada. |

Mais casos em [docs/troubleshooting.md](docs/troubleshooting.md).

## Estrutura do projeto

```
src/
  main.py                          rodada completa, --loop, lock, log
  components/
    sync_claude_templates.py       junctions/links + templates de settings/MCP (caminhos → tokens)
    sync_engine.py                 a rodada: base × local × remoto, envio, aplicação, trava, lixeira
    merge3.py                      merge de 3 vias de texto, JSON e binário
    crypto_codec.py                SYNC_KEY: AES-256-GCM + ids por HMAC
    remote_client.py               destino server (HTTP assinado), bundle, keygen, synckey
    git_backend.py                 destino repositório Git privado
docs/                              documentação completa (também é uma skill do Claude)
docs/en/                           a mesma documentação em inglês (também é skill)
install_autostart.ps1 / .bat       autostart no Windows
install_autostart.sh               autostart no macOS e no Linux
restart_sync.bat                   reinicia a tarefa no Windows depois de atualizar o código
requirements.txt
```

Arquivos locais que **não** vão para o git: `.env`, `NEW_MEMORY/`, `SHARING_MEMORY/`, `.sync_repo/`, `.sync_conflicts/`, `.sync_trash/`, `.sync.lock` e `sync.log`.

## Desenvolvimento

```bash
pip install -r requirements.txt -r requirements-dev.txt
python -m pytest -q tests
```

Os testes cobrem o merge de 3 vias (`tests/test_merge3.py`) e rodadas completas entre vários PCs simulados (`tests/test_sync.py`): conflitos, cópias desatualizadas, edição durante o sync, remoções, templates, criptografia e o destino Git. Rodam no GitHub Actions em Windows, macOS e Linux.

## Licença

[MIT](LICENSE).

## Documentação completa e skill para o Claude

A pasta [`docs/`](docs/) tem a documentação detalhada e também é uma **skill do Claude Code** (`claude-memory-sync`). Com ela instalada, o Claude de qualquer PC consegue instalar o projeto, configurar um PC novo, conferir pendências de MCP e plugin e diagnosticar erros.

```bash
git clone --depth 1 https://github.com/le0nardomartins/ClaudeMemorySync.git /tmp/cms
cp -r /tmp/cms/docs ~/.claude/skills/claude-memory-sync     # Windows: Copy-Item -Recurse
```

Outra forma é pedir ao próprio Claude: *"instale como skill a pasta docs de https://github.com/le0nardomartins/ClaudeMemorySync"*.

| Documento | Conteúdo |
|---|---|
| [docs/setup.md](docs/setup.md) | Instalação do zero, primeiro PC e PCs seguintes |
| [docs/git-backend.md](docs/git-backend.md) | Modo sem server, com um repositório Git privado |
| [docs/architecture.md](docs/architecture.md) | Rodada, merge de 3 vias, protocolo, criptografia |
| [docs/troubleshooting.md](docs/troubleshooting.md) | Erros, logs e como recuperar |
| [docs/SKILL.md](docs/SKILL.md) | Instruções que o Claude segue quando usa a skill |

Documentação em inglês: [docs/en/](docs/en/), que também funciona como skill.
