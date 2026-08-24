"""99Jobs: a frase que decide "busca vazia" precisa conter o número.

MEDIDO (2026-08-22) ao vivo, mesma pagina, tres termos:

    'analista de dados'     -> 6 cards | "Foram encontrados 3 oportunidades..."
    'power bi'              -> 4 cards | frase presente
    'business intelligence' -> 0 cards | "Foram encontrados 0 oportunidades..."

A checagem antiga era `"oportunidades para o termo" in texto_pagina` — e essa
frase aparece nos TRES casos. Ela nao discriminava nada: qualquer timeout
virava "0 resultados reais". No dia em que a 99Jobs saisse do ar, o log diria
"busca vazia" e a fonte morreria em silencio.

COMO O BUG NASCEU, pelo comentario que estava no arquivo: a versao original
procurava o "0" junto de "oportunidades", mas os dois ficam em elementos HTML
separados e nunca batiam como texto contiguo em page.content(). A correcao
trocou content() por inner_text() — e nessa troca o "0" caiu da comparacao. O
conserto de um problema real levou junto a unica parte que discriminava.

(A pagina renderiza cada vaga DUAS vezes: 6 cards para 3 oportunidades. A
deduplicacao por link ja resolve isso — os dois cards tem o mesmo href.)
"""

import pytest

from scrapers.jobs99 import classificar_timeout

COM_RESULTADO = (
    "Tipo de Oportunidade | Empresa | "
    "Foram encontrados 3 oportunidades para o termo: \u201canalista de dados\u201d | "
    "ANALISTA DE DADOS E AUTOMACAO"
)
SEM_RESULTADO = (
    "Tipo de Oportunidade | Empresa | "
    "Foram encontrados 0 oportunidades para o termo: \u201cbusiness intelligence\u201d"
)
PAGINA_QUEBRADA = "Para Empresas | Solucoes em Talentos | Acessar | Cadastre-se"


def test_zero_oportunidades_e_busca_vazia():
    assert classificar_timeout(SEM_RESULTADO) == "vazio"


def test_pagina_que_declara_vagas_mas_nao_renderiza_e_falha():
    """O caso que o bug escondia: a pagina DIZ que ha 3 vagas e nao mostra card.

    Isso e falha de renderizacao, nao busca vazia — e tem que aparecer como
    erro, nao como "0 resultados reais".
    """
    assert classificar_timeout(COM_RESULTADO) == "falha"


def test_pagina_sem_a_frase_e_falha():
    """Fonte fora do ar nao traz o total. Nao pode virar "busca vazia"."""
    assert classificar_timeout(PAGINA_QUEBRADA) == "falha"
    assert classificar_timeout("") == "falha"


@pytest.mark.parametrize("total", ["0", "1", "3", "40", "100"])
def test_so_o_zero_de_verdade_conta_como_vazio(total):
    corpo = f"Foram encontrados {total} oportunidades para o termo: \u201cx\u201d"
    esperado = "vazio" if total == "0" else "falha"
    assert classificar_timeout(corpo) == esperado


def test_total_terminado_em_zero_nao_e_vazio():
    """Mesmo cuidado que a Solides precisou: "40" nao pode casar como "0"."""
    corpo = "Foram encontrados 40 oportunidades para o termo: \u201cdados\u201d"
    assert classificar_timeout(corpo) == "falha"


def test_frase_antiga_sozinha_nao_decide_mais():
    """Trava a regressao: era exatamente esta frase que passava sempre."""
    corpo = "algo | oportunidades para o termo | outra coisa"
    assert classificar_timeout(corpo) == "falha"
