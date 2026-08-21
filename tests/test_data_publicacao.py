"""Data de publicação: extração do card e o que conta como vaga antiga.

MEDIDO (2026-08-21): 1.061 de 1.061 vagas do LinkedIn no banco estavam com
publicado_em vazio. O scraper chamava extrair_data_publicacao(card.inner_text()),
mas o LinkedIn devolve o card em INGLÊS ("4 months ago") e os padrões da função
são todos em português ("há 4 meses", "publicada em 11/08"). Nunca casava.

Consequência: sem data, Job.publicacao_antiga sempre voltava False, e vaga velha
era notificada na hora como se fosse recém-publicada. Na página 1 de "analista
de dados" (Brasil, remoto), 4 dos 6 primeiros cards tinham mais de um mês.

O card tem <time datetime="2026-03-26"> — data absoluta, sem idioma envolvido.
"""

from datetime import date, datetime, timedelta, timezone

from core.job import (
    DIAS_PARA_PUBLICACAO_ANTIGA,
    Job,
    extrair_data_do_card,
)


def _hoje() -> date:
    return datetime.now(timezone.utc).date()


def _vaga(publicado_em: str) -> Job:
    return Job(
        titulo="Analista de Dados",
        empresa="Empresa",
        local="Brasil",
        link="https://www.linkedin.com/jobs/view/1/",
        site="LinkedIn",
        modalidade="Remoto",
        publicado_em=publicado_em,
    )


# ----------------------------- extração --------------------------------

def test_prefere_a_tag_time_ao_texto_do_card():
    """Card real: texto em inglês que nenhum padrão em português casa."""
    texto = "Analista de Dados\nGonzaga\nCuritiba, Paraná, Brazil 4 months ago"
    assert extrair_data_do_card("2026-03-26", texto) == "2026-03-26"


def test_texto_em_ingles_sozinho_nao_produz_data():
    """Confirma o bug original: sem a tag, o texto em inglês não vira nada."""
    texto = "Analista de Dados\nGonzaga\nCuritiba, Paraná, Brazil 4 months ago"
    assert extrair_data_do_card(None, texto) == ""


def test_card_sem_idade_no_texto_mas_com_tag():
    """Card real medido: o texto não trazia a idade, só a tag tinha."""
    texto = "Analista de Dados – O Boticário\nGrupo Nícia\nPalmas, Tocantins, Brazil "
    assert extrair_data_do_card("2026-06-12", texto) == "2026-06-12"


def test_fonte_sem_tag_continua_usando_o_texto():
    """Regressão: quem não tem <time> tem que funcionar igual a antes."""
    assert extrair_data_do_card(None, "Publicada em 11/08") == "Publicada em 11/08"
    assert extrair_data_do_card("", "Há 4 meses") == "Há 4 meses"


def test_atributo_invalido_cai_pro_texto():
    """datetime lixo não pode virar data — cai pro caminho antigo."""
    assert extrair_data_do_card("ontem", "Há 4 meses") == "Há 4 meses"
    assert extrair_data_do_card("2026-3-6", "Há 4 meses") == "Há 4 meses"


# --------------------------- vaga antiga -------------------------------

def test_data_iso_recente_nao_e_antiga():
    recente = (_hoje() - timedelta(days=2)).isoformat()
    assert _vaga(recente).publicacao_antiga is False


def test_data_iso_no_limiar_e_antiga():
    """O limiar é o mesmo que o formato relativo já usava: 1 mês."""
    limiar = (_hoje() - timedelta(days=DIAS_PARA_PUBLICACAO_ANTIGA)).isoformat()
    assert _vaga(limiar).publicacao_antiga is True


def test_data_iso_um_dia_antes_do_limiar_nao_e_antiga():
    quase = (_hoje() - timedelta(days=DIAS_PARA_PUBLICACAO_ANTIGA - 1)).isoformat()
    assert _vaga(quase).publicacao_antiga is False


def test_card_real_de_quatro_meses_e_antiga():
    """A vaga do card 1 medido, publicada em 26/03/2026."""
    assert _vaga("2026-03-26").publicacao_antiga is True


def test_formato_relativo_continua_valendo():
    """Regressão: Sólides e afins não podem mudar de comportamento."""
    assert _vaga("há 7 meses").publicacao_antiga is True
    assert _vaga("Há 1 ano").publicacao_antiga is True
    assert _vaga("há 5 dias").publicacao_antiga is False
    assert _vaga("há 3 semanas").publicacao_antiga is False
    assert _vaga("").publicacao_antiga is False


def test_data_iso_impossivel_nao_quebra():
    """Dia 32 não existe: não pode estourar exceção no meio do ciclo."""
    assert _vaga("2026-02-31").publicacao_antiga is False


# ------------------------- exibição na mensagem ------------------------

def test_data_iso_vira_formato_brasileiro_na_mensagem():
    """"Postada 2026-03-26" se lê mal; "Postada 26/03/2026" não."""
    assert _vaga("2026-03-26").publicado_em_legivel == "26/03/2026"


def test_formato_nao_iso_vai_pra_tela_intacto():
    assert _vaga("há 7 meses").publicado_em_legivel == "há 7 meses"
    assert _vaga("Publicada em 11/08").publicado_em_legivel == "Publicada em 11/08"
    assert _vaga("").publicado_em_legivel == ""
