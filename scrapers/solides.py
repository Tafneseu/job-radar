from datetime import date, datetime, timezone
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

# ONDE PARAR DE PAGINAR.
#
# A primeira versao usava teto fixo de 15 paginas, escolhido porque o unico
# termo que eu tinha medido tinha 21. Ao ver "power bi: 523 vagas em 53
# paginas, lidas as 15 primeiras" no log, minha leitura foi que o teto estava
# APERTADO e vaga estava sendo perdida.
#
# MEDIDO (30/08), sondando os 45 termos do perfil BR: era o contrario.
#
#   termo                 vagas  pags   pagina em que a vaga passa de 7 dias
#   sql                     642    65          7
#   power bi                523    53          6
#   python                  347    35          5
#   analista de dados       205    21          4
#   business intelligence   138    14          3
#
# NENHUM termo passa da pagina 7 antes das vagas ficarem com mais de uma
# semana. O teto de 15 nunca cortou vaga nova -- ele lia 9 paginas a mais de
# vaga VELHA. Estava frouxo, nao apertado.
#
# Por isso o criterio deixou de ser "quantas paginas" e passou a ser "ate
# quando". A lista vem da mais recente pra mais antiga, entao basta parar
# quando as vagas ficarem velhas demais pra interessar. Custo medido, em
# requisicoes por termo:
#
#     teto fixo de 15 paginas   4,6
#     parar apos  7 dias        2,2
#     parar apos 14 dias        3,0
#     parar apos 30 dias        4,8   <- escolhido
#
# 30 dias custa praticamente o mesmo que o teto de hoje, mas distribui muito
# melhor: le fundo onde ha volume novo (power bi vai ate a pagina 18) e sai
# na pagina 2 onde o termo e parado. E se adapta sozinho quando o mercado
# muda, sem precisar recalibrar numero nenhum.
#
# 30 dias tambem e o limiar que Job.publicacao_antiga ja usa -- vaga mais
# velha que isso ganha o aviso de "pode ja estar preenchida" e sai do alerta
# imediato. Ler alem disso seria buscar exatamente o que o filtro desprioriza.
DIAS_PARA_PARAR = 30

# Trava de seguranca, nao criterio: impede laco infinito se a API passar a
# devolver data invalida ou parar de ordenar por data. O termo mais fundo
# medido (sql) precisa de 19 paginas pra passar de 30 dias.
MAX_PAGINAS = 30

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


def pagina_toda_antiga(itens: list[dict], dias: int, hoje: date | None = None) -> bool:
    """A vaga MAIS NOVA desta pagina ja passou do limite de idade?

    A lista da API vem ordenada da mais recente pra mais antiga, entao a
    partir daqui so vem coisa ainda mais velha -- da pra parar.

    Devolve False quando nenhuma data da pra ler: sem data nao ha o que
    concluir, e parar por engano custa vaga. Errar pro lado de continuar
    custa uma requisicao.
    """
    hoje = hoje or datetime.now(timezone.utc).date()
    idades = []
    for vaga in itens:
        bruto = (vaga.get("createdAt") or "").strip()[:10]
        try:
            idades.append((hoje - date.fromisoformat(bruto)).days)
        except ValueError:
            continue
    if not idades:
        return False
    return min(idades) > dias


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

            if pagina_toda_antiga(lote, DIAS_PARA_PARAR):
                logger.info(
                    f"[Solides] '{termo}': parou na página {pagina} de {total_paginas} "
                    f"— daqui pra frente só vaga com mais de {DIAS_PARA_PARAR} dias."
                )
                break

            time.sleep(PAUSA_ENTRE_PAGINAS)

        if total_paginas and total_paginas > MAX_PAGINAS:
            logger.warning(
                f"[Solides] '{termo}': bateu a trava de {MAX_PAGINAS} páginas sem "
                f"chegar em vaga de {DIAS_PARA_PARAR} dias ({total_paginas} páginas no "
                "total) — a API pode ter parado de ordenar por data."
            )
        return vagas
