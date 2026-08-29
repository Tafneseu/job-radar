"""Testes das funções puras de relatorio_precisao.py — agregação e
ordenação não tocam banco nenhum, então são testadas com tuplas soltas
(site, encontrada_em, feedback), o mesmo formato que _carregar_linhas
devolveria de uma consulta real. Consulta em si (_carregar_linhas) e I/O
(main) não são testados aqui pelo mesmo motivo do resto da suíte: o que
vale testar é a conta, não a chamada ao SQLite."""

import pytest

from relatorio_precisao import _taxa, agregar_por_fonte, agregar_por_semana, ordenar_pior_primeiro

CASOS_TAXA = [
    ("sem-base-mostra-traco", 0, 0, "—"),
    ("zero-positivas-com-base", 0, 10, "0%"),
    ("metade", 5, 10, "50%"),
    ("tudo-positivo", 10, 10, "100%"),
    # Arredondamento: 1/3 = 33.33...% -> "33%", não trunca nem exibe casa decimal.
    ("arredonda-sem-casa-decimal", 1, 3, "33%"),
]


@pytest.mark.parametrize("nome,positivas,base,esperado", CASOS_TAXA, ids=[c[0] for c in CASOS_TAXA])
def test_taxa(nome, positivas, base, esperado):
    assert _taxa(positivas, base) == esperado


def test_agregar_por_fonte_conta_notificadas_positivas_negativas():
    linhas = [
        ("Gupy", "2026-08-07 10:00:00", "positivo"),
        ("Gupy", "2026-08-07 11:00:00", "negativo"),
        ("Gupy", "2026-08-07 12:00:00", None),  # sem reação -- conta só em notificadas
        ("Catho", "2026-08-08 09:00:00", "negativo"),
    ]
    agregados = agregar_por_fonte(linhas)
    assert agregados["Gupy"] == (3, 1, 1)
    assert agregados["Catho"] == (1, 0, 1)


def test_agregar_por_semana_agrupa_por_semana_iso():
    linhas = [
        # 2026-08-07 é sexta da semana ISO 32; 2026-08-10 é segunda da 33.
        ("Gupy", "2026-08-07 10:00:00", "positivo"),
        ("Gupy", "2026-08-10 10:00:00", "negativo"),
    ]
    agregados = agregar_por_semana(linhas)
    assert agregados[(2026, 32)] == (1, 1, 0)
    assert agregados[(2026, 33)] == (1, 0, 1)


def test_agregar_por_semana_ignora_data_invalida_sem_quebrar():
    linhas = [
        ("Gupy", "data-corrompida", "positivo"),
        ("Gupy", None, "positivo"),
        ("Gupy", "2026-08-07 10:00:00", "positivo"),
    ]
    agregados = agregar_por_semana(linhas)
    assert sum(v[0] for v in agregados.values()) == 1  # só a linha válida entrou


def test_ordenar_pior_primeiro_usa_taxa_entre_avaliadas():
    # Catho: 0% (3 avaliadas, 0 positiva) -- pior, tem que vir primeiro.
    # Solides: 50% (2 avaliadas) -- meio.
    # Gupy: 100% (3 avaliadas, 3 positivas) -- melhor, por último entre avaliados.
    # GeekHunter: sem avaliação nenhuma -- não é "pior" nem "melhor", vai pro fim de tudo.
    agregados = {
        "Gupy": (41, 3, 0),
        "Catho": (21, 0, 3),
        "Solides": (13, 1, 1),
        "GeekHunter": (5, 0, 0),
    }
    ordenados = [site for site, _ in ordenar_pior_primeiro(agregados)]
    assert ordenados == ["Catho", "Solides", "Gupy", "GeekHunter"]
