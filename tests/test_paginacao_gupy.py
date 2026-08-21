"""Paginação da Gupy: o que um timeout esperando cards realmente significa.

MEDIDO (2026-08-21): o aviso "Vaga pode ter ficado de fora" disparava na
maioria dos termos de cada ciclo — e era ALARME FALSO.

A página de busca da Gupy cabe 12 cards. "data analyst" tem 8 resultados no
total, então a página 1 já era a última. Mas cards_por_pagina é aprendido
OLHANDO A PÁGINA 1: o scraper aprendeu "página cheia = 8 cards", concluiu que
a página 1 estava cheia e pediu a página 2, que não existe.

Medido ao vivo, mesmo dia, mesmo endpoint:
    'analista de dados'  pág.1 -> 12 cards   pág.2 -> 12 cards
    'data analyst'       pág.1 ->  8 cards   pág.2 ->  0 cards

A página inexistente carrega normalmente e diz, em texto, "Nenhum resultado
foi encontrado". O scraper já procurava essa frase — só que no ramo da página
1. Da 2 em diante ele nem olhava o corpo.

Por que isso importa: alarme falso constante esconde o aviso verdadeiro, que é
a única razão de o aviso existir.
"""

from scrapers.gupy import TEXTO_SEM_RESULTADO, classificar_timeout

# Corpo real capturado da página 2 de "data analyst" (que não existe).
CORPO_PAGINA_INEXISTENTE = (
    "Pular para o conteúdo principal | Digite um cargo | Filtrar | 1 | Ordenar | "
    "Local de trabalho | Modelo de trabalho | Empresa | Exibir vagas: | "
    "Empresas com Selo Feedback | Afirmativas para PCD | "
    "Aplique para Vagas de data analyst | 8 resultados | "
    "Nenhum resultado foi encontrado | Dicas para melhorar a sua busca:"
)

# Página que simplesmente não carregou: sem a frase, sem cards.
CORPO_DE_FALHA = (
    "Pular para o conteúdo principal | Digite um cargo | Filtrar | Ordenar"
)


def test_pagina_alem_da_ultima_e_fim_nao_falha():
    """O caso que gerava o alarme falso, com o corpo real medido."""
    assert classificar_timeout(CORPO_PAGINA_INEXISTENTE, 2) == "fim"


def test_busca_sem_resultado_nenhum_na_pagina_1():
    """Mesma frase, página 1: aí é busca vazia de verdade, não fim de lista."""
    assert classificar_timeout(CORPO_PAGINA_INEXISTENTE, 1) == "vazio"


def test_pagina_que_nao_carregou_continua_sendo_falha():
    """O aviso verdadeiro tem que sobreviver — é pra isso que ele existe."""
    assert classificar_timeout(CORPO_DE_FALHA, 2) == "falha"
    assert classificar_timeout(CORPO_DE_FALHA, 3) == "falha"


def test_corpo_vazio_e_falha():
    """inner_text pode falhar e devolver "" — não pode virar "fim" silencioso."""
    assert classificar_timeout("", 2) == "falha"
    assert classificar_timeout("", 1) == "falha"


def test_a_frase_procurada_e_a_que_o_site_mostra():
    """Trava a string: se a Gupy mudar o texto, é aqui que se descobre."""
    assert TEXTO_SEM_RESULTADO in CORPO_PAGINA_INEXISTENTE
