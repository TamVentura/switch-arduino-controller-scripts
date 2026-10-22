# eggs_auto: como deixar o jogo antes de correr

Pokémon Scarlet/Violet. Os passos estão escritos para o modo **1 (Sem localização)**,
mas servem para todos os modos. Nos modos **0 (Reiniciar)** e **4 (Full Auto)**, o
jogo é reiniciado **sem gravar**, por isso este estado tem de estar **gravado** (Save).

## Party

| Slot | O quê |
|------|-------|
| 1 | Progenitor (ex.: Growlithe) |
| 2 | Ditto (ou o outro progenitor) |
| 3–6 | **Vazios** |

Na eclosão, o script move uma coluna de 5 ovos para os slots 2–6, por isso a party
tem de ter exatamente estes 2 Pokémon.

## Boxes

Chamo **box K** à box de trabalho (no meu save é a "Box 6").

**Box K**
- **Linha 1, coluna 1:** o Pokémon com **Flame Body** (Talonflame).
- **Linha 1, coluna 2:** **vazia** (é para lá que vai o Ditto).
- O resto pode ter outros Pokémon.

**Box K+1 e as seguintes:** **vazias**. É para lá que vão os ovos (30 por box) e,
depois de eclodirem, os bebés voltam para o mesmo sítio. A eclosão avança com `R`
enquanto encontrar ovos, por isso deixa vazias as boxes suficientes para um lote.

**A última box vista tem de ser a box K.** O script abre as boxes e carrega em `R`
uma vez para ficar na K+1 antes do piquenique. Fecha as boxes (e grava, se for o
caso) com a box K no ecrã. O cursor volta sozinho à primeira casa, por isso não
tens de o pôr em lado nenhum.

## Full Auto: o que fazer à mão antes de arrancar

1. Deixa a party e as boxes como está acima.
2. **Grava o jogo** nesse estado. Cada iteração reinicia o jogo sem gravar e volta
   a este save, por isso os ingredientes nunca acabam.
3. Deixa o jogo **aberto e em primeiro plano**. O reinício começa com `Home`.
   O sítio onde estás não interessa, porque o script voa até ao local do
   piquenique.
4. Garante que há boxes vazias suficientes depois da box K para os ovos de todas
   as sandes de uma iteração (cada sande dá 30 min de ovos).
5. Arranca o `eggs.py`, escolhe **4 (Full Auto)** e responde ao número de
   iterações, ao tipo de shiny que o faz parar e ao número de sandes por iteração.

Quando o script para por causa de um shiny, o jogo está com os bebés eclodidos
mas **não gravado**. Grava tu antes de fazer mais alguma coisa.

## O que o script faz às boxes

1. **Antes do piquenique:** abre as boxes e passa para a K+1 (`R`). Os ovos ficam lá.
2. **Antes de eclodir:** arruma o piquenique, abre as boxes, volta à K (`L`) e:
   - troca o Talonflame (K, 1.ª casa) com o slot 1 da party, e o progenitor fica na K, 1.ª casa;
   - põe o Ditto (slot 2) na K, linha 1, coluna 2;
   - volta à K+1 (`R`).

   Fica na party só o Talonflame e 5 slots livres.
3. **Eclosão:** coluna a coluna, leva 5 ovos para a party, anda até eclodirem,
   verifica se há shinies (foto no Telegram) e devolve os bebés à coluna de onde vieram.

## Cuidados

- **Os ingredientes da sandes** são escolhidos por posição na lista (macro
  `make_sandwich_egg_power.json`), por isso não mudes o inventário de sítio.
- **Ao encontrar um shiny, nada é gravado.** No Full Auto, o script para (`sys.exit`)
  e és tu que tens de gravar.
- **"Restart" depois de eclodir no modo 1:** não repõe os progenitores na party
  (ficam na box K). Para outro lote, repõe a party à mão ou usa o Full Auto.
