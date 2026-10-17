# Documentação do Claude Memory Sync (cliente)

Esta pasta tem dois usos:

1. **Documentação** do cliente, para quem quer instalar, entender ou modificar o projeto.
2. **Skill do Claude Code** (`claude-memory-sync`). O `SKILL.md` faz o Claude saber instalar o projeto, adicionar um PC, conferir pendências de MCP e plugin e diagnosticar erros.

## Índice

| Documento | Para quem | Conteúdo |
|---|---|---|
| [setup.md](setup.md) | Quem vai instalar | Pré-requisitos, primeiro PC, PCs seguintes, atualização e desinstalação |
| [architecture.md](architecture.md) | Quem quer entender ou contribuir | Rodada de sync, pastas, protocolo com o server, merge, estado local |
| [troubleshooting.md](troubleshooting.md) | Quem tem um erro | Mensagens do `sync.log`, códigos HTTP e recuperação |
| [SKILL.md](SKILL.md) | O Claude | Regras e roteiro que o Claude segue ao usar a skill |

O server tem a própria pasta `docs/`, no repositório [backend-memory-interconnection-claude](https://github.com/le0nardomartins/backend-memory-interconnection-claude/tree/main/docs).

## Instalar como skill

A skill é só esta pasta, copiada para dentro das skills do Claude Code com o nome `claude-memory-sync`.

**Windows (PowerShell):**

```powershell
git clone --depth 1 https://github.com/le0nardomartins/memory-interconnection-claude.git $env:TEMP\cms
Copy-Item -Recurse $env:TEMP\cms\docs $HOME\.claude\skills\claude-memory-sync
Remove-Item -Recurse -Force $env:TEMP\cms
```

**macOS / Linux:**

```bash
git clone --depth 1 https://github.com/le0nardomartins/memory-interconnection-claude.git /tmp/cms
cp -r /tmp/cms/docs ~/.claude/skills/claude-memory-sync
rm -rf /tmp/cms
```

**Pelo próprio Claude.** Em uma sessão do Claude Code, peça:

> Instale como skill a pasta `docs` do repositório https://github.com/le0nardomartins/memory-interconnection-claude, com o nome `claude-memory-sync`.

Depois de instalada, basta pedir em linguagem natural: *"configure este PC no meu sync do Claude"*, *"o sync está dando 401"*, *"veja se falta instalar algum MCP que veio do outro PC"*.

> Se o sync já estiver rodando, a skill também é sincronizada entre os PCs, porque fica dentro de `skills/`.

## Visão geral em 30 segundos

```
 PC A ──┐                         ┌── PC B
        │  manifesto + só o que   │
        ├──► mudou (assinado) ──► │
        │       SERVER            │
        │   Postgres: 3 versões,  │
        │   cada conteúdo 1 vez   │
        └──◄ só o que falta ◄─────┘
```

- Cada PC envia o **manifesto** dos arquivos (caminho → sha256) e só o conteúdo que o server não tem.
- O server **mescla** com a versão anterior, sem apagar nada, e guarda a nova versão.
- Os outros PCs baixam o manifesto novo, pedem só o que falta e **complementam** a configuração local.
