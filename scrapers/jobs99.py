
import re
import time
from urllib.parse import quote_plus

from playwright.sync_api import sync_playwright

from core.job import Job, extrair_data_publicacao
from core.logger import get_logger
from scrapers.base import BaseScraper

logger = get_logger()

_MODALIDADES = {"remota", "híbrida", "hibrida", "presencial"}

# A 99Jobs escreve, no topo do resultado:
#     "Foram encontrados 3 oportunidades para o termo: “analista de dados”"
#     "Foram encontrados 0 oportunidades para o termo: “business intelligence”"
# O total esta ali, em texto renderizado. E ele que diz se a busca foi vazia.
_PADRAO_TOTAL = re.compile(r"foram encontrad\w*\s+(\d+)\s+oportunidade", re.IGNORECASE)


def classificar_timeout(corpo: str) -> str:
    """O que significa estourar o tempo esperando os cards.

    Devolve "vazio" (a busca nao tem resultado nenhum) ou "falha" (a pagina
    nao carregou, ou carregou dizendo que HA vaga e mesmo assim nao renderizou
    card nenhum).

    MEDIDO (2026-08-22): a checagem antiga era

        if "oportunidades para o termo" in texto_pagina:
            logger.info("0 resultados reais")

    e essa frase aparece SEMPRE -- medida ao vivo com 6 cards, com 4 e com 0.
    Ela nao discriminava nada: qualquer timeout virava "0 resultados reais",
    entao no dia em que a 99Jobs saisse do ar o log diria "busca vazia" e a
    fonte morreria em silencio, sem uma linha de aviso.

    Como o bug nasceu, pelo comentario que estava aqui: a versao original
    procurava o "0" junto de "oportunidades", mas os dois ficam em elementos
    HTML separados e nunca batiam como texto contiguo em page.content(). A
    correcao trocou content() por inner_text() -- e nessa troca o "0" caiu da
    comparacao. O conserto de um problema real levou junto a unica parte que
    discriminava.

    Agora le o NUMERO. Sem o numero na pagina, e falha: a pagina que carrega
    de verdade sempre traz essa frase.
    """
    achado = _PADRAO_TOTAL.search(corpo)
    if achado is None:
        return "falha"
    return "vazio" if achado.group(1) == "0" else "falha"


class Jobs99Scraper(BaseScraper):
    """Busca vagas no https://www.99jobs.com."""

    def __init__(self, termos_busca: list[str]):
        self.termos_busca = termos_busca

    def buscar_vagas(self) -> list[Job]:
        vagas: list[Job] = []
        for termo in self.termos_busca:
            vagas.extend(self._buscar_termo(termo))

        logger.info(f"[99Jobs] {len(vagas)} vaga(s) encontrada(s) no total")
        return vagas

    def _buscar_termo(self, termo: str) -> list[Job]:
        logger.info(f"[99Jobs] Buscando: {termo}")
        vagas: list[Job] = []
        # quote_plus em vez de .replace(" ", "+") manual: termo pode ter "&"
        # (ex: "BI & Analytics Analyst"), que sem escapar quebra a query
        # string no meio e corrompe a busca silenciosamente.
        termo_url = quote_plus(termo)
        url = f"https://www.99jobs.com/opportunities/filtered_search?search%5Bterm%5D={termo_url}"

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page(
                user_agent=(
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
                )
            )
            page.add_init_script(
                "Object.defineProperty(navigator, 'webdriver', { get: () => undefined })"
            )

            try:
                page.goto(url, timeout=60000)
                sem_resultados = False
                try:
                    page.wait_for_selector("a.opportunity-card", state="attached", timeout=25000)
                except Exception:
                    # inner_text() e nao content(): o total fica montado a
                    # partir de elementos separados, e so o texto RENDERIZADO
                    # traz a frase inteira e contigua. Ver classificar_timeout.
                    try:
                        corpo = page.inner_text("body")
                    except Exception:
                        corpo = ""

                    if classificar_timeout(corpo) == "vazio":
                        logger.info(f"[99Jobs] 0 resultados reais para '{termo}'.")
                        sem_resultados = True
                    else:
                        raise

                cards = [] if sem_resultados else page.query_selector_all("a.opportunity-card")
                if cards:
                    time.sleep(2)
                for card in cards:
                    try:
                        titulo_el = card.query_selector("h1")
                        if not titulo_el:
                            continue
                        titulo = titulo_el.inner_text().strip()

                        empresa_el = card.query_selector("h2")
                        empresa = empresa_el.inner_text().strip() if empresa_el else "Não informado"

                        cidade_el = card.query_selector("p")
                        cidade = " ".join(cidade_el.inner_text().split()) if cidade_el else "Não informado"

                        modalidade = ""
                        for span in card.query_selector_all("span"):
                            texto_span = span.inner_text().strip()
                            if texto_span.lower() in _MODALIDADES:
                                modalidade = texto_span
                                break

                        link = card.get_attribute("href")
                        if not link:
                            continue
                        if link.startswith("/"):
                            link = f"https://www.99jobs.com{link}"

                        publicado_em = extrair_data_publicacao(card.inner_text())

                        vagas.append(Job(
                            titulo=titulo,
                            empresa=empresa,
                            local=cidade,
                            link=link,
                            site="99Jobs",
                            publicado_em=publicado_em,
                            modalidade=modalidade,
                        ))
                    except Exception as e:
                        logger.warning(f"[99Jobs] Erro ao processar card: {e}")
                        continue

            except Exception as e:
                logger.error(f"[99Jobs] Erro ao buscar '{termo}': {e}")
            finally:
                browser.close()

        return vagas
