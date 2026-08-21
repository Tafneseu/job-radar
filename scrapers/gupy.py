
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

# Frase que a Gupy renderiza quando a página carregou mas não tem vaga.
TEXTO_SEM_RESULTADO = "Nenhum resultado foi encontrado"


def classificar_timeout(corpo: str, pagina: int) -> str:
    """O que significa estourar o tempo esperando os cards de uma página.

    Devolve "vazio" (busca sem resultado nenhum), "fim" (a paginação acabou,
    a página pedida não existe) ou "falha" (a página não carregou de verdade).

    MEDIDO (2026-08-21): antes desta separação, QUALQUER timeout da página 2
    em diante virava aviso de vaga perdida — e na maioria dos termos do ciclo
    isso era falso. A página de busca da Gupy cabe 12 cards; "data analyst"
    tem 8 resultados no total, então a página 1 já era a última. Só que
    cards_por_pagina é aprendido OLHANDO A PÁGINA 1: ele aprendeu "cheia = 8",
    achou que estava cheia e pediu a página 2, que não existe.

    Medido ao vivo, mesmo dia, mesmo endpoint:
        'analista de dados'  pág.1 -> 12 cards   pág.2 -> 12 cards
        'data analyst'       pág.1 ->  8 cards   pág.2 ->  0 cards

    A página inexistente CARREGA normalmente e diz, em texto, "Nenhum
    resultado foi encontrado" (junto com "8 resultados", o total real). Essa
    frase já era procurada pelo scraper, mas só no ramo da página 1 — da 2 em
    diante ele nem olhava o corpo: assumia falha e avisava perda que nunca
    houve. Alarme falso constante esconde o aviso verdadeiro, que é o motivo
    de o aviso existir.

    O aviso de verdade continua: página que falhou mesmo não traz a frase.

    MELHORIA POSSÍVEL, não implementada: a página traz "N resultados" já na
    primeira, o que permitiria nem pedir a seguinte num termo curto. Fica pra
    depois — corrigir o alarme não depende disso, e mudança a mais é risco a
    mais.
    """
    if TEXTO_SEM_RESULTADO in corpo:
        return "vazio" if pagina == 1 else "fim"
    return "falha"


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
                        try:
                            corpo = page.inner_text("body")
                        except Exception:
                            corpo = ""

                        situacao = classificar_timeout(corpo, pagina)
                        if situacao == "vazio":
                            logger.info(f"[Gupy] 0 resultados reais para '{termo}'.")
                            sem_resultados = True
                        elif situacao == "fim":
                            logger.info(
                                f"[Gupy] Fim dos resultados de '{termo}': a página "
                                f"{pagina} não existe (a anterior já era a última)."
                            )
                            break
                        elif pagina > 1:
                            logger.warning(
                                f"[Gupy] Timeout na página {pagina} de '{termo}' com a "
                                "página anterior CHEIA — havia mais resultado e ele não "
                                "carregou. Vaga pode ter ficado de fora."
                            )
                            break
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
