"""Le jobradar.log e responde uma pergunta so: quando uma busca do LinkedIn
volta vazia, ela volta CHEIA num ciclo seguinte?

POR QUE: MEDIDO em 30/08, o endpoint guest do LinkedIn devolve 0 ou 10 pro
mesmo par (termo x cidade) em horas diferentes, do mesmo IP. Se o par se
recupera sozinho no proximo ciclo, o custo e ATRASO e nao ha nada a
consertar. Se ele fica vazio ciclo apos ciclo enquanto uma rodada isolada
acha 10 vagas, entao ha vaga sendo perdida e vale escrever correcao.

Este script nao muda nada no robo. So le o log.

NOTA: o log so registra linha quando a busca volta VAZIA. Entao da pra
distinguir "vazia" de "nao vazia", que e o suficiente pra esta pergunta.
"""
import re
import sys
from collections import defaultdict

CAMINHO = sys.argv[1] if len(sys.argv) > 1 else "jobradar.log"

RE_CICLO = re.compile(r"^(\d\d:\d\d:\d\d).*Bloco de termos deste ciclo")
RE_BUSCA = re.compile(r"^(\d\d:\d\d:\d\d).*\[LinkedIn(?: Intl)?\] Buscando \((.+?)\): (.+?)\s*$")
RE_VAZIO = re.compile(r"\[LinkedIn(?: Intl)?\] Nenhum resultado retornado")
# Marca o fim do scraper do LinkedIn dentro do ciclo. Busca que aparece DEPOIS
# disso e antes do proximo "Bloco de termos" nao e do ciclo -- e script de
# medicao rodado a mao entre um ciclo e outro (foi o que aconteceu em 30/08
# com testar_22_vazios.py, e sem esta linha aquelas 22 buscas entravam no
# balde do ciclo anterior e apareciam como "recuperou" que nunca houve).
RE_FIM = re.compile(r"\[LinkedIn(?: Intl)?\] \d+ vaga\(s\) encontrada\(s\) no total")

ciclos = []          # [(hora_inicio, {par: "vazio"|"ok"})]
atual = None
ultimo_par = None
fechado = False

for linha in open(CAMINHO, encoding="utf-8", errors="replace"):
    if RE_CICLO.match(linha):
        atual = (RE_CICLO.match(linha).group(1), {})
        ciclos.append(atual)
        ultimo_par = None
        fechado = False
        continue
    if atual is None or fechado:
        continue
    if RE_FIM.search(linha):
        fechado = True
        ultimo_par = None
        continue
    m = RE_BUSCA.match(linha)
    if m:
        ultimo_par = (m.group(3), m.group(2))
        atual[1][ultimo_par] = "ok"
        continue
    if RE_VAZIO.search(linha) and ultimo_par:
        atual[1][ultimo_par] = "vazio"

ciclos = [c for c in ciclos if c[1]]
print(f"{len(ciclos)} ciclo(s) com busca do LinkedIn no log.\n")

# So interessa par visto em mais de um ciclo -- e o que responde a pergunta.
historico = defaultdict(list)
for i, (hora, pares) in enumerate(ciclos):
    for par, estado in pares.items():
        historico[par].append((i, estado))

repetidos = {p: h for p, h in historico.items() if len(h) > 1}
com_vazio = {p: h for p, h in repetidos.items() if any(e == "vazio" for _, e in h)}

print(f"{len(repetidos)} par(es) buscados em mais de um ciclo.")
print(f"{len(com_vazio)} desses voltaram vazios em pelo menos um ciclo.\n")

recuperou = sempre_vazio = 0
print("par (termo x local)                                    ciclos")
print("-" * 78)
for par, h in sorted(com_vazio.items()):
    seq = "".join("0" if e == "vazio" else "." for _, e in h)
    if "0" in seq and "." in seq:
        recuperou += 1
        marca = "RECUPEROU"
    else:
        sempre_vazio += 1
        marca = "sempre vazio"
    termo, local = par
    print(f"  {termo[:26]:26} {local[:22]:22}  {seq:10} {marca}")

print("-" * 78)
print("legenda: 0 = voltou vazia   . = voltou com vaga   (ordem cronologica)")
print()
print(f"RECUPEROU em ciclo seguinte ...... {recuperou}")
print(f"vazia em TODOS os ciclos ......... {sempre_vazio}")
print()
# Barra de amostra. Com 7 pares -- e 4 deles de configuracao antiga, de antes
# de LOCATIONS_LINKEDIN virar "Brazil" -- nao da pra concluir nada. O rodizio
# leva 3 ciclos pra repetir um termo, entao a resposta so aparece depois de
# uns 4 ciclos (~12h rodando).
MINIMO_PARES = 15

if len(com_vazio) < MINIMO_PARES:
    print(f"=> AMOSTRA PEQUENA ({len(com_vazio)} pares, minimo {MINIMO_PARES}).")
    print("   Nao concluir nada ainda. O rodizio leva 3 ciclos pra repetir um")
    print("   termo, entao rode isto de novo depois de ~12h de robo ligado.")
    print("   Cuidado tambem com ciclo antigo no log: os que rodaram antes de")
    print("   30/08 usavam outra configuracao de location e nao comparam.")
elif recuperou >= sempre_vazio * 2:
    print("=> O rodizio recupera sozinho na maioria dos casos. O custo e")
    print("   ATRASO, nao perda -- nao ha o que consertar no scraper.")
elif sempre_vazio >= recuperou * 2:
    print("=> Ha par que fica vazio ciclo apos ciclo. Pegue UM deles e rode")
    print("   isolado: se voltar com vaga, ha vaga sendo perdida de verdade,")
    print("   e ai vale escrever a segunda passada no fim do ciclo.")
else:
    print("=> Empatado: parte recupera, parte nao. Junte mais ciclos antes de")
    print("   decidir -- e nao decida por um par so.")
