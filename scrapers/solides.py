
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
                        # MEDIDO ao vivo: página além da última NÃO mostra "0
                        # vaga(s) encontrada" (o texto continua com o total
                        # real, ex: "202 vaga(s) encontrada") — só timeout de
                        # verdade (site lento/bloqueio) e "passou da última
                        # página" se parecem no comportamento (nenhum card
                        # aparece), mas só a busca genuinamente vazia na
                        # PÁGINA 1 tem o texto "0 vaga(s) encontrada" pra
                        # diferenciar — mesma lógica de gupy.py.
                        if pagina == 1 and "0 vaga(s) encontrada" in page.inner_text("body"):
                            logger.info(f"[Solides] 0 resultados reais para '{termo}'.")
                            sem_resultados = True
                        elif pagina > 1:
                            # MEDIDO: este aviso dizia "não dá pra diferenciar
                            # aqui" e disparava a cada fim de paginação, virando
                            # ruído. Agora dá pra diferenciar: a checagem de
                            # página incompleta (abaixo) encerra o fim natural
                            # antes de pedir a próxima. Se mesmo assim der
                            # timeout, a página anterior estava CHEIA — havia
                            # mais resultado de verdade.
                            #
                            # Aqui o texto do site não ajuda: MEDIDO ao vivo que
                            # página além da última continua mostrando o total
                            # real ("202 vaga(s) encontrada"), não "0 vaga(s)".
                            # Por isso a contagem de cards, e não o texto.
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
