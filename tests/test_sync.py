import json
import os
import shutil
import subprocess
import time

import pytest

from components import git_backend as gb
from components import sync_engine as eng
from components.crypto_codec import CodecError, new_key

HOUR = 3600


def test_recebe_tudo_sem_exclusoes(pcs):
    a, b = pcs("pc-a"), pcs("pc-b")
    a.write("skills/s/SKILL.md", "um\n")
    a.write("skills/s/node_modules/lib.js", "x")
    a.run()
    b.run()
    assert b.read("skills/s/SKILL.md") == "um\n"
    assert not b.path("skills/s/node_modules").exists()


def test_destino_so_ve_dados_cifrados(pcs, store):
    a = pcs("pc-a")
    a.write("segredo.md", "sk-ant-TOKEN-SUPER-SECRETO\n")
    a.run()
    raw = b"".join(store.blobs.values()) + b"".join(m for _, m in store.versions)
    assert b"SUPER-SECRETO" not in raw and b"segredo.md" not in raw


def test_remocao_propaga_e_vai_para_lixeira(pcs):
    a, b = pcs("pc-a"), pcs("pc-b")
    a.write("agents/x.md", "x\n")
    a.run(); b.run()
    b.path("agents/x.md").unlink()
    b.run(); a.run()
    assert not a.path("agents/x.md").exists()
    assert list((a.d / "trash").rglob("x.md"))


def test_conflito_ganha_o_arquivo_mais_novo(pcs):
    a, b = pcs("pc-a"), pcs("pc-b")
    a.write("s.md", "1\n2\n3\n")
    a.run(); b.run()
    now = time.time()
    a.write("s.md", "1\nNOVO-A\n3\n", mtime=now)             # A editou por último...
    b.write("s.md", "1\nVELHO-B\n3\n", mtime=now - HOUR)     # ...B editou antes
    a.run(); b.run(); a.run()                                # mas B sincroniza por último
    assert a.read("s.md") == b.read("s.md") == "1\nNOVO-A\n3\n"
    assert "s.md" in b.conflicts()


def test_copia_velha_nao_volta_por_cima_da_edicao(pcs):
    """O bug relatado: um PC com o arquivo antigo não pode desfazer a edição feita em outro."""
    a, b = pcs("pc-a"), pcs("pc-b")
    old = time.time() - 2 * HOUR
    a.write("agents/agente.md", "versão antiga\n", mtime=old)
    a.run(); b.run()
    # A edita o agente
    a.write("agents/agente.md", "versão NOVA com a edição\n")
    a.run()
    # B, sem ter recebido, aparece com o conteúdo antigo diferente da base (ex.: base perdida)
    shutil.rmtree(b.d / "BASE")
    b.write("agents/agente.md", "versão antiga, levemente diferente\n", mtime=old)
    b.run(); a.run()
    assert a.read("agents/agente.md") == "versão NOVA com a edição\n"
    assert b.read("agents/agente.md") == "versão NOVA com a edição\n"


def test_remoto_mais_velho_nao_sobrescreve_arquivo_local_mais_novo(pcs):
    a, b = pcs("pc-a"), pcs("pc-b")
    a.write("x.md", "base\n", mtime=time.time() - 3 * HOUR)
    a.run(); b.run()
    b.write("x.md", "edição velha do B\n", mtime=time.time() - 2 * HOUR)
    b.run()
    os.utime(a.path("x.md"))                                  # A mexeu no arquivo depois disso
    a.run()
    assert a.read("x.md") == "base\n"
    assert "x.md" in a.conflicts()


def test_arquivo_recebido_mantem_a_data_original(pcs):
    a, b = pcs("pc-a"), pcs("pc-b")
    t = time.time() - 5 * HOUR
    a.write("agents/y.md", "y\n", mtime=t)
    a.run(); b.run()
    assert abs(b.path("agents/y.md").stat().st_mtime - t) < 2


def test_edicao_durante_o_sync_nao_e_sobrescrita(pcs, store):
    a, b = pcs("pc-a"), pcs("pc-b")
    a.write("agents/agente.md", "linha 1\nlinha 2\n")
    a.run(); b.run()
    a.write("agents/agente.md", "linha 1\nlinha 2\nlinha do A\n")
    a.run()
    # B começa a rodada; enquanto ela está no meio, você edita o agente no B
    store.before_put = lambda: b.write("agents/agente.md", "EDIÇÃO DO B\nlinha 1\nlinha 2\n")
    b.write("outro.md", "força um envio\n")
    b.run()
    assert b.read("agents/agente.md") == "EDIÇÃO DO B\nlinha 1\nlinha 2\n"
    assert any("editado durante o sync" in line for line in b.logs)
    b.run(); a.run()                                          # próxima rodada mescla as duas
    expected = "EDIÇÃO DO B\nlinha 1\nlinha 2\nlinha do A\n"
    assert a.read("agents/agente.md") == b.read("agents/agente.md") == expected


def test_trava_de_remocao_em_massa(pcs):
    a, b = pcs("pc-a"), pcs("pc-b")
    for i in range(20):
        a.write(f"bulk/{i}.md", f"{i}\n")
    a.run(); b.run()
    for i in range(15):
        a.path(f"bulk/{i}.md").unlink()
    with pytest.raises(eng.SyncAborted):
        a.run()
    a.run(allow_delete=True); b.run()
    assert len(list(b.path("bulk").iterdir())) == 5


def test_settings_e_mcp_propagam_sem_mexer_no_que_e_local(pcs):
    a, b = pcs("pc-a"), pcs("pc-b")
    a.run(); b.run()
    s = json.loads(a.settings.read_text()); s["permissions"]["allow"].append("Bash(ls)")
    a.settings.write_text(json.dumps(s))
    c = json.loads(a.claude_json.read_text()); c["mcpServers"]["fig"] = {"url": "x"}
    a.claude_json.write_text(json.dumps(c))
    a.run(); b.run()
    assert "Bash(ls)" in json.loads(b.settings.read_text())["permissions"]["allow"]
    cb = json.loads(b.claude_json.read_text())
    assert "fig" in cb["mcpServers"] and cb["numStartups"] == 3


def test_templates_nao_ficam_indo_e_voltando(pcs, store):
    """Diferença que só existe num PC (caminho do node.exe) não pode gerar envio a cada rodada."""
    a, b = pcs("pc-a"), pcs("pc-b")
    b.settings.write_text(json.dumps({"hooks": {"Stop": [{"hooks": [
        {"command": "C:\\Program Files\\nodejs\\node.exe a.js"}]}]}}))
    a.settings.write_text(json.dumps({"hooks": {"Stop": [{"hooks": [{"command": "node a.js"}]}]}}))
    a.run(); b.run()
    s = json.loads(a.settings.read_text()); s["hooks"]["Stop"][0]["hooks"][0]["command"] = "node b.js"
    a.settings.write_text(json.dumps(s))
    for _ in range(3):
        a.run(); b.run()
    versions = len(store.versions)
    for _ in range(3):
        a.run(); b.run()
    assert len(store.versions) == versions, "os PCs continuam reenviando sem mudança real"


def test_claude_md_e_mesclado(pcs):
    a, b = pcs("pc-a"), pcs("pc-b")
    a.run(); b.run()
    a.claude_md.write_text("# regras\nregra do A\n"); b.claude_md.write_text("# regras\nregra do B\n")
    a.run(); b.run(); a.run()
    assert "regra do A" in b.claude_md.read_text() and "regra do B" in a.claude_md.read_text()


def test_rodada_sem_mudanca_nao_envia(pcs, store):
    a, b = pcs("pc-a"), pcs("pc-b")
    a.write("x.md", "x\n")
    a.run(); b.run(); a.run()
    n = len(store.versions)
    a.run(); b.run()
    assert len(store.versions) == n and a.logs == b.logs == ["sync: nada mudou"]


def test_corrida_entre_pcs_refaz_o_merge(pcs, store):
    a, b = pcs("pc-a"), pcs("pc-b")
    a.run(); b.run()
    a.write("ra.md", "a\n"); b.write("rb.md", "b\n")

    def b_chega_antes():
        b.run(); a.activate()
    store.before_put = b_chega_antes
    a.run(); b.run()
    assert a.path("rb.md").exists() and b.path("ra.md").exists()
    assert any("outro PC enviou antes" in line for line in a.logs)


def test_sync_key_diferente_e_recusada(pcs):
    a = pcs("pc-a")
    a.write("x.md", "x\n"); a.run()
    c = pcs("pc-c", key_override=new_key())
    with pytest.raises(CodecError):
        c.run()


@pytest.mark.skipif(shutil.which("git") is None, reason="git não instalado")
def test_destino_git(tmp_path, pcs):
    remote_repo = tmp_path / "remote.git"
    subprocess.run(["git", "init", "--bare", "-q", "-b", "main", str(remote_repo)], check=True)
    env = lambda n: {"GIT_REPO_URL": remote_repo.as_uri(), "CLIENT_ID": n}
    a = pcs("pc-a", store_override=gb.GitStore(env("pc-a"), tmp_path / "ga"))
    b = pcs("pc-b", store_override=gb.GitStore(env("pc-b"), tmp_path / "gb"))
    a.write("agents/x.md", "x\r\ny\r\n")
    a.run(); b.run()
    assert b.path("agents/x.md").read_bytes() == b"x\r\ny\r\n"
    blob = b"".join(p.read_bytes() for p in (tmp_path / "ga").rglob("*") if p.is_file() and ".git" not in p.parts)
    assert b"agents/x.md" not in blob
