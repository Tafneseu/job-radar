"""Trava a grafia dos `location=` do LinkedIn.

POR QUE ESTE ARQUIVO EXISTE (2026-08-20): o endpoint de visitante do LinkedIn
falha em SILÊNCIO quando não entende um location. Ele não devolve erro nem
lista vazia — devolve um resultado genérico dos EUA. Do lado do scraper isso é
indistinguível de uma busca bem-sucedida: vieram 10 cards, tudo "normal".

Foi assim que "México" e "Colômbia" (grafia em português) ficaram na config
trazendo ZERO vaga desses países, e "Brasil" ficou trazendo vaga americana no
lugar de brasileira — por quanto tempo, ninguém sabe. O filtro descartava o
lixo depois, então nada quebrava de forma visível; só faltava vaga.

O QUE ESTES TESTES CONSEGUEM FAZER: garantir que ninguém volte a trocar uma
grafia medida por uma traduzida. É uma trava de regressão, não uma validação.

O QUE ELES NÃO CONSEGUEM: dizer se o LinkedIn ainda resolve essas grafias hoje.
Isso só se descobre indo na rede. Se um país voltar a zerar no banco por
semanas, desconfie DAQUI primeiro.
"""

from core.config import (
    LOCATIONS_LINKEDIN,
    LOCATIONS_LINKEDIN_CIDADES_PRESENCIAL,
    LOCATIONS_LINKEDIN_REMOTO_APENAS,
)
from core.config_intl import LOCATIONS_INTL

# Grafias verificadas ao vivo no endpoint (contagem = vagas realmente naquele
# país, de 10 retornadas). Só entra aqui o que foi medido de fato.
GRAFIAS_MEDIDAS = {
    "Brazil": 8,
    "Mexico": 10,
    "Colombia": 10,
    "Espanha": 9,
}

# Grafias que o endpoint NÃO resolve — medidas, e cada uma já esteve na config.
GRAFIAS_QUEBRADAS = {"Brasil", "México", "Colômbia"}


def test_mercado_casa_usa_grafia_que_o_linkedin_resolve():
    """location="Brasil" devolve vaga dos EUA; "Brazil" devolve vaga do Brasil.

    Medido: "Brasil" -> 0 de 10 no Brasil / "Brazil" -> 8 de 10 no Brasil.
    """
    assert LOCATIONS_LINKEDIN == ["Brazil"]


def test_nenhuma_grafia_quebrada_em_uso():
    """Nenhuma das três grafias que sabidamente falham pode voltar à config."""
    em_uso = set(LOCATIONS_LINKEDIN) | set(LOCATIONS_LINKEDIN_REMOTO_APENAS)
    assert not (em_uso & GRAFIAS_QUEBRADAS)


def test_mercados_adicionais_so_usam_grafia_conhecida():
    """Todo país da lista ou foi medido aqui, ou já roda no perfil internacional.

    LOCATIONS_INTL serve de referência porque é a lista que trouxe 110 vagas do
    México e 48 da Colômbia — ou seja, comprovadamente resolve.
    """
    conhecidas = set(GRAFIAS_MEDIDAS) | set(LOCATIONS_INTL)
    for pais in LOCATIONS_LINKEDIN_REMOTO_APENAS:
        assert pais in conhecidas, f"{pais!r} nunca foi medido no endpoint"


def test_espanha_e_a_unica_divergencia_da_lista_internacional():
    """A config PROMETE reaproveitar LOCATIONS_INTL — este teste cobra isso.

    Foi justamente a divergência silenciosa entre as duas listas que deixou
    passar "México"/"Colômbia". "Espanha" é a única exceção tolerada, porque
    foi medida e resolve (9 de 10 na Espanha).
    """
    divergentes = set(LOCATIONS_LINKEDIN_REMOTO_APENAS) - set(LOCATIONS_INTL)
    assert divergentes == {"Espanha"}


def test_busca_por_cidade_nao_tem_pais_nem_remoto():
    """A lista de cidades é só cidade — país ali viraria busca nacional duplicada.

    "Remoto" também não é lugar de verdade pro endpoint: já é coberto pela
    passada remoto=True do mercado casa.
    """
    assert "Remoto" not in LOCATIONS_LINKEDIN_CIDADES_PRESENCIAL
    paises = set(LOCATIONS_LINKEDIN) | set(LOCATIONS_LINKEDIN_REMOTO_APENAS)
    assert not (set(LOCATIONS_LINKEDIN_CIDADES_PRESENCIAL) & paises)
