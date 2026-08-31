"""Segunda passada: repetir, no FIM do ciclo, so as buscas que voltaram vazias.

POR QUE ISTO EXISTE (e por que a versao anterior foi removida):

  · repetir na hora, com pausa de 5s / 10s / 30s: MEDIDO em 30/08, 13 buscas
    vazias, 13 repeticoes, 0 recuperacoes, +9 min por ciclo. Removido.
  · esperar o rodizio trazer o termo de volta (~9h): MEDIDO em 30-31/08,
    recuperou 12 de 22 pares -- mas deixou 5 pares com vaga COMPROVADA (o
    teste isolado achou 9 a 10 cards em cada) vazios por dois ciclos seguidos.
  · repetir ~20 min depois, isolado: recuperou 10 de 22.

Dai a escolha do fim do ciclo: e o momento mais tarde alcancavel sem esperar
de graca, e a escala em que a resposta do LinkedIn muda e de dezenas de
minutos, nao de segundos.

O QUE ESTES TESTES GUARDAM: que ela so repete o que voltou vazio, que nao se
repete a si mesma (era o jeito facil de criar laco infinito), e que so devolve
vaga INEDITA -- sem isso o contador que decide se ela se paga mente.
"""
import logging

import pytest

from core.job import Job
from scrapers.linkedin import LinkedInScraper


def vaga(n: int) -> Job:
    return Job(
        titulo=f"Analista de Dados {n}",
        empresa="Empresa",
        local="Recife, Pernambuco, Brazil",
        link=f"https://linkedin.com/jobs/view/{n}",
        site="LinkedIn",
    )


class ScraperFalso(LinkedInScraper):
    """Troca so a ida na rede. O resto do fluxo e o de producao."""

    def __init__(self, respostas, **kwargs):
        super().__init__(termos_busca=["analista de dados"], **kwargs)
        self.respostas = respostas       # {(termo, location): [ [1a vez], [2a vez] ]}
        self.chamadas = []

    def _buscar_termo(self, termo, location, remoto=False, max_paginas=None, rotulo="nacional"):
        self.chamadas.append((termo, location, rotulo))
        # Sem esta trava, uma segunda passada que se re-agenda faz o teste
        # TRAVAR em vez de falhar -- e teste que trava ninguem investiga.
        assert len(self.chamadas) <= 50, (
            "laço infinito: a segunda passada está se re-agendando"
        )
        fila = self.respostas.get((termo, location), [[]])
        achadas = fila.pop(0) if fila else []
        if not achadas and getattr(self, "_registrar_vazias", False):
            self._vazias.append((termo, location, remoto, max_paginas, rotulo, 0.0))
        return achadas


def montar(respostas):
    """Um mercado nacional e duas cidades -- o suficiente pra ver o fluxo."""
    return ScraperFalso(
        respostas,
        locations=["Brazil"],
        locations_remoto_apenas=[],
        locations_cidades_presencial=["Caruaru", "Maceió"],
    )


def test_sem_busca_vazia_nao_ha_segunda_passada():
    """Se nada voltou vazio, nao pode custar nem uma requisicao a mais."""
    s = montar({
        ("analista de dados", "Brazil"): [[vaga(1)], [vaga(1)]],
        ("analista de dados", "Caruaru"): [[vaga(2)]],
        ("analista de dados", "Maceió"): [[vaga(3)]],
    })
    s.buscar_vagas()
    assert len(s.chamadas) == 4          # nacional + nacional remoto + 2 cidades
    assert s._vazias == []


def test_repete_so_o_que_voltou_vazio():
    s = montar({
        ("analista de dados", "Brazil"): [[vaga(1)], [vaga(1)]],
        ("analista de dados", "Caruaru"): [[], [vaga(9)]],   # vazia, depois volta
        ("analista de dados", "Maceió"): [[vaga(3)]],
    })
    vagas = s.buscar_vagas()

    repetidas = s.chamadas[4:]
    assert repetidas == [("analista de dados", "Caruaru", "cidade")]
    assert vaga(9).id in {v.id for v in vagas}


def test_a_segunda_passada_nao_repete_a_si_mesma():
    """Laco infinito era o risco obvio: a repeticao volta vazia, se registra de
    novo, repete de novo. O _registrar_vazias desligado e o que impede."""
    s = montar({
        ("analista de dados", "Caruaru"): [[], []],          # vazia nas duas
        ("analista de dados", "Maceió"): [[], []],
        ("analista de dados", "Brazil"): [[vaga(1)], [vaga(1)]],
    })
    s.buscar_vagas()
    assert len(s.chamadas) == 6          # 4 da primeira + 2 repeticoes, e para


def test_so_devolve_vaga_inedita_no_ciclo():
    """A mesma vaga costuma aparecer em varias buscas (nacional + cidade). Se a
    segunda passada devolvesse tudo, o contador de "ineditas" -- que decide se
    ela se paga -- contaria vaga que o ciclo ja tinha."""
    s = montar({
        ("analista de dados", "Brazil"): [[vaga(1), vaga(2)], [vaga(1)]],
        ("analista de dados", "Caruaru"): [[], [vaga(2), vaga(7)]],
        ("analista de dados", "Maceió"): [[vaga(3)]],
    })
    vagas = s.buscar_vagas()
    ids = [v.id for v in vagas]

    assert ids.count(vaga(2).id) == 1    # ja tinha vindo da nacional
    assert vaga(7).id in ids             # essa so a segunda passada trouxe


def test_o_log_diz_quantas_foram_ineditas(caplog):
    """O numero que decide se esta passada continua existindo tem que estar no
    log -- senao daqui a um mes ninguem sabe se ela se paga."""
    s = montar({
        ("analista de dados", "Brazil"): [[vaga(1)], [vaga(1)]],
        ("analista de dados", "Caruaru"): [[], [vaga(1), vaga(7)]],
        ("analista de dados", "Maceió"): [[vaga(3)]],
    })
    import scrapers.linkedin as mod
    with caplog.at_level(logging.INFO, logger=mod.logger.name):
        s.buscar_vagas()
    resumo = [r.message for r in caplog.records if "Segunda passada:" in r.message]
    assert any("1/1 par(es) voltaram com vaga" in m for m in resumo)
    assert any("1 inédita(s)" in m for m in resumo)


def test_cada_ciclo_recomeca_a_lista():
    """buscar_vagas roda de novo a cada ciclo; carregar as vazias do ciclo
    anterior faria repetir busca que ja nao interessa."""
    s = montar({
        ("analista de dados", "Brazil"): [[vaga(1)], [vaga(1)]],
        ("analista de dados", "Caruaru"): [[], []],
        ("analista de dados", "Maceió"): [[vaga(3)]],
    })
    s.buscar_vagas()
    assert len(s._vazias) == 1

    s.respostas = {("analista de dados", "Brazil"): [[vaga(1)], [vaga(1)]],
                   ("analista de dados", "Caruaru"): [[vaga(2)]],
                   ("analista de dados", "Maceió"): [[vaga(3)]]}
    s.buscar_vagas()
    assert s._vazias == []
