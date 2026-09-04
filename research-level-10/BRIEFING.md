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

Exemplo de leitura da lista (cursor na 2ª de 5): Porygon-Z, **Remoraid**,
Empoleon, Ninetales, Magnemite.

- A **lista vai mudando** — ele disse-o explicitamente e que falamos disso
  mais tarde. Não assumir ordem estável.

## Por esclarecer (perguntar ou esperar que ele diga)

- Que jogo/versão exactamente, e onde está no jogo (Alabaster Icelands é Hisui,
  parece Legends: Arceus — confirmar em vez de assumir).
- O que contam os três contadores do topo.
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
