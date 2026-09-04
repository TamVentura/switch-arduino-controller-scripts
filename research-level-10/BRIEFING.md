# Research level 10 — briefing

Notas tiradas ao vivo enquanto o Tiago explica o que quer, com a sessão
`free-play` a correr (ver `free-play/main.py`). **Isto é só recolha de
informação** — o plano e a implementação são para uma sessão nova, a partir
deste ficheiro.

Estado: **a recolher**. O Tiago disse que avisa quando estiver tudo e quando
puder começar a pensar em como fazer.

## Objetivo

- Chegar a **research level 10 com todos os Pokémon**.
- **Zona por zona** (uma área de cada vez, não a dex toda em paralelo).
- Organizado para **o mínimo de trabalho manual possível** para ele, e **o mais
  rápido possível**.

## O que já vi no ecrã

Ecrã de trabalho: o **Pokédex**, com o painel de detalhe à esquerda e a
**lista de Pokémon à direita**. Confirmado ao vivo (captura 1920x1080).

- Filtro de área activo no topo da lista: **Alabaster Icelands**.
- Ordenação: **"Y — Ordered by research level"** (portanto a ordem da lista é
  mutável e o Y alterna-a).
- Contadores no topo: `91 / 91 / 76` (ainda não sei o que cada um conta).
- No detalhe de um Pokémon: nº e nome, tipo, peso/altura, **Preferred Foods**,
  **Items Carried**, texto de estado da investigação e o **Research Level**
  (ex.: Elekid nº182, research level 7). `X` = Motions/Cries, `B` = Back.
- Abas do topo esquerdo (3) e uma fila de ícones à direita entre `ZL` e `ZR`
  (filtros/vistas) — ainda não sei o que são.

### Coordenadas medidas (espaço 4K, o que o select da janela de captura dá)

| o que | zona | notas |
|---|---|---|
| lista de Pokémon (direita) | `2814, 444, 3722, 1870` | 454x713 no recorte; mostra **5 entradas** |
| lista + filtro + rodapé | `2788, 268, 3806, 2018` | 509x875; inclui o filtro de área no topo e o "Y — ordered by research level" em baixo |

Exemplo de leitura da lista (cursor na 2ª de 5): Porygon-Z, **Remoraid**,
Empoleon, Ninetales, Magnemite.

- A **lista vai mudando** — ele disse-o explicitamente e que falamos disso
  mais tarde. Não assumir ordem estável.
### Filtro de região (ZL / ZR)

A lista é separada por regiões e o filtro alterna com **ZL** / **ZR** (teclas
`l` e `r` do Arduino). Entre o `ZL` e o `ZR` há **6 ícones**, com o activo
sublinhado, e o nome da região aparece na barra logo abaixo.

Ordem, medida ao vivo dando uma volta completa com ZR:

| ZR | região |
|---|---|
| 0 | Entire Hisui Region |
| 1 | Obsidian Fieldlands |
| 2 | Crimson Mirelands |
| 3 | Cobalt Coastlands |
| 4 | Coronet Highlands |
| 5 | Alabaster Icelands |
| 6 | wrap — volta a Entire Hisui Region |

- **Dá wrap nos dois sentidos**: com ZL, de "Entire Hisui Region" salta para
  "Alabaster Icelands". Confirmado.
- São 6 posições, logo **6 pressões de ZR voltam ao mesmo sítio** — dá para
  reancorar o filtro sem saber onde estava, ou para o repor depois de mexer.
- O nome da região lê-se na zona `2750, 336, 3810, 420` (só a barra do nome,
  530x42 — barato de ler). A fila de ícones + nome é `2750, 136, 3810, 442`.

### Como se vê que um Pokémon está completo

**Um ícone de Poké Ball no lado direito da linha.** Ele apontou-me isto
directamente: numa lista de 5, o Elekid (incompleto) não tinha o ícone e os
outros 4 (Dartrix, Cyndaquil, Quilava, Oshawott) tinham. Confere com uma
leitura anterior, onde Bidoof/Bibarel tinham e Abomasnow/Braviary/Elekid não.

Medido no recorte `2788, 268, 3806, 2018` (509x875, escala 2.0 para 4K):

- o badge está sempre na **mesma coluna**: x ≈ **3574** em coords 4K
- **passo entre linhas: 280** em coords 4K (as 4 bolas saíram em
  y = 852, 1132, 1412, 1692; a linha do Elekid, sem bola, seria y ≈ 572)
- **cuidado:** filtrar vermelho na linha toda dá falso positivo no *sprite*
  do Pokémon (apanhei um em x ≈ 2976). Ler só a coluna do badge.

## As 3 abas do detalhe (mudam-se com A)

Barra das abas no canto superior esquerdo, zona `80, 36, 716, 222` (318x93).
O **A cicla** as três, sempre na mesma ordem, e dá wrap:

| # | ícone | aba | interesse |
|---|---|---|---|
| 1 | Poké Ball | **dados do Pokémon** | ele disse que **não interessa** |
| 2 | visto | **Research Tasks** | **uma das mais importantes** |
| 3 | cara de Pokémon | **habitat** | mapa de Hisui + Favored Conditions |

- A **aba activa é a que está desenhada a escuro/cheia**; as inactivas ficam
  esbatidas. O badge `A` flutua encostado à esquerda da aba activa, portanto
  também serve de indicador, mas o mais fiável é o ícone escuro.
- Painel de conteúdo inteiro (abas incluídas): zona `60, 30, 2740, 2050`.

### Aba 2 — Research Tasks (a que interessa)

Título `Research Tasks for <Pokémon>`. Cada linha é uma tarefa com o **valor
actual** e os **5 patamares**. Lido ao vivo para o Porygon-Z (research level 2):

| tarefa | actual | patamares | bónus |
|---|---|---|---|
| Number caught | 1 | 1 ✓ · 2 · 3 · 4 · 5 | **chevrons vermelhos** |
| Times you've seen it use Thunderbolt | 0 | 1 · 3 · 6 · 12 · 25 | — |
| Times you've seen it use Recover | 0 | 1 · 3 · 6 · 12 · 25 | — |
| Times you've seen it use Hyper Beam | 0 | 1 · 3 · 8 · 20 · 40 | **chevrons vermelhos** |
| Times you've seen it use a strong style move | 0 | 1 · 3 · 6 · 12 · 25 | — |
| Times you've seen it use an agile style move | 0 | 1 · 3 · 6 · 12 · 25 | — |

- O patamar já atingido leva um **visto vermelho**; os restantes ficam em
  amarelo claro.
- **Confirmado por ele:** as linhas com o **ícone de chevrons vermelhos duplos**
  à esquerda valem **2 pontos por passo em vez de 1**. Cada **coluna é um
  passo**, e os passos que já têm visto estão feitos. Logo o valor de uma
  tarefa não é o número de passos — é passos x 1 ou passos x 2.
- Abaixo das tarefas preenchidas ficam **checkboxes vazias** (tarefas ainda
  não reveladas para este Pokémon).
- O **Research Level** aparece em baixo à direita, com barra e número
  (Porygon-Z: 2). Os patamares mais altos de cada tarefa são a diferença entre
  chegar a 10 ou não.
- Os patamares **não são iguais para todas as tarefas** — o Hyper Beam ia a 40
  e os outros movimentos a 25. Ler os números do ecrã, não assumir uma tabela.

### ⚠️ Cada região guarda o seu próprio estado

Ele avisou-me disto e confirmei ao vivo. **O filtro de região não é só um
filtro da lista: cada posição guarda o seu próprio contexto.** Uma pressão de
ZR levou-me de

| | antes | depois de 1x ZR |
|---|---|---|
| região | Entire Hisui Region | Obsidian Fieldlands |
| aba activa | 3 — habitat | **2 — Research Tasks** |
| Pokémon | Porygon-Z | **Cherubi** |
| contadores | 242 / 242 / 188 | 85 / 85 / 83 |

Ou seja, **cada região lembra-se da aba e do Pokémon onde ficou**. E é
persistente: um ZL de volta devolveu exactamente Entire Hisui Region + habitat
+ Porygon-Z.

**Consequência para o plano:** depois de qualquer ZL/ZR **nunca assumir a aba
nem o cursor** — tem de se voltar a ler os dois. Não dá para "pôr na aba das
research tasks uma vez e depois andar pelas regiões".

### A faixa de estado — uma leitura só dá tudo

A zona `60, 30, 3830, 450` (1885x210) apanha, de uma vez:

- **qual das 3 abas está activa** (ícone escuro, canto superior esquerdo)
- os **3 contadores** do topo (mudam com a região)
- o **título do painel**, que identifica aba *e* Pokémon ao mesmo tempo:
  `Research Tasks for Cherubi`, `Porygon-Z's Habitat`, ou `No. 135 Porygon-Z`
  na aba dos dados
- o **nome da região** e a fila de ícones com o activo sublinhado

É o recorte a usar para reancorar depois de um ZL/ZR, em vez de várias
screenshots separadas.

### Segundo exemplo de research tasks — Remoraid

Mais rico que o do Porygon-Z, e mostra que a forma das linhas varia:

| tarefa | actual | passos | vale |
|---|---|---|---|
| Number caught | 1 | 1 ✓ · 2 · 4 · 10 · 15 | **x2** |
| Number you've caught without being spotted | 0 | 1 · 2 · 4 · 6 · 10 | x1 |
| Number defeated | 0 | 1 · 3 · 6 · 12 · 25 | x1 |
| Number you've defeated with Grass-type moves | 0 | 1 · 2 · 4 · 6 · 10 | x1 |
| Times you've seen it use Charge Beam | 0 | 1 · 3 · 6 · 12 · 25 | **x2** |
| Times you've seen it use Ice Beam | 0 | 1 · 3 · 6 · 12 · 25 | x1 |
| Number you've evolved | 0 | **só 1 passo, e desenhado na última coluna** | **x2** |

Reparar:

- **não são sempre 5 passos.** O "Number you've evolved" tem um único passo, e
  aparece encostado à **coluna da direita** — as colunas não são "passo 1 a 5"
  alinhados; há tarefas com menos passos. Quem ler isto tem de ler os números,
  não contar colunas.
- os tipos de tarefa que já vi: **capturar**, **capturar sem ser visto**,
  **derrotar**, **derrotar com movimentos de um tipo**, **ver usar um movimento
  concreto**, **ver usar movimento strong/agile style**, **evoluir**.
- as tarefas e os patamares **mudam de Pokémon para Pokémon** (comparar com o
  Porygon-Z acima).

## Aba 3 — habitat: onde encontrar o Pokémon

Ele sublinhou que **isto vai ser importante para o que vem a seguir**.

O painel tem **5 cartões, um por região**, em posições fixas, e cada um lista
os **sítios concretos** onde o Pokémon aparece nessa região, ou diz
**"Not found here"**. Para o Remoraid: só **Cobalt Coastlands** — *Castaway
Shore*, *Sand's Reach*, *Tranquility Cove* — e os outros 4 "Not found here".

Layout medido (coords 4K, a partir das barras de título dos cartões, painel
lido na zona `90, 218, 2660, 1690`):

| cartão | posição | barra de título medida |
|---|---|---|
| título do painel (`<Pokémon>'s Habitat`) | topo | `334, 246 – 2366, 394` |
| **Alabaster Icelands** | cima-esquerda | `190, 426 – 920, 582` |
| **Cobalt Coastlands** | cima-direita | `1776, 388 – 2508, 504` |
| **Coronet Highlands** | centro | `996, 698 – 1718, 808` |
| **Obsidian Fieldlands** | baixo-esquerda | `206, 1000 – 932, 1112` |
| **Crimson Mirelands** | baixo-direita | `1788, 966 – 2512, 1076` |

O corpo de cada cartão (a lista de sítios) fica logo abaixo da sua barra e
estende-se ~330 px (4K) para baixo — ex.: o cartão do Cobalt inteiro é
`1776, 388 – 2508, 810`. Confirmar cartão a cartão antes de usar.

No rodapé, **Favored Conditions**: um grupo de **4 ícones de hora do dia**
(`1108, 1466 – 1638, 1566`) e um grupo de **7 de meteorologia**
(`1728, 1446 – 2574, 1554`). No Remoraid apareceram todos realçados; no
Porygon-Z (sem dados) apareciam todos esbatidos. **A confirmar** o que
realçado significa.

### Nota operacional sobre o `/zone`

O recorte que o `/zone` devolve é **do momento em que a seleção foi feita**,
não é live: apanhei a barra das abas a mostrar a aba 2 quando o ecrã já estava
na 3. Para ver o estado actual usar `/shot?zone=…`; o `/zone` serve para saber
*onde* ele está a apontar.

## Por esclarecer (perguntar ou esperar que ele diga)

- Que jogo/versão exactamente, e onde está no jogo (Alabaster Icelands é Hisui,
  parece Legends: Arceus — confirmar em vez de assumir).
- O que contam os três contadores do topo (vi `91/91/76` com o filtro numa
  área e `242/242/188` em Entire Hisui Region, logo são por filtro).
- Que tarefas de investigação contam para cada Pokémon e quais compensa
  automatizar (capturas? derrotas? usar movimentos? alimentar?).
- Como decidir a ordem: por área, por research level actual, por proximidade?
- O que quer que seja automatizado vs. o que faz ele à mão.
- Se a lista muda de ordem, como é que o script se reancora entre passagens.

## Ferramentas já prontas

`free-play` (commits `bbc5868`, `beab6c0`) — janela de captura + passthrough do
controlador, sem automação, com um canal HTTP em `127.0.0.1:8777`:
`/shot[?zone=…|last]`, `/zone`, `/press`, `/hold`, `/stick`, `/state`, e a aba
`http://127.0.0.1:8777/` com a última screenshot e o último recorte. É por aí
que ele me aponta zonas do ecrã e eu leio o jogo.
