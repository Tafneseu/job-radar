
import re
import time

from playwright.sync_api import sync_playwright

from core.job import Job, extrair_data_publicacao
from core.logger import get_logger
from scrapers.base import BaseScraper

logger = get_logger()

_MODALIDADES = {"remoto", "híbrido", "hibrido", "presencial"}

# MEDIDO ao vivo (Claude in Chrome): scraper só puxava ?page=1, sempre — só
# 10 vagas por termo, não importa quantas existam de verdade. "analista de
# dados" sozinho tem 202 vaga(s) encontrada(s), 21 páginas de 10 (visto no
# rodapé de paginação do site). ?page=N muda o resultado de verdade
# (conferido página 1 vs. página 2 — títulos diferentes, sem repetição).
# Mesmo raciocínio de gupy.py/indeed.py: 3 páginas por termo, equilíbrio
# entre cobertura e custo por ciclo (aqui cobre até 30 de 202 pro termo mais
# genérico, 3x o que tinha antes).
MAX_PAGINAS = 3


def _slug(termo: str) -> str:
    return termo.strip().lower().replace(" ", "-")


# Texto que a Sólides renderiza quando a página carregou mas não tem vaga
# ("Ops! / Vaga não encontrada").
TEXTO_SEM_RESULTADO = "Vaga não encontrada"

# MEDIDO (2026-08-21): a checagem antiga era `"0 vaga(s) encontrada" in corpo`,
# substring crua — e "200 vaga(s) encontrada" CONTÉM "0 vaga(s) encontrada".
# Qualquer total terminado em zero (10, 30, 200...) casava como busca vazia.
# Efeito: página 1 que falhasse de verdade num desses termos era registrada
# como "0 resultados reais" e o scraper parava em silêncio — falha real
# escondida atrás de mensagem de busca vazia. A borda de palavra resolve.
_PADRAO_ZERO_VAGAS = re.compile(r"\b0 vaga\(s\) encontrada")


def classificar_timeout(corpo: str, pagina: int) -> str:
    """O que significa estourar o tempo esperando os cards de uma página.

    Devolve "vazio" (busca sem resultado nenhum), "fim" (a paginação acabou,
    a página pedida não existe) ou "falha" (a página não carregou de verdade).

    MEDIDO (2026-08-21) ao vivo, com o MESMO user_agent e init_script do
    scraper (sem eles a Sólides não renderiza nada e a medição não vale):

        'analista de dados' pág.1 -> 10 cards | total "200 vaga(s) encontrada"
        'data analyst'      pág.1 ->  7 cards | total   "7 vaga(s) encontrada"
        'data analyst'      pág.2 ->  0 cards | "Ops! / Vaga não encontrada"

    Ou seja: mesma causa da Gupy. A página cabe 10; "data analyst" tem 7 no
    total, então a página 1 já era a última. Mas cards_por_pagina é aprendido
    OLHANDO A PÁGINA 1 — aprendeu "cheia = 7", achou que estava cheia e pediu
    a página 2, que não existe. O timeout virava aviso de vaga perdida.

    O comentário anterior deste arquivo afirmava, com medição, que aqui o
    texto do site não ajudava. Estava certo sobre o que mediu: a página além
    da última NÃO mostra "0 vaga(s) encontrada" (mostra o total real, 7). Mas
    mostra "Ops! / Vaga não encontrada", que aquela medição não procurou.

    MELHORIA POSSÍVEL, não implementada: o total declarado ("7 vaga(s)
    encontrada") está na página 1, junto do tamanho de página. Comparar os
    dois evitaria PEDIR a página inexistente — hoje ela custa os 25s do
    timeout, por termo curto, todo ciclo. Fica pra depois: corrigir o alarme
    não depende disso, e mudança a mais é risco a mais.
    """
    if TEXTO_SEM_RESULTADO in corpo or _PADRAO_ZERO_VAGAS.search(corpo):
        return "vazio" if pagina == 1 else "fim"
    return "falha"


class SolidesScraper(BaseScraper):
    """Busca vagas no https://vagas.solides.com.br."""

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
        # Mesmo mecanismo do gupy.py: o tamanho de página é descoberto pela
        # primeira página, e página menos cheia que ela é a última.
        cards_por_pagina = None

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
                for pagina in range(1, MAX_PAGINAS + 1):
                    url = f"https://vagas.solides.com.br/vagas/todos/{_slug(termo)}?page={pagina}"
                    page.goto(url, timeout=60000)
                    sem_resultados = False
                    try:
                        page.wait_for_selector("li:has(h2 a)", state="attached", timeout=25000)
                    except Exception:
                        try:
                            corpo = page.inner_text("body")
                        except Exception:
                            corpo = ""

                        situacao = classificar_timeout(corpo, pagina)
                        if situacao == "vazio":
                            logger.info(f"[Solides] 0 resultados reais para '{termo}'.")
                            sem_resultados = True
                        elif situacao == "fim":
                            logger.info(
                                f"[Solides] Fim dos resultados de '{termo}': a página "
                                f"{pagina} não existe (a anterior já era a última)."
                            )
                            break
                        elif pagina > 1:
                            # Timeout de verdade: a página não carregou E não
                            # disse estar vazia. Sem esta distinção, um
                            # timeout real viraria break silencioso idêntico
                            # ao fim natural da paginação, e a vaga dessa
                            # página se perderia sem rastro no log.
                            logger.warning(
                                f"[Solides] Timeout na página {pagina} de '{termo}' com a "
                                "página anterior CHEIA — havia mais resultado e ele não "
                                "carregou. Vaga pode ter ficado de fora."
                            )
                            break
                        else:
                            raise
                    if not sem_resultados:
                        time.sleep(2)

                    cards = [] if sem_resultados else page.query_selector_all("li:has(h2 a)")
                    if not cards:
                        break

                    if cards_por_pagina is None:
                        cards_por_pagina = len(cards)

                    for card in cards:
                        try:
                            titulo_el = card.query_selector("h2 a")
                            if not titulo_el:
                                continue
                            titulo = titulo_el.inner_text().strip()

                            link = titulo_el.get_attribute("href")
                            if not link:
                                continue
                            if link.startswith("/"):
                                link = f"https://vagas.solides.com.br{link}"

                            paragrafos = card.query_selector_all("p")
                            empresa = paragrafos[0].inner_text().strip() if len(paragrafos) > 0 else "Não informado"
                            cidade = paragrafos[1].inner_text().strip() if len(paragrafos) > 1 else "Não informado"

                            modalidade = ""
                            for div in card.query_selector_all("div"):
                                texto_div = div.inner_text().strip()
                                if texto_div.lower() in _MODALIDADES:
                                    modalidade = texto_div
                                    break

                            publicado_em = extrair_data_publicacao(card.inner_text())

                            vagas.append(Job(
                                titulo=titulo,
                                empresa=empresa or "Não informado",
                                local=cidade,
                                link=link,
                                site="Solides",
                                publicado_em=publicado_em,
                                modalidade=modalidade,
                            ))
                        except Exception as e:
                            logger.warning(f"[Solides] Erro ao processar card: {e}")
                            continue

                    if sem_resultados:
                        break

                    if len(cards) < cards_por_pagina:
                        logger.info(
                            f"[Solides] Fim dos resultados de '{termo}' na página {pagina} "
                            f"({len(cards)} de {cards_por_pagina} por página)."
                        )
                        break

            except Exception as e:
                logger.error(f"[Solides] Erro ao buscar '{termo}': {e}")
            finally:
                browser.close()

        return vagas
