"""Avaliacao de fonte nova: as partes que dao pra testar sem rede.

POR QUE ESTA FERRAMENTA EXISTE: adicionar fonte custa tempo de ciclo em toda
execucao, pra sempre. A Senior entrou com endpoint funcionando e rendeu 0,3%
(398 vagas brutas, 1 aprovada) -- achar o endpoint e a parte facil, medir e a
que decide. avaliar_fonte.py antecipa essa medicao.

O que da pra testar aqui: leitura do robots.txt, extracao do JSON-LD, feeds e
a pontuacao de endpoint. O que NAO da: a captura de requisicoes com navegador,
que precisa de rede -- mesmo limite dos scrapers.
"""

import pytest

from avaliar_fonte import (
    caminho_permitido,
    descrever_erro,
    extrair_jobpostings,
    links_de_feed,
    pontuar_endpoint,
    regras_do_robots,
    resumir_jobposting,
)

# --------------------------------------------------------------- robots.txt

ROBOTS = """
# comentario que deve ser ignorado
User-agent: Googlebot
Disallow: /interno

User-agent: *
Disallow: /
Allow: /vagas
Allow: /carreiras
Sitemap: https://exemplo.com/sitemap.xml
"""


def test_le_o_grupo_do_agente_generico():
    """Robo sem identidade propria (o nosso caso) segue o grupo "*"."""
    regras = regras_do_robots(ROBOTS)
    assert ("disallow", "/") in regras
    assert ("allow", "/vagas") in regras


def test_nao_mistura_o_grupo_de_outro_agente():
    """A regra do Googlebot nao vale pra gente, e vice-versa."""
    assert ("disallow", "/interno") not in regras_do_robots(ROBOTS)
    assert ("disallow", "/interno") in regras_do_robots(ROBOTS, "Googlebot")


def test_sitemap_e_comentario_nao_viram_regra():
    for permissao, _ in regras_do_robots(ROBOTS):
        assert permissao in ("allow", "disallow")


@pytest.mark.parametrize("caminho, permitido", [
    ("/vagas/analista-de-dados", True),   # Allow mais especifico vence
    ("/carreiras/bi", True),
    ("/admin", False),                     # cai no Disallow: /
    ("/", False),
])
def test_prefixo_mais_longo_vence(caminho, permitido):
    """E assim que robots.txt e definido: entre "Disallow: /" e "Allow:
    /vagas", a segunda vence para /vagas/x."""
    assert caminho_permitido(regras_do_robots(ROBOTS), caminho) is permitido


def test_sem_regra_nenhuma_e_permitido():
    """robots.txt e lista de proibicoes, nao de autorizacoes."""
    assert caminho_permitido([], "/qualquer/coisa") is True


def test_empate_de_tamanho_o_allow_vence():
    regras = [("disallow", "/vagas"), ("allow", "/vagas")]
    assert caminho_permitido(regras, "/vagas/x") is True


# ------------------------------------------------------------------ JSON-LD

JSONLD_SIMPLES = """<script type="application/ld+json">
{"@context":"https://schema.org","@type":"JobPosting","title":"Analista de Dados",
 "datePosted":"2026-08-29","jobLocationType":"TELECOMMUTE",
 "hiringOrganization":{"@type":"Organization","name":"Empresa X"},
 "jobLocation":{"address":{"addressLocality":"Fortaleza","addressRegion":"CE"}}}
</script>"""

# Formato usado por varios ATSs: tudo dentro de @graph.
JSONLD_GRAFO = """<script type='application/ld+json'>
{"@graph":[{"@type":"Organization","name":"Nao e vaga"},
           {"@type":"JobPosting","title":"Analista de BI"}]}
</script>"""

# Lista no topo, e o @type vindo como lista tambem.
JSONLD_LISTA = """<script type="application/ld+json">
[{"@type":["JobPosting","Thing"],"title":"BI Analyst"}]
</script>"""


def test_extrai_jobposting_simples():
    assert len(extrair_jobpostings(JSONLD_SIMPLES)) == 1


def test_extrai_de_dentro_do_grafo():
    """@graph e o formato mais comum em ATS — ignorar ele perderia a vaga."""
    vagas = extrair_jobpostings(JSONLD_GRAFO)
    assert [v["title"] for v in vagas] == ["Analista de BI"]


def test_extrai_de_lista_e_de_tipo_composto():
    assert [v["title"] for v in extrair_jobpostings(JSONLD_LISTA)] == ["BI Analyst"]


def test_ignora_json_ld_que_nao_e_vaga():
    html = '<script type="application/ld+json">{"@type":"WebSite","name":"x"}</script>'
    assert extrair_jobpostings(html) == []


def test_json_quebrado_nao_derruba_a_analise():
    """Pagina real tem JSON-LD malformado. Isso nao pode estourar excecao no
    meio da avaliacao — o resto da pagina ainda interessa."""
    html = '<script type="application/ld+json">{isso nao e json}</script>' + JSONLD_SIMPLES
    assert len(extrair_jobpostings(html)) == 1


def test_pagina_sem_json_ld():
    assert extrair_jobpostings("<html><body>nada aqui</body></html>") == []


def test_resumo_traz_os_campos_que_o_jobradar_usa():
    vaga = extrair_jobpostings(JSONLD_SIMPLES)[0]
    assert resumir_jobposting(vaga) == {
        "titulo": "Analista de Dados",
        "empresa": "Empresa X",
        "cidade": "Fortaleza",
        "uf": "CE",
        "publicado_em": "2026-08-29",
        "remoto": True,
    }


def test_resumo_aguenta_campo_faltando():
    """JSON-LD real vem incompleto o tempo todo."""
    resumo = resumir_jobposting({"title": "Analista"})
    assert resumo["titulo"] == "Analista"
    assert resumo["empresa"] == ""
    assert resumo["remoto"] is False


# --------------------------------------------------------------------- feed

def test_acha_feed_rss_e_resolve_url_relativa():
    html = '<link rel="alternate" type="application/rss+xml" href="/vagas.rss">'
    assert links_de_feed(html, "https://exemplo.com") == ["https://exemplo.com/vagas.rss"]


def test_ignora_link_que_nao_e_feed():
    html = '<link rel="stylesheet" type="text/css" href="/estilo.css">'
    assert links_de_feed(html, "https://exemplo.com") == []


# ----------------------------------------------------------------- endpoint

@pytest.mark.parametrize("url, tipo, minimo", [
    ("https://x.com/api/jobs/search", "application/json", 3),
    ("https://x.com/api/vagas", "application/json", 3),
    ("https://x.com/api/tracking", "application/json", 1),
])
def test_json_pontua_e_nome_de_vaga_pontua_mais(url, tipo, minimo):
    assert pontuar_endpoint(url, tipo) >= minimo


# ---- o defeito medido no vagas.com.br, na primeira vez que a ferramenta rodou

# URLs REAIS que apareceram no topo do relatorio, acima do endpoint de verdade.
RASTREADORES = [
    "https://px.ads.linkedin.com/attribution_trigger?pid=3994369"
    "&url=https://www.vagas.com.br/vagas-de-analista-de-dados",
    "https://gum.criteo.com/sid/json?origin=publishertagids&domain=vagas.com.br",
    "https://fastlane.rubiconproject.com/a/api/fastlane.json?account_id=14940",
]


@pytest.mark.parametrize("url", RASTREADORES)
def test_rastreador_de_anuncio_nao_e_candidato(url):
    """MEDIDO: estes tres ficaram em 1o, 2o e 3o lugar do relatorio.

    Pontuavam alto porque a busca das pistas varria a URL INTEIRA, e o
    rastreador embute o endereco da pagina dentro do proprio parametro
    ("&url=https://vagas.com.br/vagas-de-..."). Quanto mais o anuncio
    rastreava, mais ele parecia ser a busca de vagas.
    """
    assert pontuar_endpoint(url, "application/json") == 0


def test_pista_na_query_string_nao_conta():
    """A raiz do defeito: so HOST e CAMINHO valem como pista."""
    com_pista_no_caminho = pontuar_endpoint("https://api.site.com/vagas", "application/json")
    so_na_query = pontuar_endpoint("https://api.site.com/x?url=/vagas-de-analista",
                                   "application/json")
    assert com_pista_no_caminho > so_na_query


def test_endpoint_da_mesma_casa_ganha_peso():
    """O sinal mais forte de todos: a API que o proprio site chama.

    Caso real da Gupy — a pagina e portal.gupy.io e a API e
    employability-portal.gupy.io. Subdominio diferente, mesma casa.
    """
    pagina = "https://portal.gupy.io/job-search/term=analista"
    daqui = pontuar_endpoint("https://employability-portal.gupy.io/api/v1/jobs",
                             "application/json", pagina)
    de_fora = pontuar_endpoint("https://outro-site.com/api/v1/jobs",
                               "application/json", pagina)
    assert daqui > de_fora


def test_o_endpoint_real_da_gupy_fica_acima_dos_rastreadores():
    """O teste que resume o defeito: ordenar como o relatorio ordena."""
    pagina = "https://www.vagas.com.br/vagas-de-analista-de-dados"
    real = pontuar_endpoint("https://www.vagas.com.br/api/vagas/busca",
                            "application/json", pagina)
    for tracker in RASTREADORES:
        assert real > pontuar_endpoint(tracker, "application/json", pagina)


def test_o_que_nao_e_json_nao_e_candidato():
    assert pontuar_endpoint("https://x.com/jobs/style.css", "text/css") == 0
    assert pontuar_endpoint("https://x.com/jobs.png", "image/png") == 0


def test_json_sem_pista_no_nome_continua_candidato():
    """O nome do endpoint nem sempre denuncia o que ele faz — pontuar baixo
    ordena o relatorio, nao esconde o candidato."""
    assert pontuar_endpoint("https://x.com/api/v2/q", "application/json") > 0


# ------------------------------ mensagem de erro ------------------------------

class _ErroFalso(Exception):
    """Reproduz o texto real que requests/playwright devolvem."""


@pytest.mark.parametrize("mensagem, esperado", [
    # O caso que motivou: endereco ficticio copiado da documentacao.
    ("HTTPSConnectionPool(host='x.com.br', port=443): Max retries exceeded "
     "(Caused by NameResolutionError(\"[Errno 11001] getaddrinfo failed\"))",
     "site nao encontrado"),
    ("HTTPSConnectionPool: Read timed out. (read timeout=20)", "nao respondeu a tempo"),
    ("ProxyError('Unable to connect to proxy', OSError('Tunnel connection failed: 403'))",
     "bloqueou o acesso"),
    ("SSLError: certificate verify failed", "certificado"),
])
def test_erro_de_rede_vira_frase_acionavel(mensagem, esperado):
    """MEDIDO: a primeira pessoa a usar o script recebeu quatro linhas de
    jargao com stack de proxy dentro pra dizer "esse site nao existe", e foi
    procurar defeito no script.

    Quinto caso desta base do mesmo tipo: mensagem que nao distingue as
    causas faz investigar a coisa errada.
    """
    assert esperado in descrever_erro(_ErroFalso(mensagem))


def test_erro_desconhecido_mostra_o_original():
    """Sem traducao conhecida, e melhor o texto cru do que uma frase generica
    que esconde a causa."""
    saida = descrever_erro(_ErroFalso("algo totalmente novo"))
    assert "_ErroFalso" in saida and "algo totalmente novo" in saida
