"""Avalia um site de vagas ANTES de virar scraper.

Por que existe: adicionar fonte custa tempo de ciclo em toda execucao, pra
sempre. A Senior entrou depois de um teste ao vivo e mediu rendimento de
0,3% (398 vagas brutas, 1 aprovada) -- achar o endpoint foi a parte facil, e
a que decidiu foi a medicao. Este script antecipa essa medicao: em vez de
garimpar no DevTools e escrever um scraper pra descobrir que nao presta, roda
um comando e ve o que o site oferece.

Procura quatro coisas, da mais estavel pra mais fragil:

  1. robots.txt        -- o que o site declara que permite
  2. JSON-LD           -- schema.org/JobPosting embutido na pagina. E a
                          fonte mais estavel que existe: a empresa publica
                          isso DE PROPOSITO pro Google Jobs indexar, entao
                          nao muda quando o layout muda.
  3. feed RSS/Atom     -- alguns ATSs publicam
  4. endpoint XHR      -- a API que o proprio site chama pra montar a
                          pagina. Foi assim que a Senior entrou.

Uso -- a URL precisa ser a de uma BUSCA real, nao um exemplo inventado:
    python avaliar_fonte.py "https://portal.gupy.io/job-search/term=analista%20de%20dados"

Nao raspa nem guarda nada: carrega a pagina uma vez e relata.
"""

import json
import re
import sys
from urllib.parse import urljoin, urlsplit

import requests

TIMEOUT = 20
UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)

# Pedaco de URL que sugere endpoint de busca de vaga. Serve so pra ORDENAR
# os candidatos no relatorio -- nada e descartado por nao bater aqui.
_PISTAS_DE_VAGA = (
    "job", "vaga", "vacanc", "career", "carreira", "oportunidad",
    "opportunit", "search", "busca", "position", "empreg",
)


# --------------------------------------------------------------- robots.txt

def regras_do_robots(texto: str, agente: str = "*") -> list[tuple[str, str]]:
    """Extrai as regras Allow/Disallow que valem pro agente informado.

    Devolve [(permissao, caminho)], na ordem em que aparecem. Le o grupo do
    agente exato e, se nao houver, o grupo "*" -- que e o que se aplica a um
    robo sem identidade propria, o nosso caso.

    Ignora Crawl-delay e Sitemap de proposito: os dois sao lidos a parte.
    """
    grupos: dict[str, list[tuple[str, str]]] = {}
    atual: list[str] = []
    for linha in texto.splitlines():
        linha = linha.split("#")[0].strip()
        if not linha or ":" not in linha:
            continue
        chave, _, valor = linha.partition(":")
        chave = chave.strip().lower()
        valor = valor.strip()
        if chave == "user-agent":
            atual = [valor.lower()]
            grupos.setdefault(valor.lower(), [])
        elif chave in ("allow", "disallow") and atual:
            for ag in atual:
                grupos.setdefault(ag, []).append((chave, valor))
    return grupos.get(agente.lower()) or grupos.get("*") or []


def caminho_permitido(regras: list[tuple[str, str]], caminho: str) -> bool:
    """O caminho e permitido pelas regras?

    Usa a regra de maior prefixo casado, que e como robots.txt e definido:
    entre "Disallow: /" e "Allow: /vagas", a segunda vence pra /vagas/x.
    Empate entre Allow e Disallow do mesmo tamanho: Allow vence.

    Sem regra nenhuma casando, e permitido -- robots.txt e uma lista de
    proibicoes, nao de autorizacoes.
    """
    melhor: tuple[int, str] | None = None
    for permissao, prefixo in regras:
        if not prefixo:
            continue
        if caminho.startswith(prefixo):
            peso = len(prefixo)
            if melhor is None or peso > melhor[0] or (peso == melhor[0] and permissao == "allow"):
                melhor = (peso, permissao)
    return melhor is None or melhor[1] == "allow"


# ------------------------------------------------------------------ JSON-LD

def _achatar(no):
    """JSON-LD vem em formatos diferentes conforme quem gerou: objeto solto,
    lista, ou embrulhado em @graph. Achata os tres num so."""
    if isinstance(no, list):
        for item in no:
            yield from _achatar(item)
    elif isinstance(no, dict):
        yield no
        if "@graph" in no:
            yield from _achatar(no["@graph"])


def extrair_jobpostings(html: str) -> list[dict]:
    """Devolve os blocos schema.org/JobPosting embutidos na pagina.

    E a fonte mais estavel possivel: a empresa publica isso pro Google Jobs
    indexar, entao ela tem interesse em manter, e o formato nao depende do
    layout visual. Achar isso aqui vale mais que achar endpoint.
    """
    achados: list[dict] = []
    for bloco in re.findall(
        r'<script[^>]*type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
        html, re.DOTALL | re.IGNORECASE,
    ):
        try:
            dados = json.loads(bloco.strip())
        except (json.JSONDecodeError, ValueError):
            continue
        for no in _achatar(dados):
            tipo = no.get("@type", "")
            tipos = tipo if isinstance(tipo, list) else [tipo]
            if any(str(x).lower() == "jobposting" for x in tipos):
                achados.append(no)
    return achados


def resumir_jobposting(vaga: dict) -> dict:
    """Campos que interessam ao JobRadar, com os nomes que ele usa."""
    org = vaga.get("hiringOrganization") or {}
    local = vaga.get("jobLocation") or {}
    if isinstance(local, list):
        local = local[0] if local else {}
    endereco = (local or {}).get("address") or {}
    return {
        "titulo": vaga.get("title", ""),
        "empresa": org.get("name", "") if isinstance(org, dict) else "",
        "cidade": endereco.get("addressLocality", "") if isinstance(endereco, dict) else "",
        "uf": endereco.get("addressRegion", "") if isinstance(endereco, dict) else "",
        "publicado_em": vaga.get("datePosted", ""),
        "remoto": bool(vaga.get("jobLocationType")),
    }


# --------------------------------------------------------------------- feed

def links_de_feed(html: str, base: str = "") -> list[str]:
    """Feeds RSS/Atom declarados no <head>."""
    achados = []
    for tag in re.findall(r"<link[^>]+>", html, re.IGNORECASE):
        if not re.search(r'type=["\'][^"\']*(rss|atom)', tag, re.IGNORECASE):
            continue
        href = re.search(r'href=["\']([^"\']+)', tag, re.IGNORECASE)
        if href:
            achados.append(urljoin(base, href.group(1)) if base else href.group(1))
    return achados


# ----------------------------------------------------------------- endpoint

def pontuar_endpoint(url: str, tipo_conteudo: str) -> int:
    """Quao provavel e que esta requisicao seja a busca de vagas do site.

    So ordena o relatorio -- nenhum candidato e escondido por pontuar baixo,
    porque o nome do endpoint nem sempre denuncia o que ele faz.
    """
    if "json" not in (tipo_conteudo or "").lower():
        return 0
    alvo = url.lower()
    return 1 + sum(2 for pista in _PISTAS_DE_VAGA if pista in alvo)


# ------------------------------------------------------------------ relatorio

def descrever_erro(erro: BaseException) -> str:
    """Traduz falha de rede pra algo acionavel.

    MEDIDO na primeira vez que este script foi usado: a usuaria copiou o
    endereco de EXEMPLO da documentacao (que era ficticio) e recebeu de
    volta um NameResolutionError com stack de proxy dentro -- quatro linhas
    de jargao pra dizer "esse site nao existe". Perdeu tempo procurando
    defeito no script.

    Mesmo principio dos avisos dos scrapers, e o quinto caso desta base:
    mensagem que nao distingue as causas faz a pessoa investigar a coisa
    errada.
    """
    texto = f"{erro}".lower()
    if "getaddrinfo" in texto or "nameresolution" in texto or "name or service not known" in texto:
        return "site nao encontrado — confira se o endereco existe e esta escrito certo"
    if "timed out" in texto or "timeout" in texto:
        return "o site nao respondeu a tempo — pode estar lento ou bloqueando robo"
    if "proxy" in texto or "tunnel connection failed" in texto:
        return "a rede daqui bloqueou o acesso a este site"
    if "certificate" in texto or "ssl" in texto:
        return "problema no certificado do site"
    if "connection refused" in texto or "connectionerror" in texto:
        return "nao consegui conectar no site"
    return f"{type(erro).__name__}: {erro}"


def _linha(rotulo: str, valor: str) -> None:
    print(f"  {rotulo:<26} {valor}")


def avaliar(url: str) -> None:
    partes = urlsplit(url)
    raiz = f"{partes.scheme}://{partes.netloc}"
    print(f"\n{'=' * 70}\nAVALIANDO: {url}\n{'=' * 70}")

    # 1. robots.txt
    print("\n[1] robots.txt")
    try:
        r = requests.get(f"{raiz}/robots.txt", timeout=TIMEOUT, headers={"User-Agent": UA})
        if r.status_code == 200:
            regras = regras_do_robots(r.text)
            permitido = caminho_permitido(regras, partes.path or "/")
            _linha("regras para robo generico:", len(regras))
            _linha("este caminho e permitido?", "SIM" if permitido else "NAO  <<< o site pede pra nao raspar aqui")
            for permissao, caminho in regras[:8]:
                _linha("", f"{permissao}: {caminho}")
            for sitemap in re.findall(r"(?im)^\s*sitemap:\s*(\S+)", r.text)[:3]:
                _linha("sitemap:", sitemap)
        else:
            _linha("status:", f"{r.status_code} (sem robots.txt)")
    except Exception as erro:
        _linha("falhou:", descrever_erro(erro))

    # 2 e 3. pagina: JSON-LD e feed
    print("\n[2] JSON-LD (schema.org/JobPosting) na pagina")
    html = ""
    try:
        r = requests.get(url, timeout=TIMEOUT, headers={"User-Agent": UA})
        html = r.text
        vagas = extrair_jobpostings(html)
        _linha("status HTTP:", r.status_code)
        _linha("JobPosting encontrados:", len(vagas))
        if vagas:
            print("\n  >>> MELHOR CASO: dado estruturado, publicado de proposito pro")
            print("      Google Jobs. Nao depende de seletor de HTML.\n")
            for vaga in vagas[:3]:
                resumo = resumir_jobposting(vaga)
                _linha("", f"{resumo['titulo'][:44]:<44} | {resumo['empresa'][:20]:<20} | "
                           f"{resumo['cidade']}/{resumo['uf']} | {resumo['publicado_em'][:10]}")
    except Exception as erro:
        _linha("falhou:", descrever_erro(erro))

    if not html:
        print("\n" + "-" * 70)
        print("A pagina nao carregou, entao nao ha o que avaliar nela.")
        print("Se o endereco veio da documentacao: o exemplo antigo era FICTICIO.")
        print("Use a URL de uma busca real, por exemplo:")
        print('  python avaliar_fonte.py '
              '"https://portal.gupy.io/job-search/term=analista%20de%20dados"')
        return

    print("\n[3] feed RSS/Atom")
    feeds = links_de_feed(html, raiz)
    _linha("feeds declarados:", len(feeds))
    for feed in feeds[:5]:
        _linha("", feed)

    # 4. endpoints XHR
    print("\n[4] endpoints JSON que a propria pagina chama")
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        _linha("playwright:", "nao instalado — pulando")
        return

    candidatos: list[tuple[int, str, str, int]] = []
    with sync_playwright() as p:
        navegador = p.chromium.launch(headless=True)
        aba = navegador.new_page(user_agent=UA)

        def ao_responder(resposta):
            tipo = (resposta.headers or {}).get("content-type", "")
            pontos = pontuar_endpoint(resposta.url, tipo)
            if pontos:
                candidatos.append((pontos, resposta.url, tipo.split(";")[0], resposta.status))

        aba.on("response", ao_responder)
        try:
            aba.goto(url, timeout=60000)
            aba.wait_for_timeout(6000)
        except Exception as erro:
            _linha("falhou:", descrever_erro(erro))
        finally:
            navegador.close()

    unicos = {u: (p, u, t, s) for p, u, t, s in sorted(candidatos)}.values()
    _linha("candidatos JSON:", len(unicos))
    for pontos, endereco, tipo, status in sorted(unicos, reverse=True)[:10]:
        marca = "  <<< nome sugere vagas" if pontos > 1 else ""
        print(f"      [{pontos:>2}] {status} {tipo:<18} {endereco[:88]}{marca}")

    print("\n" + "-" * 70)
    print("PROXIMO PASSO: se achou JSON-LD, use ele — e o mais estavel.")
    print("Se nao, abra o endpoint de maior pontuacao no navegador e veja se")
    print("da pra trocar o termo e a pagina nos parametros. E MEÇA o rendimento")
    print("antes de ligar no ciclo: a Senior tinha endpoint bom e rendeu 0,3%.")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)
    for alvo in sys.argv[1:]:
        avaliar(alvo)
