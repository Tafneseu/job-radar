"""Cidade de país aceito não pode virar descarte por escopo.

MEDIDO no log de 21/08/2026:

    Descarte por escopo: Estados Unidos (39); rancagua (2); bogota dc (1)

Chile e Colômbia são mercados ACEITOS. As três vagas foram jogadas fora por
dois motivos diferentes, os dois neste arquivo.

1. RANCAGUA — cidade de país aceito que não estava no mapa cidade→mercado.
   Sem a entrada, o escopo vira o texto cru ("rancagua"), que não bate em
   mercado aceito nenhum, e a vaga é descartada. Falha silenciosa: não gera
   erro, só falta vaga.

2. BOGOTÁ, D.C. — pior que faltar: resolvia pra ESTADOS UNIDOS. O laço que lê
   a sigla depois da vírgula retornava em "dc" (District of Columbia) ANTES de
   olhar a cidade. Mas ali "D.C." é Distrito Capital, que é como a Colômbia
   escreve Bogotá. Sem o ", D.C.", a mesma vaga resolvia certo.

A segunda correção é deliberadamente estreita. Uma regra genérica de "cidade
conhecida vence a sigla" quebraria "San Jose, CA" — que é California de
verdade, não San José da Costa Rica, mesmo "san jose" estando no mapa.
"""

import pytest

from core.job import extrair_escopo_remoto
from core.perfis import PERFIL_INTL

ACEITOS = set(PERFIL_INTL.regras.mercados_remoto_aceitos)


# ------------------- cidades que estavam faltando -------------------

@pytest.mark.parametrize("local, mercado", [
    ("Remoto (Rancagua)", "Chile"),            # do log de 21/08
    ("Remoto (Granada)", "Espanha"),           # de ciclo anterior
    ("Remoto (Pachuca de Soto)", "México"),    # de ciclo anterior
    ("Remoto (Viña del Mar)", "Chile"),
    ("Remoto (Antofagasta)", "Chile"),
    ("Remoto (Concepcion)", "Chile"),
    ("Remoto (Salta)", "Argentina"),
    ("Remoto (Mar del Plata)", "Argentina"),
    ("Remoto (Puebla)", "México"),
    ("Remoto (Queretaro)", "México"),
])
def test_cidade_de_pais_aceito_resolve_o_pais(local, mercado):
    assert extrair_escopo_remoto(local) == {mercado}


# --------------------- D.C. não é sempre os EUA ---------------------

def test_bogota_dc_e_colombia_nao_estados_unidos():
    """O caso do log: "dc" ali é Distrito Capital, não District of Columbia."""
    assert extrair_escopo_remoto("Remoto (Bogotá, D.C.)") == {"Colômbia"}
    assert extrair_escopo_remoto("Remoto (Bogota, DC)") == {"Colômbia"}


def test_washington_dc_continua_estados_unidos():
    """A outra metade: "dc" com cidade americana tem que seguir sendo EUA."""
    assert extrair_escopo_remoto("Remoto (Washington, D.C.)") == {"Estados Unidos"}


@pytest.mark.parametrize("local", [
    "Remoto (Washington DC)",
    "Remoto (Washington DC-Baltimore)",
])
def test_washington_sem_virgula_nao_e_rotulado_mas_segue_rejeitado(local):
    """LACUNA que este commit NAO corrige — registrada pra nao se perder.

    Sem virgula, "washington dc" nunca vira segmento de sigla: o escopo sai
    como o texto cru. O efeito pratico e o mesmo (conjunto nao-vazio que nao
    bate mercado aceito e rejeitado), mas o log mostra "washington dc" em vez
    de "Estados Unidos", o que atrapalha na hora de ler o descarte.

    Descoberto porque um teste meu afirmou o contrario e falhou. O teste
    estava errado, nao o codigo — este ficou pra provar qual e a verdade.
    """
    escopo = extrair_escopo_remoto(local)
    assert escopo, "escopo vazio significaria 'sem restricao' — aceitaria a vaga"
    assert not (escopo & ACEITOS)


def test_san_jose_ca_continua_estados_unidos():
    """Por que a correção do "dc" é estreita de propósito.

    "san jose" está no mapa como Costa Rica. Uma regra genérica de "cidade
    conhecida vence a sigla" transformaria San Jose da California em vaga
    costa-riquenha. Só "dc" pede confirmação da cidade.
    """
    assert extrair_escopo_remoto("Remoto (San Jose, CA)") == {"Estados Unidos"}


# ----------------------------- regressão -----------------------------

@pytest.mark.parametrize("local, esperado", [
    ("Remoto (New York, NY)", "Estados Unidos"),
    ("Remoto (Austin, TX)", "Estados Unidos"),
    ("Remoto (Miami, FL)", "Estados Unidos"),
    ("Remoto (Santiago)", "Chile"),
    ("Remoto (Bogota)", "Colômbia"),
    ("Remoto (São Paulo - SP)", "Brasil"),
    ("Remoto (Fortaleza - CE)", "Brasil"),
])
def test_o_que_ja_funcionava_continua(local, esperado):
    assert extrair_escopo_remoto(local) == {esperado}
