"""Portal de Talentos da Senior — busca por API, sem navegador.

Diferente de TODAS as outras fontes deste projeto: não abre Playwright, não
espera seletor, não pagina por tentativa e erro. É um POST que devolve JSON
com os campos já separados.

Isso importa além da velocidade. A classe de bug que mais custou tempo aqui
(ver extrair_escopo_remoto em core/job.py) existe porque as outras fontes
entregam localização como texto solto — "En remoto", "Móstoles, Madrid",
"São Paulo - SP" — e é preciso adivinhar o que é cidade, o que é estado e o
que é país. Aqui vem `localization.city`, `.province` e `.country` em campos
próprios. Não há o que adivinhar.

Também é a ÚNICA fonte do projeto que informa data de publicação de verdade
(`publication.startDate`, formato ISO). Nas outras, Job.publicado_em fica
vazio em praticamente todo registro — 1.279 de 1.280 no banco medido — o que
deixa Job.publicacao_antiga sem efeito prático.

AVISO — não testado em produção: o endpoint respondeu bem a partir de uma
máquina doméstica, mas o robô roda no GitHub Actions, cujo IP é de
datacenter. Foi exatamente esse tipo de IP que fez o Indeed ser bloqueado
(ver MEDIDO em core/perfis.py). Só a execução real dirá se a Senior faz o
mesmo. Por isso entra como FREQUENCIA_BAIXA quando for ligada.
"""

from datetime import date

import requests

from core.job import Job
from core.logger import get_logger
from scrapers.base import BaseScraper

logger = get_logger()

URL_BUSCA = (
    "https://platform.senior.com.br/t/senior.com.br/bridge/1.0/anonymous/rest"
    "/hcm/careersmanagercandidate/queries/searchVacancies"
)

# Forma GENÉRICA do link, sem o subdomínio da empresa. Confirmado ao vivo que
# abre a vaga. A forma por empresa (ourosafra.portaldetalentos...) também
# funciona, mas exigiria derivar o subdomínio de `company.tenant` — e os dois
# não batem: o tenant do JSON vem "ourosafracombr" enquanto o subdomínio real
# é "ourosafra". Sem regra confiável entre um e outro, a forma genérica evita
# o problema inteiro.
URL_VAGA = "https://www.portaldetalentos.senior.com.br/vacancy/"

TAMANHO_PAGINA = 50
MAX_PAGINAS = 3
TIMEOUT_SEGUNDOS = 30

# `jobModel` é uma LISTA. Quando traz mais de um valor, a vaga aceita mais de
# um arranjo — e aí a leitura mais favorável é a que abre mais portas para
# quem procura: remoto vence híbrido, que vence presencial.
_MODALIDADE = {
    "REMOTE": "Remoto",
    "HYBRID": "Híbrido",
    "IN_PERSON": "Presencial",
}
_PRIORIDADE_MODALIDADE = ["REMOTE", "HYBRID", "IN_PERSON"]


def _modalidade(job_model) -> str:
    """Traduz jobModel para o vocabulário de Job.modalidade. "" quando o campo
    vem vazio ou com valor desconhecido — melhor deixar em branco do que
    afirmar errado (ver _confirma_remoto em core/job.py: campo vazio ainda
    tem o texto de `local` como segunda chance; campo errado não tem)."""
    valores = job_model if isinstance(job_model, list) else [job_model]
    valores = {str(v).upper() for v in valores if v}
    for chave in _PRIORIDADE_MODALIDADE:
        if chave in valores:
            return _MODALIDADE[chave]
    return ""


def _local(localizacao: dict) -> str:
    """Monta o texto de `local` no formato que o filtro já entende
    ("Natal - RN"). País só entra quando não é Brasil — senão viraria ruído
    em toda vaga, e o filtro de cidade trabalha sobre esse mesmo texto."""
    cidade = (localizacao or {}).get("city") or ""
    uf = (localizacao or {}).get("province") or ""
    pais = (localizacao or {}).get("country") or ""

    partes = " - ".join(p for p in (cidade.strip(), uf.strip()) if p)
    if pais and pais.strip().lower() not in ("brasil", "brazil"):
        partes = f"{partes}, {pais.strip()}" if partes else pais.strip()
    return partes or "Não informado"


def _expirada(publicacao: dict, hoje: date | None = None) -> bool:
    """A API devolve vaga com publicação encerrada. Sem esse corte, o
    JobRadar notificaria vaga que não aceita mais candidatura — o oposto do
    que Job.publicacao_antiga tenta evitar. Data ilegível NÃO é tratada como
    expirada: na dúvida, mostra."""
    fim = (publicacao or {}).get("endDate")
    if not fim:
        return False
    try:
        return date.fromisoformat(str(fim)[:10]) < (hoje or date.today())
    except ValueError:
        return False


def montar_job(item: dict, hoje: date | None = None) -> Job | None:
    """Converte um item de `contents` num Job. None quando a vaga não serve
    (sem id, sem título ou com publicação encerrada).

    Separada da chamada de rede de propósito: é a parte que vale testar, e dá
    pra testar com o JSON real capturado da API, sem tocar na rede."""
    vaga = (item or {}).get("vacancy") or {}
    empresa = (item or {}).get("company") or {}

    id_vaga = vaga.get("id")
    titulo = (vaga.get("title") or "").strip()
    if not id_vaga or not titulo:
        return None

    if _expirada(vaga.get("publication"), hoje):
        return None

    return Job(
        titulo=titulo,
        empresa=(empresa.get("name") or "Não informada").strip(),
        local=_local(vaga.get("localization")),
        link=f"{URL_VAGA}{id_vaga}",
        site="Senior",
        publicado_em=str((vaga.get("publication") or {}).get("startDate") or ""),
        modalidade=_modalidade(vaga.get("jobModel")),
    )


class SeniorScraper(BaseScraper):

    def __init__(self, termos_busca: list[str]):
        self.termos_busca = termos_busca

    def buscar_vagas(self) -> list[Job]:
        vagas: list[Job] = []
        for termo in self.termos_busca:
            vagas.extend(self._buscar_termo(termo))
        logger.info(f"[Senior] {len(vagas)} vaga(s) encontrada(s) no total")
        return vagas

    def _buscar_termo(self, termo: str) -> list[Job]:
        logger.info(f"[Senior] Buscando: {termo}")
        encontradas: list[Job] = []

        for pagina in range(MAX_PAGINAS):
            try:
                dados = self._consultar(termo, pagina)
            except requests.HTTPError as e:
                status = e.response.status_code if e.response is not None else "?"
                logger.warning(
                    f"[Senior] HTTP {status} na página {pagina + 1} de '{termo}' — "
                    "parando de paginar (possível bloqueio ou termo inválido)."
                )
                break
            except requests.RequestException as e:
                # Nunca loga str(e): a mensagem do requests inclui a URL, e o
                # padrão desta base é não jogar URL no log (ver notifier/
                # telegram.py). Aqui a URL não tem segredo, mas manter o mesmo
                # hábito evita que a exceção mude e passe a ter.
                logger.warning(
                    f"[Senior] {type(e).__name__} na página {pagina + 1} de '{termo}' — "
                    "parando de paginar."
                )
                break

            itens = dados.get("contents") or []
            if not itens:
                break

            for item in itens:
                job = montar_job(item)
                if job is not None:
                    encontradas.append(job)

            # A API informa o total de páginas — não precisa descobrir o fim
            # por tentativa e erro como as fontes de navegador fazem (é daí
            # que vem boa parte dos timeouts no log).
            if pagina + 1 >= int(dados.get("totalPages") or 0):
                break

        return encontradas

    def _consultar(self, termo: str, pagina: int) -> dict:
        corpo = {
            "page": pagina,
            "filter": termo,
            "size": TAMANHO_PAGINA,
            "match": {"localizations": [], "companies": []},
        }
        resposta = requests.post(URL_BUSCA, json=corpo, timeout=TIMEOUT_SEGUNDOS)
        resposta.raise_for_status()
        return resposta.json()
