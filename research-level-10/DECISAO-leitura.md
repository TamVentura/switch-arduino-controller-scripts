# Como ler o ecrã: Haiku vs algoritmo Python

Pergunta dele (2026-09-04): *"Não quero ter erros na leitura disto de todo. Achas
que usar agentes em haiku para ler cada mon é a melhor opção ou usar um
algoritmo python para extrair a info?"*

## Decisão proposta

**Haiku lê os pixels; Python conduz a navegação e verifica.** O que garante a
ausência de erros é a **verificação**, não a escolha do modelo.

## Teste feito ao vivo

Três agentes Haiku 4.5, um por Pokémon, com o mesmo prompt, sobre screenshots
reais tiradas pelo `free-play` (`/shot?zone=90,218,2740,1900`). Casos escolhidos
por serem os que partem heurísticas de coluna:

| mon | caso | resultado |
|---|---|---|
| Porygon-Z | patamares desalinhados entre linhas (`1·3·8·20·40` vs `1·3·6·12·25`) | perfeito |
| Remoraid | `Number you've evolved` com **um só pill**, encostado à direita | perfeito, `steps: [1]` |
| Empoleon | linha com **3 pills right-aligned**, e 3 linhas de bónus | perfeito, `[1,2,3]`, sem inventar 5 valores |

**3/3 sem um erro** em nomes, patamares, valores actuais, vistos, flags de bónus
e contagem de linhas vazias. Ground truth: leitura manual das mesmas imagens.

Custo medido: **36 200 / 36 282 / 36 657 tokens** por leitura (~36,4k), dos quais
~1 490 são a imagem; o resto é overhead do agente. A $1/MTok de input em Haiku
4.5, **~$0,04-0,05 por leitura**.

- tarefas + habitat para os 54 que faltam: **~$5**
- **tudo em duplicado**, aceitando só onde as duas leituras concordam: **~$11**

## Porque não um algoritmo Python a extrair

- **Metade do que é preciso não é texto.** "Tem chevrons?", "que pills têm
  visto?", "que ícones de condição estão acesos?" é visão/template matching, não
  OCR.
- **Os pills são right-aligned com número variável de passos.** Qualquer lógica
  de "coluna 1 a 5" parte-se — o Empoleon tem uma linha de 3 e o Remoraid uma de
  1. Já visto no ecrã, não é hipotético.
- **Um erro do Python é silencioso.** Prova da própria sessão: escrevi um
  detector rápido para saber qual das 3 abas estava aberta e ele respondeu
  "aba 1" três vezes seguidas, com confiança, estando errado. Só olhando para a
  imagem se viu.
- Os nomes e patamares mudam por espécie, logo não há template fixo onde encostar.

## Porque o Haiku também não basta sozinho

3/3 é evidência, não prova. São ~54 mons x ~7 tarefas = ~380 linhas; a taxa de
erro que interessa é de 1 em centenas e três amostras não a medem.

## A verificação que compra o "sem erros"

O ecrã tem redundância suficiente para se auto-verificar:

1. **Invariante por linha:** os passos com visto têm de ser **exactamente** os
   `<= current`. Apanha um dígito mal lido ou um visto perdido. Determinístico,
   custo zero.
2. **Cruzar com o research level:** `soma(passos feitos x (2 se bónus))` tem de
   dar o nível mostrado na caixa. Calibrando a tabela pontos->nível com os mons
   já lidos, fica um checksum por mon.
3. **Diff de pixels entre painéis consecutivos:** se um d-pad não registar, o
   painel repete-se. Apanha sem OCR o pior erro de todos — ler o mesmo mon duas
   vezes e saltar outro.
4. **Reler quando um check falha**, e escalar para o Opus quando duas leituras
   divergem. A $0,04 a leitura, reler é grátis.

## Nota para poupar em passagens futuras

Nomes de tarefas, patamares e habitat são **dados estáticos da espécie** — não
mudam com o save. Depois da primeira passagem completa só é preciso reler o
**valor actual** de cada tarefa, o que torna as actualizações muito mais baratas.
