
import time

from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError

from core.job import Job, extrair_data_publicacao
from core.logger import get_logger
from scrapers.base import BaseScraper

logger = get_logger()

_MODALIDADES = {"remoto", "híbrido", "hibrido", "presencial"}

# Busca só a 1a página (10-12 vagas) nunca alcançava vaga de cidade menor
# (Recife, Natal, Maceió etc.) — um termo genérico tipo "analista de dados"
# tem centenas de resultados nacionais, e São Paulo/grandes polos sempre
# dominam a 1a página. Paginando, aumenta a chance real de achar vaga das
# cidades monitoradas, ao custo de mais requests por termo.
MAX_PAGINAS = 3


class GupyScraper(BaseScraper):
    """Busca vagas no portal público da Gupy (https://portal.gupy.io)."""

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
        # Quantos cards a primeira página trouxe — é o tamanho de página do
        # site, descoberto em vez de chutado. Ver o "fim natural" no fim do
        # laço: página que vem menos cheia que a primeira é a última, e a
        # seguinte nem chega a ser pedida.
        cards_por_pagina = None
        base_url = f"https://portal.gupy.io/job-search/term={termo.replace(' ', '%20')}"

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page()

            try:
                for pagina in range(1, MAX_PAGINAS + 1):
                    url = base_url if pagina == 1 else f"{base_url}?page={pagina}"
                    page.goto(url, timeout=60000)
                    sem_resultados = False
                    try:
                        page.wait_for_selector("a:has(h3)", timeout=15000)
                    except PlaywrightTimeoutError:
                        if pagina > 1:
                            # Timeout de verdade (site lento, bloqueio) —
                            # DIFERENTE de "acabaram as vagas", que é
                            # sinalizado abaixo (página carrega normal mas
                            # devolve 0 cards). Sem essa distinção, um
                            # timeout na página 2/3 virava break silencioso
                            # idêntico ao fim natural da paginação, e a vaga
                            # que estaria nessa página se perdia sem deixar
                            # rastro nenhum no log.
                            logger.warning(
                                f"[Gupy] Timeout na página {pagina} de '{termo}' com a "
                                "página anterior CHEIA — havia mais resultado e ele não "
                                "carregou. Vaga pode ter ficado de fora."
                            )
                            break
                        if "Nenhum resultado foi encontrado" in page.inner_text("body"):
                            logger.info(f"[Gupy] 0 resultados reais para '{termo}'.")
                            sem_resultados = True
                        else:
                            raise
                    time.sleep(2 if not sem_resultados else 0)  # dá tempo do React terminar de renderizar

                    cards = [] if sem_resultados else page.query_selector_all("a:has(h3)")
                    if not cards:
                        break

                    if cards_por_pagina is None:
                        cards_por_pagina = len(cards)

                    for card in cards:
                        try:
                            titulo_el = card.query_selector("h3")
                            if not titulo_el:
                                continue
                            titulo = titulo_el.inner_text().strip()

                            empresa_el = card.query_selector("p")
                            empresa = empresa_el.inner_text().strip() if empresa_el else "Não informado"

                            local_el = card.query_selector('[data-testid="job-location"]')
                            cidade = local_el.inner_text().strip() if local_el else "Não informado"

                            # O modelo de trabalho (Remoto/Híbrido/Presencial) fica num span
                            # solto no card. Antes dependia do atributo alt do ícone ao lado
                            # (alt="Ícone de Modelo de Trabalho"), mas a Gupy parou de renderizar
                            # esse atributo — agora procura direto pelo texto do span, mesma
                            # técnica usada nos outros scrapers.
                            modelo = ""
                            for span in card.query_selector_all("span"):
                                texto_span = span.inner_text().strip()
                                if texto_span.lower() in _MODALIDADES:
                                    modelo = texto_span
                                    break

                            link = card.get_attribute("href")
                            if not link:
                                continue

                            publicado_em = extrair_data_publicacao(card.inner_text())

                            vagas.append(Job(
                                titulo=titulo,
                                empresa=empresa,
                                local=cidade,
                                link=link,
                                site="Gupy",
                                publicado_em=publicado_em,
                                modalidade=modelo,
                            ))
                        except Exception as e:
                            logger.warning(f"[Gupy] Erro ao processar card: {e}")
                            continue

                    if sem_resultados:
                        break

                    # MEDIDO: o log ficou cheio de "Timeout na página 2 — pode
                    # ter ficado vaga de fora", quinze por ciclo, e quase todos
                    # eram FIM DOS RESULTADOS, não falha. O padrão denunciava:
                    # termo de muito resultado (sql, data analyst) chegava à
                    # página 3; termo de pouco resultado (qlik, looker) parava
                    # na 2. O scraper só sabia distinguir "vazio" de "falhou"
                    # na página 1, onde procura o texto de nenhum resultado.
                    #
                    # Custo real disso: passei um bom tempo investigando uma
                    # perda de vaga que não existia, porque o log afirmava
                    # perda quinze vezes por ciclo. Alarme falso constante
                    # esconde o aviso verdadeiro.
                    #
                    # Página que veio menos cheia que a primeira é a última —
                    # não existe página seguinte pra pedir. Assim o timeout que
                    # sobrar passa a ser informativo de verdade: se ele
                    # acontecer, a página anterior estava CHEIA, então havia
                    # mesmo algo a mais e a vaga pode ter se perdido.
                    if len(cards) < cards_por_pagina:
                        logger.info(
                            f"[Gupy] Fim dos resultados de '{termo}' na página {pagina} "
                            f"({len(cards)} de {cards_por_pagina} por página)."
                        )
                        break

            except Exception as e:
                logger.error(f"[Gupy] Erro ao buscar '{termo}': {e}")
            finally:
                browser.close()

        return vagas
