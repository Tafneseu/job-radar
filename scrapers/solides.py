import time

import requests

from core.job import Job
from core.logger import get_logger
from scrapers.base import BaseScraper

logger = get_logger()

# API que o proprio portal da Solides chama pra montar a busca.
#
# MEDIDO (2026-08-29): o scraper anterior abria um NAVEGADOR e raspava HTML
# com "li:has(h2 a)", lendo 3 paginas de 10 = teto de 30 vagas por termo, e
# levava ~7 minutos por ciclo (45 cargas de pagina). Esta API responde, pro
# mesmo termo "analista de dados":
#
#     {"data": {"count": 205, "totalPages": 21}}
#
# 205 contra 30. Mesma historia da Gupy: a fonte sempre teve volume, o
# scraper e que so via o comeco.
#
# O que a troca resolve, alem do alcance:
#   - paginacao deixa de ser adivinhada CONTANDO cards (mecanismo que ja
#     produziu alarme falso aqui, ver 9520409). A resposta declara count e
#     totalPages.
#   - state.code vem com a SIGLA pronta ("ES"), melhor que a Gupy, que da o
#     estado por extenso.
#   - createdAt ja vem em AAAA-MM-DD, o formato que Job.publicacao_antiga
#     espera.
#   - sem navegador: nada de seletor pra quebrar, e o ciclo encurta muito.
#
# O QUE NAO MUDOU, de proposito: o Job montado, o filtro, a pontuacao, a
# deduplicacao e o formato do log. So a camada de busca foi trocada.
URL_API = "https://apigw.solides.com.br/jobs/v3/portal-vacancies-new"

# MEDIDO: take so aceita 10. Testado com 20, 30, 50, 60 e 100 -- todos
# devolvem count=0, nao so lista vazia. O tamanho de pagina e fixo.
TAKE = 10

# Paginas por termo. NAO e limite da API (o termo medido tinha 21) -- e
# escolha: a lista vem da mais recente pra mais antiga, entao as ultimas
# paginas sao as vagas mais velhas. 15 paginas = 150 vagas, 5x o teto
# anterior, e ainda deixa o ciclo mais rapido que os ~7 minutos de hoje.
MAX_PAGINAS = 15

PAUSA_ENTRE_PAGINAS = 0.5
TIMEOUT = 30
UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)

# jobType da API -> vocabulario que o filtro ja usa (ver core/job.py).
_MODALIDADE = {
    "presencial": "Presencial",
    "hibrido": "Híbrido",
    "híbrido": "Híbrido",
    "remoto": "Remoto",
    "home office": "Remoto",
    "homeoffice": "Remoto",
}


def montar_local(vaga: dict) -> str:
    """Monta `local` como "Cidade - UF", que e um dos formatos que o filtro
    ja reconhece.

    A Solides entrega a SIGLA em state.code ("ES"), diferente da Gupy, que
    so da o nome por extenso. Sigla e o caminho mais curto e mais seguro na
    conferencia de UF (ver _uf_declarada em core/job.py).
    """
    cidade = ((vaga.get("city") or {}).get("name") or "").strip()
    sigla = ((vaga.get("state") or {}).get("code") or "").strip()
    if cidade and sigla:
        return f"{cidade} - {sigla}"
    return cidade or sigla or "Não informado"


def montar_modalidade(vaga: dict) -> str:
    """Traduz jobType. homeOffice entra como reforco: sao campos
    independentes, e vaga remota as vezes chega com so um deles."""
    bruto = (vaga.get("jobType") or "").strip().lower()
    if bruto in _MODALIDADE:
        return _MODALIDADE[bruto]
    if vaga.get("homeOffice") is True:
        return "Remoto"
    return ""


def montar_job(vaga: dict) -> Job | None:
    """Converte um item da API num Job. None quando falta o essencial.

    Funcao pura de proposito: e o que da pra testar sem rede, e onde mora
    todo o risco da troca -- mapear campo errado nao quebra nada, so muda
    silenciosamente o que e aprovado.
    """
    titulo = (vaga.get("title") or "").strip()
    link = (vaga.get("redirectLink") or "").strip()
    if not titulo or not link:
        return None

    return Job(
        titulo=titulo,
        empresa=(vaga.get("companyName") or "Não informado").strip(),
        local=montar_local(vaga),
        link=link,
        site="Solides",
        publicado_em=(vaga.get("createdAt") or "").strip()[:10],
        modalidade=montar_modalidade(vaga),
    )


class SolidesScraper(BaseScraper):
    """Busca vagas na API publica do portal da Sólides."""

    def __init__(self, termos_busca: list[str]):
        self.termos_busca = termos_busca

    def buscar_vagas(self) -> list[Job]:
        vagas: list[Job] = []
        for termo in self.termos_busca:
            vagas.extend(self._buscar_termo(termo))
        logger.info(f"[Solides] {len(vagas)} vaga(s) encontrada(s) no total")
        return vagas

    def _buscar_termo(self, termo: str) -> list[Job]:
        logger.info(f"[Solides] Buscando: {termo}")
        vagas: list[Job] = []
        total_paginas = None
        total = None

        for pagina in range(1, MAX_PAGINAS + 1):
            try:
                resposta = requests.get(
                    URL_API,
                    params={"title": termo, "take": TAKE, "page": pagina},
                    timeout=TIMEOUT,
                    headers={"User-Agent": UA, "Accept": "application/json"},
                )
            except Exception as erro:
                logger.error(f"[Solides] Erro ao buscar '{termo}' (página {pagina}): {erro}")
                break

            if resposta.status_code != 200:
                logger.warning(
                    f"[Solides] Status {resposta.status_code} em '{termo}' "
                    f"(página {pagina}) — resposta inesperada da API, não é busca vazia."
                )
                break

            try:
                corpo = resposta.json()
            except ValueError:
                logger.warning(f"[Solides] Resposta não-JSON em '{termo}' (página {pagina}).")
                break

            dados = corpo.get("data") or {}
            lote = dados.get("data") or []

            if total_paginas is None:
                total = dados.get("count", 0)
                total_paginas = dados.get("totalPages", 0)
                if not total:
                    logger.info(f"[Solides] 0 resultados reais para '{termo}'.")
                    break

            for item in lote:
                job = montar_job(item)
                if job is not None:
                    vagas.append(job)

            if not lote or pagina >= total_paginas:
                break
            time.sleep(PAUSA_ENTRE_PAGINAS)

        if total_paginas and total_paginas > MAX_PAGINAS:
            logger.info(
                f"[Solides] '{termo}': {total} vagas em {total_paginas} páginas, lidas as "
                f"{MAX_PAGINAS} primeiras (teto do scraper, não fim dos resultados)."
            )
        return vagas
