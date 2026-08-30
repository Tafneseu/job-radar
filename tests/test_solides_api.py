"""Solides pela API: o mapeamento de campo, que e onde mora o risco da troca.

MEDIDO (2026-08-29): o scraper anterior abria navegador e raspava HTML, lendo
3 paginas de 10 = teto de 30 vagas por termo, em ~7 minutos de ciclo. A API
responde, pro mesmo termo "analista de dados":

    {"data": {"count": 205, "totalPages": 21}}

E pagina de verdade -- conferido comparando os ids:
    page 1 x page 2: 1 id em comum de 10   (ordenacao instavel, nao repeticao)
    page 2 x page 3: 0
    page 1 x page 3: 0
    29 ids distintos em 3 paginas          (seriam ~30 se paginasse perfeito)
    page 20 -> 10 vagas | page 21 -> 5 | page 22 -> 0   = 205, o count exato

O QUE ESTES TESTES GUARDAM: a conversao de um item da API num Job. Mapear
campo errado nao quebra nada -- so muda em silencio o que e aprovado.

O item de exemplo e a RESPOSTA REAL, copiada da sondagem.
"""

import logging
from datetime import date, timedelta

import pytest

from core.job import Job
from core.perfis import PERFIL_BR
from scrapers import solides
from scrapers.solides import (
    DIAS_PARA_PARAR,
    montar_job,
    montar_local,
    montar_modalidade,
    pagina_toda_antiga,
)

VAGA_API = {
    "id": 912529,
    "title": "Analista de Dados",
    "companyName": "SOMA SOLUTION",
    "city": {"id": 4378, "name": "Chapecó", "state_id": 22},
    "state": {"id": 22, "name": "Santa Catarina", "code": "SC"},
    "jobType": "presencial",
    "homeOffice": False,
    "createdAt": "2026-08-28",
    "redirectLink": "https://somasolution.solides.jobs/vacancies/912529?origem=portal",
    "seniority": [{"id": 4, "name": "Junior", "level": None}],
    "description": "<h2>Analista de Dados</h2>",
}


def test_converte_a_vaga_real_da_api():
    job = montar_job(VAGA_API)
    assert job.titulo == "Analista de Dados"
    assert job.empresa == "SOMA SOLUTION"
    assert job.local == "Chapecó - SC"
    assert job.modalidade == "Presencial"
    assert job.publicado_em == "2026-08-28"
    assert job.site == "Solides"


def test_usa_a_sigla_e_nao_o_nome_do_estado():
    """A Solides entrega state.code ("SC") pronto -- diferente da Gupy, que so
    da o nome por extenso. Sigla e o caminho mais curto na conferencia de UF.
    """
    assert montar_local(VAGA_API).endswith(" - SC")


def test_data_ja_vem_no_formato_certo():
    """createdAt vem como "2026-08-28", sem hora -- ao contrario da Gupy, que
    manda ISO completo e precisa de corte."""
    job = montar_job(VAGA_API)
    assert job.publicado_em_legivel == "28/08/2026"
    assert job.publicacao_antiga is False


@pytest.mark.parametrize("jobtype, home, esperado", [
    ("presencial", False, "Presencial"),
    ("hibrido", False, "Híbrido"),
    ("remoto", True, "Remoto"),
    ("PRESENCIAL", False, "Presencial"),
    ("", True, "Remoto"),        # so homeOffice preenchido
    ("", False, ""),             # nenhum dos dois: nao chuta
    ("qualquer coisa", False, ""),
])
def test_modalidade_traduzida(jobtype, home, esperado):
    assert montar_modalidade({"jobType": jobtype, "homeOffice": home}) == esperado


def test_local_com_campo_faltando():
    assert montar_local({"city": {"name": "Recife"}, "state": {}}) == "Recife"
    assert montar_local({"city": {}, "state": {"code": "CE"}}) == "CE"
    assert montar_local({}) == "Não informado"


@pytest.mark.parametrize("faltando", ["title", "redirectLink"])
def test_vaga_sem_o_essencial_e_descartada(faltando):
    assert montar_job({**VAGA_API, faltando: ""}) is None


# ------------- o mapeamento tem que respeitar as regras de negocio -------------

@pytest.mark.parametrize("cidade, sigla, jobtype, aprovada", [
    ("Fortaleza", "CE", "presencial", True),
    ("Recife", "PE", "presencial", True),
    ("Chapecó", "SC", "presencial", False),        # fora das 9 cidades
    ("São Paulo", "SP", "hibrido", False),
    ("Curitiba", "PR", "remoto", True),            # Brasil remoto de qualquer lugar
    ("Campina Grande", "PR", "presencial", False), # HOMONIMA: a do Parana
    ("Campina Grande", "PB", "presencial", True),  # a de verdade
    ("VITORIA", "ES", "presencial", False),        # a API as vezes manda em CAIXA ALTA
])
def test_o_local_montado_respeita_as_regras(cidade, sigla, jobtype, aprovada):
    job = montar_job({
        **VAGA_API,
        "title": "Analista de Dados",
        "city": {"name": cidade},
        "state": {"code": sigla},
        "jobType": jobtype,
        "redirectLink": f"https://x.solides.jobs/vacancies/{cidade}{sigla}{jobtype}",
    })
    assert job.combina_com(PERFIL_BR.regras) is aprovada


def test_o_job_montado_e_um_job_de_verdade():
    """Contrato com o resto do sistema: dedup, score e notificacao esperam Job."""
    job = montar_job(VAGA_API)
    assert isinstance(job, Job)
    assert job.id and job.chave_secundaria
    assert job.pontuar_relevancia(PERFIL_BR.regras) >= 0


# --------------------- onde parar de paginar (medido) ---------------------

HOJE = date(2026, 8, 30)


def _pagina(*idades_em_dias):
    """Uma pagina da API, com vagas das idades dadas."""
    return [{"createdAt": (HOJE - timedelta(days=d)).isoformat()} for d in idades_em_dias]


def test_pagina_com_vaga_nova_nao_para():
    """Basta UMA vaga dentro do limite pra valer a pena continuar."""
    assert pagina_toda_antiga(_pagina(90, 60, 3), DIAS_PARA_PARAR, HOJE) is False


def test_pagina_toda_velha_para():
    assert pagina_toda_antiga(_pagina(45, 60, 90), DIAS_PARA_PARAR, HOJE) is True


def test_no_limite_exato_ainda_nao_para():
    """Vaga com exatamente 30 dias ainda conta -- e o mesmo limiar que
    Job.publicacao_antiga usa pra marcar "pode ja estar preenchida"."""
    assert pagina_toda_antiga(_pagina(DIAS_PARA_PARAR), DIAS_PARA_PARAR, HOJE) is False
    assert pagina_toda_antiga(_pagina(DIAS_PARA_PARAR + 1), DIAS_PARA_PARAR, HOJE) is True


@pytest.mark.parametrize("itens", [
    [],                                    # pagina vazia
    [{"createdAt": ""}],                   # sem data
    [{"createdAt": "sei la"}],             # data invalida
    [{"outro_campo": 1}],                  # campo ausente
])
def test_sem_data_legivel_nao_para(itens):
    """Sem data nao ha o que concluir. Parar por engano custa VAGA; continuar
    por engano custa uma requisicao. Erra pro lado barato."""
    assert pagina_toda_antiga(itens, DIAS_PARA_PARAR, HOJE) is False


def test_data_com_hora_junto_e_lida():
    """A Solides manda so a data, mas nao custa aguentar ISO completo."""
    itens = [{"createdAt": "2026-01-01T10:00:00.000Z"}]
    assert pagina_toda_antiga(itens, DIAS_PARA_PARAR, HOJE) is True


def test_o_limite_bate_com_o_do_filtro():
    """MEDIDO: parar em 30 dias custa 4,8 requisicoes por termo, praticamente
    o mesmo que o teto fixo de 15 paginas (4,6) -- mas le fundo onde ha vaga
    nova e sai cedo onde o termo e parado.

    30 e tambem o limiar de Job.publicacao_antiga: alem disso, a vaga ja
    ganharia o aviso de "pode ja estar preenchida" e sairia do alerta
    imediato. Ler mais fundo seria buscar o que o filtro desprioriza.
    """
    from core.job import DIAS_PARA_PUBLICACAO_ANTIGA
    assert DIAS_PARA_PARAR == DIAS_PARA_PUBLICACAO_ANTIGA


# ------------------- o aviso da trava (regressao de bug) -------------------


class _RespostaFalsa:
    status_code = 200

    def __init__(self, corpo):
        self._corpo = corpo

    def json(self):
        return self._corpo


def _api_falsa(monkeypatch, idade_das_vagas, total_paginas):
    """Finge a API da Solides: sempre responde uma pagina cheia de vagas com a
    idade pedida, dizendo que existem `total_paginas` no total."""
    def get(url, params=None, timeout=None, headers=None):
        itens = [
            dict(VAGA_API, createdAt=(HOJE - timedelta(days=idade_das_vagas)).isoformat())
            for _ in range(10)
        ]
        return _RespostaFalsa({"data": {
            "data": itens,
            "count": total_paginas * 10,
            "totalPages": total_paginas,
        }})

    monkeypatch.setattr(solides.requests, "get", get)
    monkeypatch.setattr(solides.time, "sleep", lambda _: None)


def _avisou_da_trava(caplog):
    return any("bateu a trava" in r.message for r in caplog.records)


def test_parar_por_idade_nao_dispara_o_aviso_da_trava(monkeypatch, caplog):
    """REGRESSAO. O aviso antigo perguntava "esse termo TEM mais paginas que a
    trava?" em vez de "a trava foi atingida?". No ciclo de 30/08 'power bi'
    parou certinho na pagina 18 de 53 POR IDADE -- que e o comportamento
    correto -- e ainda assim saiu o aviso de que a trava tinha estourado.

    Aqui: 53 paginas (bem mais que a trava), mas as vagas tem 90 dias, entao
    a primeira pagina ja para por idade. Nao pode avisar nada."""
    _api_falsa(monkeypatch, idade_das_vagas=90, total_paginas=53)
    with caplog.at_level(logging.WARNING, logger=solides.logger.name):
        solides.SolidesScraper(["power bi"]).buscar_vagas()
    assert not _avisou_da_trava(caplog)


def test_estourar_a_trava_de_verdade_dispara_o_aviso(monkeypatch, caplog):
    """O outro lado: vaga nova ate o fim e mais paginas do que a trava aguenta.
    Ai a trava e realmente o motivo da parada, e o aviso e informacao."""
    _api_falsa(monkeypatch, idade_das_vagas=1, total_paginas=53)
    with caplog.at_level(logging.WARNING, logger=solides.logger.name):
        solides.SolidesScraper(["power bi"]).buscar_vagas()
    assert _avisou_da_trava(caplog)


def test_acabar_as_paginas_do_termo_nao_e_estourar_a_trava(monkeypatch, caplog):
    """Termo pequeno: 2 paginas so, todas com vaga nova. Leu tudo que existia.
    Nao ha nada a avisar."""
    _api_falsa(monkeypatch, idade_das_vagas=1, total_paginas=2)
    with caplog.at_level(logging.WARNING, logger=solides.logger.name):
        solides.SolidesScraper(["power bi"]).buscar_vagas()
    assert not _avisou_da_trava(caplog)
