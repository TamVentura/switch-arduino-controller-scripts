# Handoff — ler o que já está feito, mon a mon

Texto para colar numa sessão nova. Tudo o que está aqui foi medido ao vivo na
sessão de 2026-09-04, não é estimativa.

---

## O que quero

Para cada Pokémon que ainda **não** está a research level 10, quero saber
**quantos passos estão feitos em cada research task**. Só isso do ecrã.

O resultado é um JSON por mon, tipo:

```json
{"mon": "Remoraid", "research_level": 2,
 "tasks": [{"index": 1, "checks": 1}, {"index": 2, "checks": 0}, ...]}
```

## Porque só isso

Os nomes das tarefas, os patamares de cada passo, quais têm bónus, as
localizações, o learnset, as preferred foods e os mapas **já estão todos** em
`C:\Projects\pla-dex-data` (raspados do game8, 245 Pokémon). São dados do jogo,
iguais em todos os saves. Ler isso do ecrã seria trabalho a dobrar e risco de
erro a dobrar.

## O que NÃO fazer

- **Não** tirar screenshot da aba do habitat. Essa informação vem do game8.
- **Não** ler nomes de tarefas nem patamares do ecrã.
- **Não** usar um modelo para contar os vistos. É Python — determinístico,
  barato e verificável. (Um teste com Haiku deu 3/3 leituras perfeitas do painel
  inteiro, mas isso era para quando não havia a fonte de dados externa; ver
  `DECISAO-leitura.md`.)

## Como arrancar

```
python free-play/main.py
```

Abre a janela de captura, o passthrough do controlador e um servidor de
controlo em `127.0.0.1:8777`. Comandos por curl:

| endpoint | faz |
|---|---|
| `/shot?zone=x1,y1,x2,y2` | screenshot (coords 4K), grava em `free-play/shots/` |
| `/press?keys=A,2&sleep=0.4` | botões (`2` = d-pad baixo, `l`/`r` = ZL/ZR) |
| `/state` | há frame? em pause? |

Nota: o Tiago pode estar a jogar ao mesmo tempo. Quando estiveres a conduzir,
avisa-o.

## Geometria medida (coords 4K)

| o que | zona |
|---|---|
| painel das research tasks | `90, 218, 2740, 1900` (crop 1325x841, escala 2.0) |
| research level (aparece em qualquer aba) | `2074, 1624, 2720, 1868` |
| lista de Pokémon | `2814, 444, 3722, 1870` (5 entradas visíveis) |
| barra das 3 abas | `80, 36, 716, 222` |
| faixa de estado (aba + contadores + região) | `60, 30, 3830, 450` |

Dentro do painel das tarefas:

- **linha `i` (1-based), centro:** `y = 486 + (i-1) * 116`
- **ícone de bónus:** coluna `x ~ 205`
- **vistos:** na zona dos pills, `x > 1750`

Na lista de Pokémon: o **badge de Poké Ball** (mon completo) cai em `x ~ 3574`,
com **280** de passo entre linhas.

## Detector de vermelho — validado 3/3

O visto e o ícone de bónus são ambos vermelhos; separam-se pela coluna.

```python
b, g, r = (im[:, :, i].astype(int) for i in range(3))
red = ((r > 150) & (g < 110) & (b < 110) & (r - g > 70)).astype(np.uint8)
n, _, stats, cents = cv2.connectedComponentsWithStats(red, 8)
blobs = [... for i in range(1, n) if stats[i][4] > 80]   # area > 80
# no crop 1325x841: x < 100 -> icone de bonus; x > 780 -> visto
```

Testado em Porygon-Z, Remoraid e Empoleon: acertou o número de bónus (2, 3, 3) e
de vistos (1, 1, 1) em todos, **sem um falso positivo**. Atenção: o sprite do
Pokémon também tem vermelho noutras abas — por isso só se corre isto no painel
das tarefas.

Como os pills são **right-aligned**, uma tarefa com menos de 5 passos ocupa as
últimas colunas. Para contar não interessa: contam-se os vistos na faixa da
linha. O game8 diz quantos passos tem cada tarefa, o que dá o limite superior.

## Saber que mon está selecionado

Duas leituras, ambas em posição fixa (o Tiago insistiu nisto, e tem razão — não
ler o título do painel, que é texto variável):

1. Na lista, a linha selecionada é a que está **branca/creme** (com o `>`
   amarelo à esquerda). A posição dela dentro das 5 visíveis varia.
2. **OCR do nome** com `utils/ocr.py`, e depois **fuzzy match** contra os 245
   nomes de `C:\Projects\pla-dex-data\data\index.json`. O OCR não precisa de
   estar perfeito — o match resolve.

E a **aba activa** lê-se na barra das abas: o ícone escuro. Aba 2 (visto) é a
das research tasks.

## Verificação obrigatória

O ecrã tem redundância suficiente para se auto-verificar. Nenhum mon entra no
resultado sem passar isto:

1. **checks <= nº de passos da tarefa** (do game8).
2. **Checksum do nível:** `soma(checks_da_tarefa x pontos_por_passo) / 10`, com
   cap em 10, tem de dar o **research level mostrado no ecrã**. `pontos_por_passo`
   é 10, ou 20 nas tarefas de bónus. Validado em 3 mons: 1 passo numa tarefa de
   bónus = 20 pontos = nível 2, e o ecrã mostrava 2 nos três casos.
3. **nº de bónus detectados == nº de bónus no game8** — valida que as linhas
   estão alinhadas com a tarefa certa.
4. **nº de tarefas no ecrã == nº de tarefas no game8** para aquele mon.
5. **Diff de pixels entre painéis consecutivos != 0** — se um d-pad não
   registar, o painel repete-se e ler-se-ia o mesmo mon duas vezes, saltando
   outro. É o pior erro possível e apanha-se sem OCR.

Qualquer mon que falhe um destes: tirar nova screenshot e repetir. Se falhar
outra vez, guardar a screenshot e reportar em vez de adivinhar.

## Navegação — nunca assumir estado

- `2` (d-pad baixo) desce na lista.
- `A` cicla as abas: **dados -> research tasks -> habitat -> dados**.
- `l`/`r` (ZL/ZR) muda a região, e **cada região guarda a sua própria aba e o
  seu próprio cursor**. Depois de mexer no filtro, reler aba e cursor.
- A lista **dá wrap** e a ordenação muda com `Y` ("Ordered by research level"),
  o que agrupa os incompletos — verificar se compensa usar isso para os
  encontrar mais depressa.

## Ponto de partida

188 de 242 completos, ou seja **faltam 54**. Encontram-se pelos que **não têm o
badge de Poké Ball** na lista. Não varrer os 242.

## Ler primeiro

`research-level-10/BRIEFING.md` — tem o resto do que foi medido: as 6 regiões e
o wrap do ZL/ZR, as 3 abas, a estrutura das research tasks, o significado dos
chevrons duplos, e as armadilhas já apanhadas.
