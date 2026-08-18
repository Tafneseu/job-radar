"""Regressao do disparo do digest diario.

MEDIDO em producao: o digest NUNCA foi enviado. A prova esta na tabela
metadados do jobs.db de producao -- tem heartbeat_ultimo_dia_* das duas
chaves, atualizadas, e nenhum digest_ultimo_dia_*. O heartbeat e disparado
na linha anterior ao digest, no mesmo ciclo: se ele chega e o digest nao, a
diferenca esta na condicao de horario. Resultado real: 408 vagas presas na
fila, acumulando desde que o recurso existe.

Eram duas causas somadas:
  1. a condicao exigia que o ciclo TERMINASSE dentro de uma janela de 1
     hora, mas o cron so COMECA na hora certa e o ciclo dura ~80 min;
  2. a rede de seguranca ("se pular um dia, manda no ciclo seguinte")
     exigia um envio anterior registrado -- e nunca houve o primeiro.

Nenhum teste cobria o disparo, so o conteudo do digest. Por isso o recurso
passou a vida inteira sem nunca funcionar.
"""

from datetime import datetime, timezone

import pytest

import main
from core.perfis import PERFIL_BR


class _RelogioFalso:
    """Substitui datetime dentro de main so pra fixar o instante em que o
    ciclo termina -- e esse instante, nao o do agendamento, que a regra le."""

    def __init__(self, instante):
        self._instante = instante

    def now(self, tz=None):
        return self._instante


@pytest.fixture
def cenario(monkeypatch):
    estado = {"enviadas": [], "metadados": {}, "marcou_enviado": [], "envio_ok": True}

    def enviar_digest(vagas, rotulo):
        estado["enviadas"].append((rotulo, list(vagas)))
        return estado["envio_ok"]

    monkeypatch.setattr(main, "enviar_digest", enviar_digest)
    monkeypatch.setattr(main, "obter_metadado", lambda c: estado["metadados"].get(c))
    monkeypatch.setattr(main, "definir_metadado", lambda c, v: estado["metadados"].__setitem__(c, v))
    monkeypatch.setattr(main, "marcar_digest_enviado", lambda p: estado["marcou_enviado"].append(p))
    # Fixa a hora nos testes de LOGICA pra eles nao quebrarem quando o
    # valor real de DIGEST_HORA_UTC mudar (o horario de entrega e escolha
    # da usuaria, nao regra do codigo). O valor real tem teste proprio, no
    # fim do arquivo.
    monkeypatch.setattr(main, "DIGEST_HORA_UTC", 0)
    monkeypatch.setattr(main, "obter_vagas_pendentes_digest",
                        lambda p: [("Analista de Dados", "Empresa", "https://x/1", 6, 0)])
    return estado


def _rodar(monkeypatch, hora_utc, dia=18):
    instante = datetime(2026, 8, dia, hora_utc, 39, tzinfo=timezone.utc)
    monkeypatch.setattr(main, "datetime", _RelogioFalso(instante))
    main._enviar_digest_diario(PERFIL_BR)


def test_envia_mesmo_o_ciclo_terminando_fora_da_hora_exata(cenario, monkeypatch):
    """O caso real que estava quebrado: cron das 00:00 UTC, ciclo de ~80
    min, funcao rodando 01:39. A condicao antiga (hora == 0) barrava."""
    _rodar(monkeypatch, hora_utc=1)
    assert len(cenario["enviadas"]) == 1
    assert cenario["marcou_enviado"] == ["brasil"]
    assert cenario["metadados"]["digest_ultimo_dia_brasil"] == "2026-08-18"


@pytest.mark.parametrize("hora", [0, 1, 2, 5, 11, 17, 23])
def test_envia_no_primeiro_ciclo_do_dia_qualquer_que_seja_a_hora(cenario, monkeypatch, hora):
    """Com DIGEST_HORA_UTC = 0, todo ciclo do dia esta "depois da hora" --
    entao vale o primeiro que rodar, sem depender de o Actions atrasar."""
    _rodar(monkeypatch, hora_utc=hora)
    assert len(cenario["enviadas"]) == 1


def test_nao_envia_duas_vezes_no_mesmo_dia(cenario, monkeypatch):
    _rodar(monkeypatch, hora_utc=1)
    _rodar(monkeypatch, hora_utc=4)
    _rodar(monkeypatch, hora_utc=7)
    assert len(cenario["enviadas"]) == 1


def test_volta_a_enviar_no_dia_seguinte(cenario, monkeypatch):
    _rodar(monkeypatch, hora_utc=1, dia=18)
    _rodar(monkeypatch, hora_utc=1, dia=19)
    assert len(cenario["enviadas"]) == 2


def test_sem_envio_anterior_registrado_ainda_assim_envia(cenario, monkeypatch):
    """A rede de seguranca antiga exigia ultimo_envio nao-nulo, entao
    dependia exatamente do que deveria consertar. Fila vazia de metadado
    (o estado real da producao) nao pode mais impedir o envio."""
    assert cenario["metadados"] == {}
    _rodar(monkeypatch, hora_utc=2)
    assert len(cenario["enviadas"]) == 1


def test_falha_de_envio_nao_marca_como_enviado(cenario, monkeypatch):
    """Preferir duplicar a perder vaga: se o Telegram falhar, a fila fica
    intacta e tenta de novo no ciclo seguinte."""
    cenario["envio_ok"] = False
    _rodar(monkeypatch, hora_utc=1)
    assert cenario["marcou_enviado"] == []
    assert "digest_ultimo_dia_brasil" not in cenario["metadados"]


def test_fila_vazia_marca_o_dia_sem_mandar_mensagem(cenario, monkeypatch):
    monkeypatch.setattr(main, "obter_vagas_pendentes_digest", lambda p: [])
    _rodar(monkeypatch, hora_utc=1)
    assert cenario["enviadas"] == []
    assert cenario["metadados"]["digest_ultimo_dia_brasil"] == "2026-08-18"


def test_respeita_hora_configurada_quando_nao_e_zero(cenario, monkeypatch):
    """Se DIGEST_HORA_UTC for 21, ciclo que termina as 20h ainda nao envia;
    o primeiro depois das 21h envia."""
    monkeypatch.setattr(main, "DIGEST_HORA_UTC", 21)
    _rodar(monkeypatch, hora_utc=20)
    assert cenario["enviadas"] == []
    _rodar(monkeypatch, hora_utc=22)
    assert len(cenario["enviadas"]) == 1


def test_hora_configurada_entrega_de_manha_e_nao_de_madrugada(cenario, monkeypatch):
    """Usa o valor REAL de DIGEST_HORA_UTC, nao um fixado no teste.

    A escolha da usuaria foi receber de manha. Com cron de 3 em 3 horas e
    ciclo de ~80 min, isso quer dizer: ciclo terminando ~04h UTC (01h da
    madrugada em Brasilia) NAO manda; ciclo terminando ~10h UTC (07h20 em
    Brasilia) manda."""
    from core.config import DIGEST_HORA_UTC

    monkeypatch.setattr(main, "DIGEST_HORA_UTC", DIGEST_HORA_UTC)

    _rodar(monkeypatch, hora_utc=4)   # 01h da manha em Brasilia
    assert cenario["enviadas"] == [], "digest nao pode chegar de madrugada"

    _rodar(monkeypatch, hora_utc=10)  # 07h20 da manha em Brasilia
    assert len(cenario["enviadas"]) == 1, "digest tem que chegar de manha"


# ------------------------------------------------- ALERTA DE SAUDE

@pytest.mark.parametrize("com_problema, total, esperado", [
    # 2 fontes: o caso que motivou a mudanca. Perfil Internacional ficou
    # assim quando o Indeed foi desligado, e o WeWorkRemotely e pequeno o
    # bastante pra voltar vazio num dia fraco.
    (0, 2, False),
    (1, 2, False),   # antes disparava aqui -- alerta falso
    (2, 2, True),    # as duas cairam: e problema de verdade
    # 3 fontes (perfil Brasil num ciclo normal): comportamento inalterado.
    (1, 3, False),
    (2, 3, True),
    (3, 3, True),
    # 7 fontes (Brasil no ciclo que roda as de baixa frequencia).
    (3, 7, False),
    (4, 7, True),
    # numero par maior: fica um pouco mais exigente, de proposito.
    (4, 8, False),
    (5, 8, True),
    # nenhuma fonte no ciclo: nao ha o que alertar.
    (0, 0, False),
])
def test_alerta_de_saude_exige_maioria_estrita(com_problema, total, esperado):
    """Alerta que dispara sem motivo deixa de ser lido -- e ai nao serve nem
    quando o problema e real."""
    assert main._deve_alertar_saude(com_problema, total) is esperado
