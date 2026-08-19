"""Testes do scraper da Senior, contra o JSON REAL capturado da API.

O payload de `vaga_real()` foi copiado da resposta de verdade do endpoint
(termo "analista de dados", 18/08) — não é um exemplo inventado. É o que
permite testar a conversão inteira sem tocar na rede.
"""

from datetime import date

import pytest

from scrapers.senior import (
    URL_VAGA,
    _expirada,
    _local,
    _modalidade,
    montar_job,
)


def vaga_real():
    """Resposta real da API, item de `contents`."""
    return {
        "vacancy": {
            "id": "8a0d3627-4900-4b37-8f04-b448821ea09f",
            "title": "Analista de Dados",
            "jobModel": ["IN_PERSON"],
            "localization": {
                "id": "41eee193-0280-4719-a6a9-02f498df8b28",
                "city": "Pilar do Sul",
                "province": "SP",
                "country": "Brasil",
            },
            "publication": {
                "id": "f3e8c6a2-b129-428b-9cc3-64b2f55527ad",
                "startDate": "2026-08-17",
                "endDate": "2026-09-01",
            },
        },
        "company": {
            "id": "8a032fae-d152-47f0-b2fe-532a7059057c",
            "name": "OURO SAFRA",
            "sector": "hcm.careerscorporate.sector_agricultura",
            "address": {"city": "PILAR DO SUL", "province": "SP", "country": "Brasil"},
            "publicationDate": "2025-09-11",
            "tenant": "ourosafracombr",
        },
    }


HOJE = date(2026, 8, 18)


def test_converte_a_vaga_real_inteira():
    job = montar_job(vaga_real(), hoje=HOJE)
    assert job.titulo == "Analista de Dados"
    assert job.empresa == "OURO SAFRA"
    assert job.local == "Pilar do Sul - SP"
    assert job.modalidade == "Presencial"
    assert job.publicado_em == "2026-08-17"
    assert job.site == "Senior"
    assert job.link == URL_VAGA + "8a0d3627-4900-4b37-8f04-b448821ea09f"


def test_data_de_publicacao_e_preenchida():
    """Nenhuma outra fonte do projeto entrega isso — publicado_em está vazio
    em 1.279 de 1.280 registros do banco."""
    assert montar_job(vaga_real(), hoje=HOJE).publicado_em == "2026-08-17"


# ------------------------------------------------------------- MODALIDADE

@pytest.mark.parametrize("job_model, esperado", [
    (["IN_PERSON"], "Presencial"),
    (["REMOTE"], "Remoto"),
    (["HYBRID"], "Híbrido"),
    # Lista com mais de um valor: a vaga aceita mais de um arranjo, e a
    # leitura que mais abre portas vence.
    (["IN_PERSON", "REMOTE"], "Remoto"),
    (["HYBRID", "IN_PERSON"], "Híbrido"),
    (["remote"], "Remoto"),          # caixa não pode importar
    # Desconhecido/vazio fica em branco de propósito: campo vazio ainda deixa
    # _confirma_remoto tentar pelo texto de `local`; campo errado não deixa.
    ([], ""),
    (None, ""),
    (["ALGO_NOVO"], ""),
])
def test_modalidade(job_model, esperado):
    assert _modalidade(job_model) == esperado


# ------------------------------------------------------------ LOCALIZACAO

@pytest.mark.parametrize("localizacao, esperado", [
    ({"city": "Natal", "province": "RN", "country": "Brasil"}, "Natal - RN"),
    ({"city": "Campina Grande", "province": "PB", "country": "Brasil"}, "Campina Grande - PB"),
    ({"city": "Manaus", "province": "AM", "country": "Brasil"}, "Manaus - AM"),
    # País só aparece quando não é Brasil — senão vira ruído em toda vaga.
    ({"city": "Lisboa", "province": "", "country": "Portugal"}, "Lisboa, Portugal"),
    ({"city": "", "province": "", "country": ""}, "Não informado"),
    (None, "Não informado"),
])
def test_local(localizacao, esperado):
    assert _local(localizacao) == esperado


def test_local_sai_no_formato_que_o_filtro_de_cidade_entende():
    """O filtro procura o nome da cidade dentro do texto de `local`, e o
    escopo remoto lê a UF depois do hífen (ver extrair_escopo_remoto)."""
    from core.job import extrair_escopo_remoto
    from core.perfis import PERFIL_BR
    from core.job import Job

    job = Job(titulo="Analista de Dados", empresa="X",
              local=_local({"city": "Natal", "province": "RN", "country": "Brasil"}),
              link="https://x/1", site="Senior", modalidade="Presencial")
    assert job.combina_com(PERFIL_BR.regras)
    assert extrair_escopo_remoto("Natal - RN", "Remoto") == {"Brasil"}


# --------------------------------------------------------------- EXPIRADA

@pytest.mark.parametrize("fim, esperado", [
    ("2026-08-19", False),   # termina amanhã: vale
    ("2026-08-18", False),   # termina hoje: vale
    ("2026-08-17", True),    # terminou ontem: não vale
    ("2025-01-01", True),
    (None, False),           # sem data: na dúvida, mostra
    ("data-invalida", False),
])
def test_expirada(fim, esperado):
    assert _expirada({"endDate": fim}, hoje=HOJE) is esperado


def test_vaga_expirada_nao_vira_job():
    item = vaga_real()
    item["vacancy"]["publication"]["endDate"] = "2026-08-01"
    assert montar_job(item, hoje=HOJE) is None


# ------------------------------------------------------- DADO INCOMPLETO

@pytest.mark.parametrize("mexer", [
    lambda i: i["vacancy"].pop("id"),
    lambda i: i["vacancy"].pop("title"),
    lambda i: i["vacancy"].update({"title": "   "}),
])
def test_vaga_sem_id_ou_titulo_e_descartada(mexer):
    item = vaga_real()
    mexer(item)
    assert montar_job(item, hoje=HOJE) is None


@pytest.mark.parametrize("item", [{}, None, {"vacancy": {}}, {"company": {}}])
def test_item_degenerado_nao_quebra(item):
    assert montar_job(item, hoje=HOJE) is None


def test_vaga_sem_empresa_ainda_vira_job():
    """Empresa ausente não invalida a vaga — só o rótulo fica genérico."""
    item = vaga_real()
    item.pop("company")
    job = montar_job(item, hoje=HOJE)
    assert job is not None
    assert job.empresa == "Não informada"
