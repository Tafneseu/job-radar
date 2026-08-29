"""Cargo ambíguo no perfil INTERNACIONAL: o que entra e o que continua fora.

MEDIDO (2026-08-29) contra 175 vagas reais do LinkedIn Intl (8 termos, 3
mercados, 85 já aprovadas pela regra da época). Dois caminhos na MESMA amostra:

    A) hoje                                         85
    B) + as keywords fortes em inglês que o BR tem  85   (+0)
    C) + cargo ambíguo "Analyst"                    92   (+7)

O caminho B era a hipótese preferida — a de que o buraco fosse só as duas
listas de keywords terem divergido ao longo do tempo, e não falta de
mecanismo. A medição a DERRUBOU: alinhar as listas não ganhou uma vaga.

Três das 7 vinham aparecendo no log ciclo após ciclo como "barradas só pelo
título": Oliver Wyman (Lisboa, duas grafias) e Air Liquide (Lisboa).
"""

import pytest

from core.job import Job
from core.perfis import PERFIL_INTL

REGRAS = PERFIL_INTL.regras


def _vaga(titulo: str, local: str = "Remoto (Lisboa)", modalidade: str = "Remoto") -> Job:
    return Job(
        titulo=titulo,
        empresa="Empresa",
        local=local,
        link="https://www.linkedin.com/jobs/view/" + titulo,
        site="LinkedIn Internacional",
        modalidade=modalidade,
    )


# ------------------- as 7 vagas medidas, uma a uma -------------------

@pytest.mark.parametrize("titulo", [
    "Data & Analytics Analyst",
    "Data & Analytics Analyst - Lisbon",
    "Business & Data Integration Analyst (m/f)- HR Analytics",
    "Senior Data Research Analyst (Portuguese speaker)",
    "Multilingual Data Research Analyst",
    "Analytics Analyst - Remote Work | REF#301318",
    "Data Services Analyst",
])
def test_as_vagas_medidas_passam(titulo):
    assert _vaga(titulo).combina_com(REGRAS)


# ----------- a proteção: sem qualificador de dados, não passa -----------

@pytest.mark.parametrize("titulo", [
    "Financial Analyst",
    "HR Analyst",
    "Analyst, Investment Banking",
    "Credit Risk Analyst",
    "Spanish Speaker Customer Support Analyst",
    "Procurement Analyst",
    "Compensation Analyst",
])
def test_analyst_sem_qualificador_de_dados_nao_passa(titulo):
    """É isto que permite "Analyst" existir na lista sem virar porta aberta.

    "Spanish Speaker Customer Support Analyst" está aqui de propósito: os
    termos de busca deste perfil incluem "spanish speaker" solto, então esse
    é o formato de ruído que ele mais teria chance de trazer.
    """
    assert not _vaga(titulo).combina_com(REGRAS)


# ------------------ o ruído previsto, medido e vigiado ------------------

@pytest.mark.parametrize("titulo", [
    "Data Center Operations Analyst",
    "Data Entry Analyst",
])
def test_ruido_conhecido_passa(titulo):
    """RISCO ACEITO CONSCIENTEMENTE, não bug — mesmo caso do perfil BR.

    "data center" e "data entry" casam o qualificador "data" sem serem
    análise de dados. Nenhum apareceu nas 175 da amostra, por isso o
    mecanismo entrou mesmo assim.

    Este teste FIXA o comportamento atual em vez de fingir que o problema não
    existe. Se um dia esse ruído incomodar, o teste mostra que ele sempre
    esteve previsto — e inverte junto com a correção.
    """
    assert _vaga(titulo).combina_com(REGRAS)


# --------------- o que a regra de negócio ainda barra ---------------

@pytest.mark.parametrize("modalidade", ["Presencial", "Híbrido"])
def test_cargo_ambiguo_nao_afrouxa_a_regra_de_localizacao(modalidade):
    """Internacional é REMOTO. A keyword nova não pode abrir essa porta."""
    assert not _vaga("Data & Analytics Analyst",
                     local="Lisbon, Lisbon, Portugal",
                     modalidade=modalidade).combina_com(REGRAS)


def test_mercado_fora_da_lista_continua_barrado():
    """Remoto só vale nos mercados aceitos — EUA continua fora."""
    assert not _vaga("Data & Analytics Analyst",
                     local="Remoto (New York, NY)").combina_com(REGRAS)


# ------------------- o que NÃO entrou, e por quê -------------------

def test_especialista_e_analista_nao_entraram():
    """Ambas mediram 0 vagas a mais na amostra — não entraram por falta de
    evidência, não por decisão de gosto."""
    assert PERFIL_INTL.regras.keywords_ambiguo == ["Analyst"]


def test_os_dois_perfis_usam_o_mesmo_mecanismo():
    """Foi a divergência silenciosa entre os dois perfis que deixou o
    internacional sem cargo ambíguo por tanto tempo. Este teste cobra que os
    dois pelo menos usem a MESMA lista de qualificadores."""
    from core.perfis import PERFIL_BR
    assert PERFIL_INTL.regras.qualificadores_dados == PERFIL_BR.regras.qualificadores_dados
