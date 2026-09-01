"""A Solides voltou 70 vagas no ciclo de 01/09 08:14, contra ~400 nos ciclos
anteriores -- e devolveu "0 resultados reais" nos CINCO termos prioritarios,
incluindo 'analista de dados', que dois dias antes dava 205 vagas em 21
paginas.

No ciclo de 31/08 13:53 tambem apareceu um "Status 504" nessa API.

O QUE ESTE SCRIPT SEPARA:

  (A) A API responde 200 com count > 0 agora  -> a queda foi passageira
      (instabilidade ou rate-limit). O scraper esta lendo certo, mas
      interpreta count=0 como "nao ha vaga", que e a mesma classe de alarme
      falso que a gente passou a semana eliminando -- so que pior, porque
      some com a fonte inteira em silencio.

  (B) A API responde 200 com count = 0        -> o contrato mudou (nome do
      parametro, endpoint, filtro obrigatorio) e o scraper precisa de
      conserto de verdade.

  (C) A API nao responde (5xx, timeout)       -> esta fora do ar.

Rode com o robo parado, pra nao disputar requisicao.
"""
import time

import requests

from scrapers.solides import TAKE, TIMEOUT, UA, URL_API

TERMOS = ["analista de dados", "analista de bi", "business intelligence",
          "data analyst", "power bi", "sql"]

print(f"{'termo':24} {'status':>7} {'count':>8} {'paginas':>8} {'itens':>6}")
print("-" * 58)
linhas = []
for termo in TERMOS:
    try:
        r = requests.get(
            URL_API,
            params={"title": termo, "take": TAKE, "page": 1},
            timeout=TIMEOUT,
            headers={"User-Agent": UA, "Accept": "application/json"},
        )
        try:
            d = (r.json() or {}).get("data") or {}
        except ValueError:
            print(f"{termo:24} {r.status_code:>7}   resposta nao e JSON")
            linhas.append((r.status_code, None))
            continue
        count = d.get("count")
        print(f"{termo:24} {r.status_code:>7} {str(count):>8} "
              f"{str(d.get('totalPages')):>8} {len(d.get('data') or []):>6}")
        linhas.append((r.status_code, count))
    except Exception as erro:
        print(f"{termo:24}   ERRO  {type(erro).__name__}: {erro}")
        linhas.append((None, None))
    time.sleep(1.5)

print("-" * 58)
ok = [c for s, c in linhas if s == 200 and c]
zero = [c for s, c in linhas if s == 200 and c == 0]
falhou = [s for s, _ in linhas if s != 200]

if ok:
    print(f"=> (A) {len(ok)} de {len(TERMOS)} termos respondem com vaga AGORA.")
    print("   A queda do ciclo foi passageira. O problema nao e o scraper ler")
    print("   errado -- e ele tratar count=0 como 'nao ha vaga' quando pode ser")
    print("   a API oscilando. Mesma familia do alarme falso do LinkedIn.")
elif zero and not falhou:
    print("=> (B) A API responde 200 mas com count=0 em tudo. O contrato mudou:")
    print("   parametro, endpoint ou filtro obrigatorio. Precisa consertar o")
    print("   scraper -- e a sondagem de campo (testar_api_solides.py) diz como.")
else:
    print(f"=> (C) A API nao esta respondendo ({falhou}). Esta fora do ar.")
    print("   Nada a consertar no codigo agora; medir de novo daqui a algumas horas.")
