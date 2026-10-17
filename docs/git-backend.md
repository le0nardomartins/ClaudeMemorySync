# Modo sem server: repositório Git

Use um repositório Git **privado** no lugar do server. Não há deploy, banco nem chave Ed25519: cada PC usa as credenciais do Git que já tem.

O resto do sync é igual: as mesmas pastas, as mesmas exclusões, o mesmo merge de 3 vias, a mesma criptografia e a mesma skill.

> [!IMPORTANT]
> Use `SYNC_KEY`. Com ela, o repositório só guarda dados cifrados: nem o conteúdo, nem o nome dos arquivos aparecem, e o GitHub não vê suas chaves de API. Mesmo assim, deixe o repositório **privado** e separado deste repositório de código.

## Quando escolher cada modo

| Escolha o **modo Git** se... | Escolha o **server** se... |
|---|---|
| Quer começar em 5 minutos, sem pagar nada | Já tem o server rodando |
| Já usa GitHub/GitLab com SSH ou o Git Credential Manager | Quer revogar um PC sem mexer nas permissões do Git |
| Quer o histórico completo de versões | Prefere um histórico curto (3 versões) |

Dá para trocar de modo depois (veja [Trocar de modo](#trocar-de-modo)).

## Passo a passo

### 1. Crie um repositório privado e vazio

No GitHub: **New repository**, marque **Private**, e **não** crie README, licença nem `.gitignore`. Exemplo: `claude-memory-data`.

### 2. Confira se o Git deste PC acessa o repositório

```bash
git ls-remote git@github.com:voce/claude-memory-data.git
```

Sem erro (pode não imprimir nada, porque o repo está vazio), está pronto. Se pedir senha ou falhar, configure uma das opções:
- **SSH:** gere uma chave (`ssh-keygen -t ed25519`) e cadastre em GitHub → Settings → SSH keys.
- **HTTPS:** use o Git Credential Manager (vem com o Git for Windows; no macOS, o Keychain). Faça um `git clone` manual uma vez para ele guardar o login.

O sync roda em segundo plano e **não consegue pedir senha**: a autenticação precisa funcionar sem interação.

### 3. Clone o cliente

```bash
git clone https://github.com/le0nardomartins/ClaudeMemorySync.git
cd ClaudeMemorySync
python -m venv .venv
.venv/Scripts/pip install -r requirements.txt     # macOS/Linux: .venv/bin/pip
```

### 4. Gere a SYNC_KEY (uma vez só)

```bash
.venv/Scripts/python src/components/remote_client.py synckey
```

Use a **mesma** `SYNC_KEY` em todos os PCs e guarde-a num gerenciador de senhas.

### 5. Crie o `.env`

```ini
SYNC_BACKEND=git
GIT_REPO_URL=git@github.com:voce/claude-memory-data.git
SYNC_KEY=...
# opcionais
GIT_BRANCH=main
CLIENT_ID=pc-notebook
```

`CLIENT_ID` só nomeia os commits. Sem ele, o nome do computador é usado.

### 6. Primeira rodada e autostart

```bash
.venv/Scripts/python src/main.py
powershell -ExecutionPolicy Bypass -File install_autostart.ps1     # Windows
./install_autostart.sh                                             # macOS / Linux
```

Saída esperada na primeira rodada:

```
[10:00:03] NEW_MEMORY: 13 itens prontos para envio
[10:00:09] enviar: versão 1 (578 arquivos), 6.20 MB enviados
```

### 7. Outros PCs

Repita os passos 2, 3, 5 e 6 em cada PC, com o **mesmo** `GIT_REPO_URL` e a **mesma** `SYNC_KEY`. Não há chave para cadastrar: basta o Git do PC ter acesso ao repositório.

## Como funciona

```
 PC                                          Repositório Git privado
 ──                                          ───────────────────────
 buscar   git fetch + reset  ◄────────────   meta.json       { "version": N }
                                             manifest.bin    manifesto cifrado
                                             blobs/<id>      conteúdo cifrado
 mesclar  base × local × remoto (no PC)
 enviar   grava blobs novos + manifesto, commit "sync: pc-x -> versão N+1"
          git push  ──────────────────────►  recusado? outro PC enviou antes:
                                             busca de novo e refaz o merge (até 5x)
```

- Os ids em `blobs/` são HMACs (com `SYNC_KEY`): não revelam o conteúdo nem o nome do arquivo.
- A cada versão, blobs que ela não usa saem da árvore. Eles continuam no histórico do Git.
- O clone local fica em `.sync_repo/`, na raiz do projeto. É só um cache: pode ser apagado a qualquer hora, e o próximo sync clona de novo.
- `.gitattributes` com `* -text` garante que o Git nunca mexa nos bytes.

## Tamanho do repositório

O Git guarda todo o histórico. Como dado cifrado não compacta, cada arquivo alterado acrescenta o próprio tamanho ao histórico. Para skills e configs isso é pouco. Se um dia o repo ficar grande, recomece o histórico:

1. Pare o sync em todos os PCs.
2. Apague e recrie o repositório remoto (vazio).
3. Em cada PC, apague `.sync_repo/`.
4. Rode `python src/main.py` em um PC e depois nos outros. As bases (`SHARING_MEMORY/`) continuam valendo.

## Trocar de modo

Use o mesmo procedimento nos dois sentidos (server → Git ou Git → server):
1. Troque o `.env` em todos os PCs.
2. Rode o sync em um PC: o destino novo está vazio, e ele envia tudo.
3. Rode nos outros PCs.

As bases locais continuam valendo, então os PCs só mesclam o que mudou.

## Problemas comuns

| Mensagem | Causa | Solução |
|---|---|---|
| `faltam no .env: GIT_REPO_URL` | `SYNC_BACKEND=git` sem URL. | Acrescente `GIT_REPO_URL`. |
| `git não encontrado no PATH` | Git não instalado, ou fora do PATH do autostart. | Instale o Git e reinicie o autostart. |
| `git clone falhou: ... Permission denied` / `Authentication failed` | O Git deste PC não tem acesso ao repo. | Teste com `git ls-remote <url>`. Configure SSH ou o Credential Manager. |
| `git fetch falhou: ... terminal prompts disabled` | O Git quis pedir senha, e o sync não é interativo. | Faça um clone manual uma vez para o Credential Manager guardar o login, ou use SSH. |
| `git push falhou: ...` | Push recusado por outro motivo que não concorrência (permissão, branch protegida). | Confira se o usuário tem permissão de escrita e se a branch não é protegida. |
| `outro PC enviou antes 5 vezes seguidas` | Vários PCs enviando ao mesmo tempo. | Nada: a próxima rodada tenta de novo. |
