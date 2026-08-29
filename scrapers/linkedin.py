
import time
from urllib.parse import quote_plus

from playwright.sync_api import sync_playwright

from core.config import (
    LOCATIONS_LINKEDIN,
    LOCATIONS_LINKEDIN_CIDADES_PRESENCIAL,
    LOCATIONS_LINKEDIN_REMOTO_APENAS,
)
from core.job import Job, _e_remoto, _normalizar, extrair_data_do_card
from core.logger import get_logger
from scrapers.base import BaseScraper

logger = get_logger()

# Ver comentário equivalente em scrapers/gupy.py: só a 1a página nunca
# alcançava vaga de cidade menor (Recife, Natal, Maceió etc.) — confirmado
# ao vivo que vaga real dessas cidades existe mas fica fora do top 10
# nacional. O LinkedIn pagina via &start= (10 vagas por página).
MAX_PAGINAS = 3

# Segunda passada, só com o filtro nativo de remoto do LinkedIn (f_WT=2 —
# confirmado ao vivo que existe e funciona: filtra de verdade por vaga
# marcada como remota pelo próprio LinkedIn). Existe porque o card de busca
# NUNCA mostra "Remoto" no campo local, nem nessa passada filtrada — mesmo
# vaga 100% remota aparece com a cidade onde a empresa está registrada (ex:
# "Analista de Dados" remoto veio como "Curitiba, PR"). Sem essa passada e
# sem essa correção, só achávamos vaga remota por acidente (cidade
# literalmente escrita "Remoto" na listagem, o que quase nunca acontece).
# Menos páginas que a passada nacional pra não dobrar o custo do scraper —
# volume de vaga remota tende a ser menor que o total nacional por termo.
MAX_PAGINAS_REMOTO = 2

# Terceira passada, uma por cidade de LOCATIONS_LINKEDIN_CIDADES_PRESENCIAL
# (ver MEDIDO em config.py — a passada nacional não alcança cidade menor
# quando o termo é concorrido em SP/RJ/MG). 1 página só: essas cidades têm
# volume baixo por termo (mercado menor que capital), 10 resultados por
# cidade por termo já cobre a esmagadora maioria — e multiplicar por 11
# cidades já é o triplo de requisições da passada nacional sozinha, então
# manter enxuto aqui é o que evita esse custo virar desproporcional.
MAX_PAGINAS_CIDADE = 1

# Segunda chance quando a primeira pagina volta sem card nenhum.
#
# MEDIDO no ciclo do GitHub Actions de 29/08: 15 avisos de "Nenhum resultado
# retornado" num unico ciclo, concentrados nas buscas POR CIDADE
# ('looker' em Aracaju, 'qlik' em Caruaru, 'tableau' em Natal).
#
# A hipotese obvia era alarme falso -- cidade pequena + ferramenta de nicho
# nao tem vaga mesmo. MEDIDO ao vivo e DERRUBADO: essas mesmas buscas
# devolvem 10, 8, 10 cards quando rodadas isoladas. Nao e busca vazia; e
# falha, e vaga esta sendo perdida.
#
# POR QUE NAO DA PRA REPRODUZIR AQUI: 45 buscas seguidas da maquina da
# usuaria (IP residencial) deram ZERO vazias. O ciclo real roda no GitHub
# Actions, de IP de datacenter compartilhado, que o LinkedIn trata com muito
# mais rigor. O status 429 aparece nos dois ambientes, mas so no Actions o
# resultado chega vazio. Ou seja: a causa e inferida, nao provada, e a
# verificacao vem dos logs do Actions ao longo dos proximos dias.
#
# Por isso a correcao e SEGURA POR CONSTRUCAO em vez de precisa: uma
# requisicao a mais so quando a busca ja falhou (~15 por ciclo, nao 240).
# Se for rate-limit, recupera a vaga. Se nao for, a segunda tentativa volta
# vazia igual, o aviso dispara como antes e nada piorou.
#
# Efeito colateral bom: o aviso passa a sair so depois de DUAS tentativas,
# entao deixa de ser ruido e vira sinal.
PAUSA_SEGUNDA_TENTATIVA = 5


class LinkedInScraper(BaseScraper):
    """Busca vagas usando o endpoint público ("guest") de busca de vagas do
    LinkedIn, o mesmo usado para carregar mais resultados sem estar logado.

    Aviso importante: esse endpoint não é oficial/documentado pelo LinkedIn.
    Ele costuma funcionar sem login, mas o LinkedIn pode bloquear ou
    rate-limitar acessos automatizados repetidos a qualquer momento —
    principalmente vindos de IPs de nuvem/datacenter, como os do GitHub
    Actions. Não é possível garantir estabilidade a longo prazo.

    Roda duas passadas por termo (e por mercado, ver `locations` abaixo):
    uma nacional sem filtro de modalidade (pega vaga presencial/híbrida em
    cidade específica, cobre Recife/Natal/Maceió etc.) e outra com f_WT=2
    (só vaga que o próprio LinkedIn marca como remota). Essa segunda marca o
    campo `modalidade` como "Remoto" diretamente (o próprio LinkedIn já
    garante isso), porque o card nunca expõe isso sozinho em texto (ver
    MAX_PAGINAS_REMOTO acima) — `local` fica só com a cidade que o card
    mostra, mesmo pra vaga remota.

    `locations`: LinkedIn é a única fonte deste pipeline com alcance fora do
    Brasil — as outras são portais brasileiros. Antes ficava fixo em
    location=Brasil, então essa porta pra fora nunca era usada. Roda as DUAS
    passadas (nacional + remoto) só pra `locations` — o mercado "casa", onde
    vaga presencial/híbrida faz sentido.

    `locations_remoto_apenas`: mercado adicional onde só interessa vaga
    REMOTA — o usuário não mora lá, então presencial/híbrida de lá não
    serve, e rodar essa passada seria só custo sem retorno (o resultado
    nunca bateria em CIDADES, que é só cidade brasileira). Roda só a passada
    f_WT=2 pra esses países.

    `locations_cidades_presencial`: uma cidade por busca (só passada
    nacional, sem f_WT=2) pras cidades de CIDADES em config.py — existe
    porque a passada nacional acima (location="Brazil") não alcança essas
    cidades quando o termo é concorrido em SP/RJ/MG (MEDIDO ao vivo: 3
    páginas de "analista de dados" em Brasil inteiro vieram só de capital
    grande). Busca por cidade específica não depende de volume nacional —
    o próprio location= do LinkedIn já restringe à cidade.

    Cada país/cidade em qualquer uma das três listas multiplica o número de
    buscas — defaults (ver LOCATIONS_LINKEDIN/LOCATIONS_LINKEDIN_REMOTO_
    APENAS/LOCATIONS_LINKEDIN_CIDADES_PRESENCIAL em config.py) começam
    enxutos de propósito.
    """

    def __init__(
        self,
        termos_busca: list[str],
        locations: list[str] | None = None,
        locations_remoto_apenas: list[str] | None = None,
        locations_cidades_presencial: list[str] | None = None,
    ):
        self.termos_busca = termos_busca
        self.locations = locations if locations is not None else LOCATIONS_LINKEDIN
        self.locations_remoto_apenas = (
            locations_remoto_apenas
            if locations_remoto_apenas is not None
            else LOCATIONS_LINKEDIN_REMOTO_APENAS
        )
        self.locations_cidades_presencial = (
            locations_cidades_presencial
            if locations_cidades_presencial is not None
            else LOCATIONS_LINKEDIN_CIDADES_PRESENCIAL
        )

    def buscar_vagas(self) -> list[Job]:
        vagas: list[Job] = []
        for termo in self.termos_busca:
            for location in self.locations:
                vagas.extend(self._buscar_termo(termo, location, remoto=False))
                vagas.extend(self._buscar_termo(termo, location, remoto=True))
            for location in self.locations_remoto_apenas:
                vagas.extend(self._buscar_termo(termo, location, remoto=True))
            for location in self.locations_cidades_presencial:
                vagas.extend(self._buscar_termo(
                    termo, location, remoto=False,
                    max_paginas=MAX_PAGINAS_CIDADE, rotulo="cidade",
                ))

        total_mercados = (
            len(self.locations) + len(self.locations_remoto_apenas)
            + len(self.locations_cidades_presencial)
        )
        logger.info(
            f"[LinkedIn] {len(vagas)} vaga(s) encontrada(s) no total "
            f"({total_mercados} mercado(s): {', '.join(self.locations)} [completo] + "
            f"{', '.join(self.locations_remoto_apenas)} [remoto apenas] + "
            f"{len(self.locations_cidades_presencial)} cidade(s) [presencial/híbrido])"
        )
        return vagas

    def _buscar_termo(
        self,
        termo: str,
        location: str,
        remoto: bool,
        max_paginas: int | None = None,
        rotulo: str = "nacional",
    ) -> list[Job]:
        tag = f"{location}, remoto (f_WT=2)" if remoto else f"{location}, {rotulo}"
        logger.info(f"[LinkedIn] Buscando ({tag}): {termo}")
        vagas: list[Job] = []
        # quote_plus em vez de .replace(" ", "+") manual: termo (ou location)
        # pode ter "&" ou acento (ex: "BI & Analytics Analyst", "México"),
        # que sem escapar quebra a query string no meio e corrompe a busca
        # silenciosamente.
        termo_url = quote_plus(termo)
        location_url = quote_plus(location)
        if max_paginas is None:
            max_paginas = MAX_PAGINAS_REMOTO if remoto else MAX_PAGINAS

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page(
                user_agent=(
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
                )
            )

            try:
                for pagina in range(max_paginas):
                    start = pagina * 10
                    filtro_wt = "&f_WT=2" if remoto else ""
                    url = (
                        "https://www.linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings/search"
                        f"?keywords={termo_url}&location={location_url}{filtro_wt}&start={start}"
                    )
                    page.goto(url, timeout=60000)
                    time.sleep(2)

                    cards = page.query_selector_all("li")
                    if not cards and pagina == 0:
                        # Ver PAUSA_SEGUNDA_TENTATIVA no topo do arquivo: no
                        # Actions a busca volta vazia por rate-limit, nao por
                        # falta de vaga. Uma segunda tentativa custa uma
                        # requisição só nesse caso.
                        logger.info(
                            f"[LinkedIn] Sem resultado ({tag}) — tentando de novo em "
                            f"{PAUSA_SEGUNDA_TENTATIVA}s."
                        )
                        time.sleep(PAUSA_SEGUNDA_TENTATIVA)
                        page.goto(url, timeout=60000)
                        time.sleep(2)
                        cards = page.query_selector_all("li")
                        if cards:
                            logger.info(
                                f"[LinkedIn] Segunda tentativa recuperou {len(cards)} "
                                f"resultado(s) ({tag}) — a primeira foi bloqueio, não vaga zero."
                            )

                    if not cards:
                        if pagina == 0:
                            logger.warning(
                                f"[LinkedIn] Nenhum resultado retornado ({tag}) mesmo na "
                                "SEGUNDA tentativa — 0 vaga real, ou bloqueio persistente."
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

                            # f_WT=2 já garante que é vaga remota (o próprio
                            # LinkedIn classificou assim), mesmo quando o
                            # campo local só mostra a cidade da empresa —
                            # marca direto, sem precisar achar "remoto" no
                            # texto do local. f_WT=2 às vezes diverge do
                            # próprio anúncio (título diz "Hybrid") —
                            # Job.__post_init__ corrige isso pra QUALQUER
                            # fonte, não só aqui. Passada nacional: só marca
                            # se o próprio card organicamente disser isso no
                            # local.
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
                                site="LinkedIn",
                                publicado_em=publicado_em,
                                modalidade=modalidade,
                            ))
                        except Exception as e:
                            logger.warning(f"[LinkedIn] Erro ao processar card: {e}")
                            continue

            except Exception as e:
                logger.error(f"[LinkedIn] Erro ao buscar '{termo}' ({tag}): {e}")
            finally:
                browser.close()

        return vagas
