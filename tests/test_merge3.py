from components.merge3 import merge3_json, merge_file


def m(base, local, remote, **kw):
    enc = lambda s: None if s is None else s.encode("utf-8")
    data, conflict = merge_file(kw.pop("rel", "a.md"), enc(base), enc(local), enc(remote), **kw)
    return (None if data is None else data.decode("utf-8")), conflict


def test_edicao_de_linha_substitui_sem_duplicar():
    assert m("a\nb\nc\n", "a\nB2\nc\n", "a\nb\nc\n") == ("a\nB2\nc\n", False)


def test_remocao_de_linha_propaga():
    assert m("a\nb\nc\n", "a\nc\n", "a\nb\nc\nd\n") == ("a\nc\nd\n", False)


def test_edicoes_em_trechos_diferentes():
    assert m("1\n2\n3\n4\n5\n", "1\nX\n3\n4\n5\n", "1\n2\n3\n4\nY\n") == ("1\nX\n3\n4\nY\n", False)


def test_dois_acrescentam_no_mesmo_ponto_mantem_blocos_inteiros():
    # Caso relatado: seções novas no fim de um .md perdiam as linhas em branco
    base = "# T\n"
    local = "# T\n\n## A\n\nfoo\n"
    remote = "# T\n\n## B\n\nbar\n"
    out, conflict = m(base, local, remote)
    assert not conflict
    assert out == "# T\n\n## B\n\nbar\n\n## A\n\nfoo\n"


def test_dois_acrescentam_itens_de_lista_sem_embaralhar():
    out, _ = m("- x\n", "- x\n- a1\n- a2\n", "- x\n- b1\n- b2\n")
    assert out == "- x\n- b1\n- b2\n- a1\n- a2\n"


def test_conflito_ganha_o_lado_indicado():
    assert m("a\nb\nc\n", "a\nL\nc\n", "a\nR\nc\n", prefer_local=True) == ("a\nL\nc\n", True)
    assert m("a\nb\nc\n", "a\nL\nc\n", "a\nR\nc\n", prefer_local=False) == ("a\nR\nc\n", True)


def test_arquivo_apagado_de_um_lado():
    assert m("x", None, "x") == (None, False)     # só um apagou: apaga
    assert m("x", None, "y") == ("y", False)      # o outro editou: edição vence


def test_binario_conflito_respeita_lado_mais_novo():
    data, conflict = merge_file("a.bin", b"\xff\x00", b"\xff\x01", b"\xff\x02", prefer_local=False)
    assert (data, conflict) == (b"\xff\x02", True)


def test_json_por_chave():
    out, conflict = m('{"a":1,"b":2,"l":[1,2,3]}', '{"a":1,"l":[1,3]}', '{"a":1,"b":2,"c":3,"l":[1,2,3,4]}',
                      rel="s.json", sort_json=True)
    assert not conflict
    assert out == '{\n  "a": 1,\n  "c": 3,\n  "l": [\n    1,\n    3,\n    4\n  ]\n}\n'


def test_json_conflito_de_valor():
    assert merge3_json({"k": 1}, {"k": 2}, {"k": 3}, prefer_local=False) == ({"k": 3}, True)
