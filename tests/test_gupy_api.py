"""Gupy pela API: o mapeamento de campo, que e onde mora o risco da troca.

MEDIDO (2026-08-29): o scraper anterior abria um navegador e raspava HTML,
lendo no maximo 3 paginas de 12 = teto de 36 vagas por termo. A API responde,
pro mesmo termo "analista de dados":

    {"pagination": {"total": 252, "limit": 10, "offset": 0}}

252 contra 36 -- era essa a explicacao das 59 vagas que a Gupy trouxe em tres
semanas. Volume ela sempre teve; o scraper e que so via o comeco.

O QUE ESTES TESTES GUARDAM: a conversao de um item da API num Job. Trocar a
camada de busca so e seguro se o Job sair igual, e mapear campo errado e o
tipo de erro que passa despercebido -- nao quebra nada, so muda silenciosamente
o que e aprovado.

O item de exemplo e a RESPOSTA REAL da API, copiada da sondagem.
"""

import pytest

from core.job import Job
from core.perfis import PERFIL_BR
from scrapers.gupy import montar_job, montar_local, montar_modalidade

# Resposta real da API, campo por campo, como veio na sondagem de 29/08.
VAGA_API = {
    "applicationDeadline": "2026-09-11",
    "careerPageId": 164897,
    "careerPageName": "Gradus",
    "city": "São Paulo",
    "companyId": 43069,
    "country": "Brasil",
    "description": "A Gradus é uma consultoria de gestão brasileira...",
    "disabilities": True,
    "id": 12341552,
    "isRemoteWork": False,
    "jobUrl": "https://gradus.gupy.io/job/eyJqb2JJZCI6MTIzNDE1NTIs",
    "name": "Analista de Dados    ",
    "publishedDate": "2026-08-28T21:28:28.868Z",
    "skills": [],
    "state": "São Paulo",
    "type": "vacancy_type_effective",
    "workplaceType": "on-site",
}


def test_converte_a_vaga_real_da_api():
    job = montar_job(VAGA_API)
    assert job.titulo == "Analista de Dados"        # a API devolve com espaco no fim
    assert job.empresa == "Gradus"
    assert job.local == "São Paulo, São Paulo"
    assert job.modalidade == "Presencial"
    assert job.publicado_em == "2026-08-28"          # sem a parte de hora
    assert job.site == "Gupy"


def test_titulo_vem_com_espaco_sobrando_na_api():
    """MEDIDO: "Analista de Dados    " com quatro espacos no fim. Sem strip,
    o espaco entra na chave de deduplicacao empresa|titulo e a mesma vaga
    passa duas vezes."""
    assert montar_job({**VAGA_API, "name": "  Analista de BI  "}).titulo == "Analista de BI"


def test_data_iso_com_hora_vira_data_pura():
    """Job.publicacao_antiga so reconhece AAAA-MM-DD (ver core/job.py). Com a
    hora junto, o padrao nao casa e a vaga volta a nao ter data -- exatamente
    o bug que custou a correcao da tag <time> do LinkedIn."""
    job = montar_job(VAGA_API)
    assert len(job.publicado_em) == 10
    assert job.publicado_em_legivel == "28/08/2026"


@pytest.mark.parametrize("bruto, remoto, esperado", [
    ("on-site", False, "Presencial"),
    ("onsite", False, "Presencial"),
    ("hybrid", False, "Híbrido"),
    ("remote", True, "Remoto"),
    ("", True, "Remoto"),       # so isRemoteWork preenchido
    ("", False, ""),            # nenhum dos dois: deixa vazio, nao chuta
    ("REMOTE", False, "Remoto"),
])
def test_modalidade_traduzida(bruto, remoto, esperado):
    assert montar_modalidade({"workplaceType": bruto, "isRemoteWork": remoto}) == esperado


def test_local_com_campo_faltando():
    assert montar_local({"city": "Recife", "state": ""}) == "Recife"
    assert montar_local({"city": "", "state": "Ceará"}) == "Ceará"
    assert montar_local({}) == "Não informado"


@pytest.mark.parametrize("faltando", ["name", "jobUrl"])
def test_vaga_sem_o_essencial_e_descartada(faltando):
    """Sem titulo ou sem link nao da pra deduplicar nem notificar."""
    assert montar_job({**VAGA_API, faltando: ""}) is None


# ------------- o mapeamento tem que respeitar as regras de negocio -------------

@pytest.mark.parametrize("cidade, estado, workplace, aprovada", [
    ("Fortaleza", "Ceará", "on-site", True),           # cidade aceita, presencial
    ("São Paulo", "São Paulo", "on-site", False),      # fora das 9 cidades
    ("São Paulo", "São Paulo", "hybrid", False),
    ("Curitiba", "Paraná", "remote", True),            # Brasil remoto de qualquer lugar
    ("Campina Grande", "Paraná", "on-site", False),    # HOMONIMA: a do PR, nao a da PB
    ("Campina Grande", "Paraíba", "on-site", True),    # a de verdade
])
def test_o_local_montado_respeita_as_regras(cidade, estado, workplace, aprovada):
    """A API devolve o estado por EXTENSO ("Paraná"), nao a sigla.

    Isso so funciona porque a guarda de UF passou a ler estado por extenso em
    5e91895. Antes daquele commit, "Campina Grande, Paraná" seria aprovada
    como se fosse a Campina Grande da Paraiba -- e a troca pra API teria
    introduzido esse falso positivo sem ninguem notar.
    """
    job = montar_job({
        **VAGA_API,
        "name": "Analista de Dados",
        "city": cidade,
        "state": estado,
        "workplaceType": workplace,
        "jobUrl": f"https://x.gupy.io/job/{cidade}{estado}{workplace}",
    })
    assert job.combina_com(PERFIL_BR.regras) is aprovada


def test_o_job_montado_e_um_job_de_verdade():
    """Contrato com o resto do sistema: dedup, score e notificacao esperam Job."""
    job = montar_job(VAGA_API)
    assert isinstance(job, Job)
    assert job.id and job.chave_secundaria
    assert job.pontuar_relevancia(PERFIL_BR.regras) >= 0
