
import time
from urllib.parse import quote_plus

from playwright.sync_api import sync_playwright

from core.job import Job, _e_remoto, _normalizar, extrair_data_do_card
from core.logger import get_logger
from scrapers.base import BaseScraper

logger = get_logger()

# Começa sem paginar (MAX_PAGINAS=1) — isso aqui é um pipeline novo, ainda
# não validado em produção. Termos x países já multiplica bastante o número
# de buscas; paginar em cima disso só depois de confirmar que vale o tempo
# de execução extra.
MAX_PAGINAS = 1


class LinkedInIntlScraper(BaseScraper):
    """Busca vaga remota internacional (fora do Brasil) que aceite/peça
    português ou espanhol, usando o mesmo endpoint público ("guest") do
    scrapers/linkedin.py — a diferença é que aqui o parâmetro location varia
    por país (LOCATIONS_INTL), em vez de ficar fixo em "Brasil".

    Mesmos avisos do LinkedIn normal: endpoint não documentado, sujeito a
    bloqueio/rate-limit sem aviso prévio.

    Roda duas passadas por termo/país — mesma estrutura de scrapers/linkedin.py
    (ver docstring de LinkedInScraper): uma nacional sem filtro de modalidade
    (pega vaga presencial/híbrida, alimenta o eixo Ibérico quando ligado) e
    outra com f_WT=2 (só vaga que o próprio LinkedIn marca como remota).
    Antes só existia a nacional, e modalidade só era preenchida quando o
    texto de local dizia "remote" organicamente — o que quase nunca
    acontece (o card mostra a cidade onde a empresa está registrada, não a
    modalidade), confirmado com o mesmo raciocínio que já motivou a segunda
    passada no scraper nacional. Resultado medido: praticamente toda vaga
    remota real vinha com modalidade vazia e era barrada por CIDADES_INTL
    (que só aceita "Remote"/"Remoto"), deixando a fonte principal do
    pipeline internacional muda pra REGRAS_INTL.
    """

    def __init__(self, termos_busca: list[str], locations: list[str]):
        self.termos_busca = termos_busca
        self.locations = locations

    def buscar_vagas(self) -> list[Job]:
        vagas: list[Job] = []
        for termo in self.termos_busca:
            for location in self.locations:
                vagas.extend(self._buscar_termo(termo, location, remoto=False))
                vagas.extend(self._buscar_termo(termo, location, remoto=True))

        logger.info(f"[LinkedIn Intl] {len(vagas)} vaga(s) encontrada(s) no total")
        return vagas

    def _buscar_termo(self, termo: str, location: str, remoto: bool) -> list[Job]:
        tag = f"{location}, remoto (f_WT=2)" if remoto else f"{location}, nacional"
        logger.info(f"[LinkedIn Intl] Buscando ({tag}): {termo}")
        vagas: list[Job] = []
        termo_url = quote_plus(termo)
        location_url = quote_plus(location)

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page(
                user_agent=(
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
                )
            )

            try:
                for pagina in range(MAX_PAGINAS):
                    start = pagina * 10
                    filtro_wt = "&f_WT=2" if remoto else ""
                    url = (
                        "https://www.linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings/search"
                        f"?keywords={termo_url}&location={location_url}{filtro_wt}&start={start}"
                    )
                    page.goto(url, timeout=60000)
                    time.sleep(2)

                    cards = page.query_selector_all("li")
                    if not cards:
                        if pagina == 0:
                            logger.warning(
                                f"[LinkedIn Intl] Nenhum resultado ({tag}) pra '{termo}' — "
                                "provável bloqueio/rate-limit ou 0 vaga real."
                            )
                        break

                    for card in cards:
                        try:
                            titulo_el = card.query_selector("h3.base-search-card__title")
                            if not titulo_el:
                                continue
                            titulo = titulo_el.inner_text().strip()

                            empresa_el = card.query_selector("h4.base-search-card__subtitle a")
                            empresa = empresa_el.inner_text().strip() if empresa_el else "Não informado"

                            local_el = card.query_selector(".job-search-card__location")
                            local = local_el.inner_text().strip() if local_el else "Não informado"

                            # Passada com f_WT=2: o próprio LinkedIn já filtrou
                            # por remoto, então força modalidade="Remoto" sem
                            # depender do texto de local (que normalmente só
                            # mostra a cidade onde a empresa está registrada,
                            # nunca "Remote"/"Remoto" literalmente). Filtro
                            # nativo às vezes diverge do próprio anúncio (ex:
                            # título diz "Hybrid") — Job.__post_init__ corrige
                            # isso pra QUALQUER fonte, não só aqui. Passada
                            # nacional: sem filtro nativo, só resta a detecção
                            # orgânica no texto (raramente bate, mas serve como
                            # fallback pro eixo Ibérico quando religado).
                            if remoto:
                                modalidade = "Remoto"
                            else:
                                modalidade = "Remoto" if _e_remoto(_normalizar(local)) else ""

                            link_el = card.query_selector("a.base-card__full-link")
                            link = link_el.get_attribute("href") if link_el else None
                            if not link:
                                continue
                            link = link.split("?")[0]

                            # A tag <time> do card traz a data ABSOLUTA
                            # (datetime="2026-03-26"). O texto traz "4
                            # months ago", em ingles, que os padroes em
                            # portugues nunca casavam — ver
                            # extrair_data_do_card em core/job.py.
                            el_data = card.query_selector("time")
                            publicado_em = extrair_data_do_card(
                                el_data.get_attribute("datetime") if el_data else None,
                                card.inner_text(),
                            )

                            vagas.append(Job(
                                titulo=titulo,
                                empresa=empresa,
                                local=local,
                                link=link,
                                site="LinkedIn Internacional",
                                publicado_em=publicado_em,
                                modalidade=modalidade,
                            ))
                        except Exception as e:
                            logger.warning(f"[LinkedIn Intl] Erro ao processar card: {e}")
                            continue

            except Exception as e:
                logger.error(f"[LinkedIn Intl] Erro ao buscar '{termo}' em {location}: {e}")
            finally:
                browser.close()

        return vagas
