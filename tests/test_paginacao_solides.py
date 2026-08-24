"""Paginação da Sólides: o que um timeout esperando cards significa.

MEDIDO (2026-08-21) ao vivo, com o MESMO user_agent e init_script do scraper
— sem eles a Sólides não renderiza nada e a medição não vale (a primeira
tentativa media exatamente isso, e foi descartada):

    'analista de dados' pág.1 -> 10 cards | total "200 vaga(s) encontrada"
    'data analyst'      pág.1 ->  7 cards | total   "7 vaga(s) encontrada"
    'data analyst'      pág.2 ->  0 cards | "Ops! / Vaga não encontrada"

Mesma causa da Gupy: a página cabe 10, "data analyst" tem 7 no total, então a
página 1 já era a última. Mas cards_por_pagina é aprendido OLHANDO A PÁGINA 1
— aprendeu "cheia = 7", achou que estava cheia e pediu a página 2, que não
existe. O timeout virava aviso de vaga perdida.

O arquivo afirmava, com medição, que aqui o texto do site não ajudava. Estava
certo sobre o que mediu: a página além da última NÃO mostra "0 vaga(s)
encontrada" — mostra o total real. Mas mostra "Ops! / Vaga não encontrada",
que aquela medição não procurou.

SEGUNDO BUG, achado na mesma medição: a checagem de busca vazia era substring
crua, e "200 vaga(s) encontrada" contém "0 vaga(s) encontrada".
"""

import pytest

from scrapers.solides import TEXTO_SEM_RESULTADO, classificar_timeout

# Corpo real da página 2 de "data analyst" (que não existe).
CORPO_PAGINA_INEXISTENTE = (
    "Todas as vagas | Vagas RH | Para Empresas | Blog | Cursos | Entrar | "
    "O portal de vagas de empregos mais amado do Brasil. | Buscar vagas | "
    "Vagas | 7 vaga(s) encontrada(s) | Ordenadas por: Data de postagem | "
    "Filtros | Ops! | Vaga não encontrada | Mas não desanime, tente uma nova busca"
)

# Corpo de uma busca genuinamente vazia, na página 1.
CORPO_BUSCA_VAZIA = (
    "Buscar vagas | Vagas | 0 vaga(s) encontrada(s) | Ops! | Vaga não encontrada"
)

# Página que não carregou: sem marcador nenhum.
CORPO_DE_FALHA = "Todas as vagas | Vagas RH | Para Empresas | Blog | Buscar vagas"


def test_pagina_alem_da_ultima_e_fim_nao_falha():
    """O caso que gerava o alarme falso, com o corpo real medido."""
    assert classificar_timeout(CORPO_PAGINA_INEXISTENTE, 2) == "fim"


def test_busca_vazia_na_pagina_1():
    assert classificar_timeout(CORPO_BUSCA_VAZIA, 1) == "vazio"


def test_pagina_que_nao_carregou_continua_sendo_falha():
    """O aviso verdadeiro tem que sobreviver — é pra isso que ele existe."""
    assert classificar_timeout(CORPO_DE_FALHA, 2) == "falha"
    assert classificar_timeout(CORPO_DE_FALHA, 3) == "falha"


def test_corpo_vazio_e_falha():
    """inner_text pode falhar e devolver "" — não pode virar "fim" silencioso."""
    assert classificar_timeout("", 2) == "falha"
    assert classificar_timeout("", 1) == "falha"


# ---------------- o segundo bug: substring de "0 vaga(s)" ----------------

@pytest.mark.parametrize("total", [
    "200 vaga(s) encontrada(s)",
    "10 vaga(s) encontrada(s)",
    "30 vaga(s) encontrada(s)",
    "1.200 vaga(s) encontrada(s)",
])
def test_total_terminado_em_zero_nao_e_busca_vazia(total):
    """MEDIDO: "200 vaga(s) encontrada" contém "0 vaga(s) encontrada".

    Com a substring crua, uma página 1 que falhasse de verdade num termo
    desses era registrada como "0 resultados reais" e o scraper parava em
    silêncio — falha real escondida atrás de mensagem de busca vazia.
    """
    corpo = f"Buscar vagas | Vagas | {total} | Ordenadas por: Data de postagem"
    assert classificar_timeout(corpo, 1) == "falha"


def test_zero_de_verdade_ainda_e_busca_vazia():
    """A outra metade: consertar o falso positivo não pode matar o caso real."""
    corpo = "Buscar vagas | Vagas | 0 vaga(s) encontrada(s) | Filtros"
    assert classificar_timeout(corpo, 1) == "vazio"


def test_a_frase_procurada_e_a_que_o_site_mostra():
    """Trava a string: se a Sólides mudar o texto, é aqui que se descobre."""
    assert TEXTO_SEM_RESULTADO in CORPO_PAGINA_INEXISTENTE
