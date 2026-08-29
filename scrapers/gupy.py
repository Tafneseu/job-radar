import time

import requests

from core.job import Job
from core.logger import get_logger
from scrapers.base import BaseScraper

logger = get_logger()

# API que o proprio portal da Gupy chama pra montar a pagina de busca.
#
# MEDIDO (2026-08-29): o scraper anterior abria um NAVEGADOR e raspava o HTML
# com o seletor "a:has(h3)", lendo no maximo 3 paginas de 12 = teto de 36
# vagas por termo. Esta API responde, pro mesmo termo "analista de dados":
#
#     {"pagination": {"total": 252, "limit": 10, "offset": 0}}
#
# 252 contra 36. Era essa a explicacao das 59 vagas que a Gupy trouxe em tres
# semanas -- volume ela sempre teve; o scraper e que so enxergava o comeco.
#
# O que a troca resolve, alem do alcance:
#   - paginacao deixa de ser adivinhada. O scraper de HTML descobria o fim
#     CONTANDO cards e comparando com a primeira pagina, mecanismo que ja
#     produziu alarme falso de "vaga perdida" (ver d5db08a). Aqui a propria
#     resposta diz o total: para-se quando offset >= total, sem heuristica.
#   - publishedDate e workplaceType vem prontos. O HTML nao trazia data
#     nenhuma, e vaga velha chegava como nova.
#   - sem navegador: nao ha seletor pra quebrar quando a Gupy mexe no layout,
#     e o ciclo encurta.
#
# O QUE NAO MUDOU, de proposito: o Job montado, o filtro, a pontuacao, a
# deduplicacao e o formato do log. So a camada de busca foi trocada.
URL_API = "https://employability-portal.gupy.io/api/v1/jobs"

# A API aceita limit alto (testado com 100). 100 por requisicao cobre a
# maioria dos termos numa chamada so.
LIMITE_POR_PAGINA = 100

# Teto de vagas por termo. Nao e limitacao da API -- e escolha: termo
# concorrido tem centenas, e as ultimas sao as mais antigas e menos
# relevantes. 300 e 8x o teto anterior e ainda cabe em 3 requisicoes.
MAX_VAGAS_POR_TERMO = 300

TIMEOUT = 30
UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)

# workplaceType da API -> vocabulario que o filtro ja usa (ver
# _FLAGS_REMOTO e as regras de cidade em core/job.py).
_MODALIDADE = {
    "on-site": "Presencial",
    "onsite": "Presencial",
    "hybrid": "Híbrido",
    "remote": "Remoto",
}


def montar_local(vaga: dict) -> str:
    """Monta o campo `local` a partir de city/state.

    A API devolve o estado por EXTENSO ("São Paulo", "Ceará"), nao a sigla.
    Isso funciona porque a guarda de UF passou a ler estado por extenso em
    5e91895 -- antes desse commit, "Campina Grande, Paraná" passaria como se
    fosse a Campina Grande da Paraiba. Os testes cobrem exatamente esse caso.
    """
    cidade = (vaga.get("city") or "").strip()
    estado = (vaga.get("state") or "").strip()
    if cidade and estado:
        return f"{cidade}, {estado}"
    return cidade or estado or "Não informado"


def montar_modalidade(vaga: dict) -> str:
    """Traduz workplaceType. isRemoteWork entra como reforco: sao dois campos
    independentes na resposta, e vaga remota as vezes chega com um so."""
    bruto = (vaga.get("workplaceType") or "").strip().lower()
    if bruto in _MODALIDADE:
        return _MODALIDADE[bruto]
    if vaga.get("isRemoteWork") is True:
        return "Remoto"
    return ""


def montar_job(vaga: dict) -> Job | None:
    """Converte um item da API num Job. None quando falta o essencial.

    Funcao pura de proposito: e o que da pra testar sem rede, e onde mora
    todo o risco da troca (mapear campo errado passa despercebido).
    """
    titulo = (vaga.get("name") or "").strip()
    link = (vaga.get("jobUrl") or "").strip()
    if not titulo or not link:
        return None

    # publishedDate vem como "2026-08-28T21:28:28.868Z". Job.publicacao_antiga
    # e publicado_em_legivel esperam a data ISO pura (ver core/job.py).
    publicado = (vaga.get("publishedDate") or "")[:10]

    return Job(
        titulo=titulo,
        empresa=(vaga.get("careerPageName") or "Não informado").strip(),
        local=montar_local(vaga),
        link=link,
        site="Gupy",
        publicado_em=publicado,
        modalidade=montar_modalidade(vaga),
    )


class GupyScraper(BaseScraper):
    """Busca vagas na API publica do portal da Gupy."""

    def __init__(self, termos_busca: list[str]):
        self.termos_busca = termos_busca

    def buscar_vagas(self) -> list[Job]:
        vagas: list[Job] = []
        for termo in self.termos_busca:
            vagas.extend(self._buscar_termo(termo))
        logger.info(f"[Gupy] {len(vagas)} vaga(s) encontrada(s) no total")
        return vagas

    def _buscar_termo(self, termo: str) -> list[Job]:
        logger.info(f"[Gupy] Buscando: {termo}")
        vagas: list[Job] = []
        offset = 0
        total = None

        while offset < MAX_VAGAS_POR_TERMO:
            try:
                resposta = requests.get(
                    URL_API,
                    params={"jobName": termo, "limit": LIMITE_POR_PAGINA, "offset": offset},
                    timeout=TIMEOUT,
                    headers={"User-Agent": UA},
                )
            except Exception as erro:
                logger.error(f"[Gupy] Erro ao buscar '{termo}' (offset {offset}): {erro}")
                break

            if resposta.status_code != 200:
                logger.warning(
                    f"[Gupy] Status {resposta.status_code} em '{termo}' (offset {offset}) "
                    "— resposta inesperada da API, não é busca vazia."
                )
                break

            try:
                dados = resposta.json()
            except ValueError:
                logger.warning(f"[Gupy] Resposta não-JSON em '{termo}' (offset {offset}).")
                break

            lote = dados.get("data") or []
            if total is None:
                total = (dados.get("pagination") or {}).get("total", 0)
                if not total:
                    logger.info(f"[Gupy] 0 resultados reais para '{termo}'.")
                    break

            for item in lote:
                job = montar_job(item)
                if job is not None:
                    vagas.append(job)

            offset += LIMITE_POR_PAGINA
            if not lote or offset >= total:
                break
            time.sleep(1)

        if total and total > MAX_VAGAS_POR_TERMO:
            logger.info(
                f"[Gupy] '{termo}': {total} vagas no total, lidas as {MAX_VAGAS_POR_TERMO} "
                "mais recentes (teto do scraper, não fim dos resultados)."
            )
        return vagas
