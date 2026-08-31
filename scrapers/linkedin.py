
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

# POR QUE UMA BUSCA VAZIA AQUI NAO PROVA NADA (medido tres vezes, 29-30/08).
#
# O sintoma: buscas voltam sem card nenhum, concentradas nas passadas POR
# CIDADE. Parecia obvio que era alarme falso -- cidade pequena com termo de
# nicho nao tem vaga mesmo. Nao e: rodadas isoladas devolvem 8 a 10 cards
# pras mesmas buscas. Entao ha vaga sendo perdida.
#
# HIPOTESE 1, DERRUBADA -- 'e bloqueio ao IP de datacenter do Actions':
#
#     ciclo no GitHub Actions (IP de datacenter) .... 13 buscas vazias
#     ciclo na maquina da usuaria (IP residencial) .. 22 buscas vazias
#
#   Deu MAIS vazio no IP residencial. A conclusao anterior estava errada.
#
# HIPOTESE 2, DERRUBADA -- 'e a fila: o LinkedIn corta depois de N
# requisicoes seguidas no mesmo ciclo':
#
#   Os 22 pares vazios do ciclo local foram repetidos ISOLADOS, do mesmo
#   IP, 20 minutos depois. Se fosse a fila, todos voltariam com vaga.
#   Voltaram 10 de 22:
#
#     mis analyst            Joao Pessoa      0 no ciclo -> 10 isolado
#     mis analyst            Maceio           0 no ciclo -> 10 isolado
#     mis analyst            Fortaleza        0 no ciclo -> 10 isolado
#     reporting analyst      Caruaru          0 no ciclo -> 10 isolado
#     data specialist        Caruaru          0 no ciclo -> 10 isolado
#     data quality analyst   Maceio           0 no ciclo ->  9 isolado
#     data intelligence an.  Caruaru          0 no ciclo ->  6 isolado
#     data quality analyst   Campina Grande   0 no ciclo ->  2 isolado
#     mis analyst            Brazil (remoto)  0 no ciclo -> 10 isolado
#     mis analyst            Chile  (remoto)  0 no ciclo -> 10 isolado
#
#   e 12 continuaram vazios. E 'power bi'/Maceio fez o caminho inverso:
#   10 cards numa medicao isolada de 29/08, 0 no ciclo de 30/08, 0 de novo
#   na repeticao isolada de 30/08.
#
# O QUE SOBRA, E O QUE OS NUMEROS SUSTENTAM: mesmo IP, mesmo par, resposta
# diferente em horas diferentes. Nem o IP nem a fila explicam. O formato da
# resposta diz o resto -- quase todo resultado e 0 ou 10, pagina cheia ou
# nada. Inventario real de cidade pequena com termo de nicho apareceria
# como 1, 2, 3, 4. Bimodal assim e decisao de servidor, nao contagem de
# vaga: o endpoint guest do LinkedIn as vezes serve a pagina e as vezes
# devolve vazio, e nao da pra prever qual.
#
# JA TENTADO E MEDIDO COMO INUTIL -- NAO REFACA: repetir a busca dentro do
# mesmo ciclo. Com pausa de 5s, depois com 10s e 30s. Resultado: 13 buscas
# vazias, 13 repeticoes disparadas, 0 recuperacoes, e +9 minutos por ciclo.
# A escala em que a resposta muda e de DEZENAS DE MINUTOS, nao de segundos
# -- por isso esperar dentro do ciclo nao alcanca, e por isso o rodizio de
# termos ja funciona como nova tentativa de graca: o mesmo par volta em
# ciclos seguintes, e ai costuma vir cheio.
#
# CONCLUSAO PRATICA: busca vazia aqui e SINAL FRACO. Nao da pra distinguir
# 'nao tem vaga' de 'o LinkedIn nao quis responder agora' -- por isso o
# aviso abaixo nao afirma nem uma coisa nem outra. Ele serve pra ver
# TENDENCIA no log (se um dia forem 200 e nao 20, mudou alguma coisa), nao
# pra agir em cima de uma ocorrencia.


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
        # Cada item: (termo, location, remoto, max_paginas, rotulo, momento).
        # Preenchido por _buscar_termo quando a primeira pagina volta sem card.
        self._vazias = []
        self._registrar_vazias = True
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

        vagas.extend(self._segunda_passada(vagas))

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

    def _segunda_passada(self, vagas_da_primeira: list[Job]) -> list[Job]:
        """Repete, no FIM do ciclo, so as buscas que voltaram sem card nenhum.

        POR QUE NO FIM E NAO NA HORA (isso ja foi medido e errado duas vezes):
        repetir depois de 5s, 10s ou 30s deu 0 recuperacao em 13 tentativas. A
        escala em que a resposta do LinkedIn muda e de DEZENAS DE MINUTOS. O
        ciclo inteiro dura ~25 min, entao o fim do ciclo e o momento mais tarde
        que da pra alcancar sem esperar de graca.

        MEDIDO (30-31/08), que e o que justifica o custo:
          · os 22 pares vazios de um ciclo, repetidos isolados ~20 min depois:
            10 voltaram com vaga (8 a 10 cards cada).
          · esperar o rodizio trazer o termo de volta (~9h) recuperou 12 de 22,
            MAS deixou 5 pares com vaga comprovada vazios por DOIS ciclos
            seguidos. Ou seja: o rodizio sozinho nao basta.

        SEGURA POR CONSTRUCAO: so repete busca que ja voltou zero, entao no pior
        caso volta zero de novo e nada piorou. Custo medido: ~25 buscas extras
        em ~375, uns 2 min num ciclo de 25.

        SE MEDE SOZINHA: o log diz quantos pares voltaram, quantas vagas vieram
        e quantas delas eram INEDITAS neste ciclo (id que a primeira passada nao
        tinha trazido por nenhum outro termo ou cidade). Se as ineditas forem ~0
        por alguns ciclos, esta passada nao se paga e deve ser REMOVIDA -- esse
        e o criterio, escrito antes de ver o resultado.
        """
        if not self._vazias:
            return []

        self._registrar_vazias = False
        pendentes = self._vazias
        logger.info(
            f"[LinkedIn] Segunda passada: repetindo {len(pendentes)} busca(s) "
            "que voltaram vazias na primeira."
        )

        recuperadas: list[Job] = []
        pares_que_voltaram = 0
        for termo, location, remoto, max_paginas, rotulo, momento in pendentes:
            minutos = (time.monotonic() - momento) / 60
            achadas = self._buscar_termo(
                termo, location, remoto, max_paginas=max_paginas, rotulo=rotulo
            )
            if achadas:
                pares_que_voltaram += 1
                recuperadas.extend(achadas)
                logger.info(
                    f"[LinkedIn] Segunda passada recuperou {len(achadas)} vaga(s) "
                    f"em '{termo}' ({location}) apos {minutos:.0f} min — a "
                    "primeira foi resposta instável, não vaga zero."
                )

        ja_vistos = {v.id for v in vagas_da_primeira}
        ineditas = [v for v in recuperadas if v.id not in ja_vistos]
        logger.info(
            f"[LinkedIn] Segunda passada: {pares_que_voltaram}/{len(pendentes)} "
            f"par(es) voltaram com vaga, {len(recuperadas)} vaga(s) bruta(s), "
            f"{len(ineditas)} inédita(s) neste ciclo."
        )
        return ineditas
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

                    if not cards:
                        if pagina == 0:
                            # Guarda pra segunda passada no fim do ciclo. O
                            # getattr protege quem chama _buscar_termo direto
                            # (scripts de medicao), e o registrar=False evita
                            # que a propria segunda passada se re-agende.
                            if getattr(self, "_registrar_vazias", False):
                                self._vazias.append((
                                    termo, location, remoto, max_paginas,
                                    rotulo, time.monotonic(),
                                ))
                            logger.warning(
                                f"[LinkedIn] Nenhum resultado retornado ({tag}) — pode "
                                "ser ausência de vaga ou resposta instável do "
                                "LinkedIn; medido, não dá pra distinguir os dois "
                                "(ver o MEDIDO no topo deste arquivo). O rodízio "
                                "de termos costuma recuperar em ciclos seguintes."
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
