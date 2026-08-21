# Brief — Switch 2 passa a ATACANTE (Iron Hands), combate guiado por VISÃO (não-cego)

> Para: sessão que trabalha no repo `C:\Projects\switch-arduino` (ficheiro
> `tera-raid-farmer/main.py` + `reference_images/` + `tera-raid-farmer/config.json`).
> Não mexas em `C:\Projects\auto-raid-switch1` — o lado da Switch 1 (host) é tratado noutra
> sessão. Este brief é só o teu lado.

## 0. Contexto e decisão (já fechada)

Setup de 2 consolas a farmar Tera Raids:

- **Switch 1** = HOST. Tem **sys-botbase** (IP `192.168.1.174:6000`). Injeta a seed, hospeda a
  raid e lê RAM. Corre a FSM Python em `auto-raid-switch1`. **Passa a fazer SUPORTE (Screech).**
- **Switch 2** = **Arduino/Joy-Con + visão** (capture card 4K). **Sem sys-botbase.** Corre este
  `tera-raid-farmer/main.py`. **Passa a ser o ATACANTE (Iron Hands).**

**Porquê a troca:** o Iron Hands atacante **já está treinado e pronto na Switch 2** (Lv100
Adamant, Tera Fighting, Belly Drum no move-slot 1, Drain Punch no move-slot 2). Não faz sentido
recriá-lo no host. Portanto o combate de ataque vive agora na Switch 2.

**A tua entrega:** transformar o macro de combate da Switch 2 de "suporte (Screech)" em
"atacante Iron Hands", **guiado por visão** — reagindo ao estado real do ecrã em vez de dormir
tempos fixos às cegas. O código atual (`use_screech_on_boss` / `handle_battle_and_rewards`) é
best-effort com `sleep`s — é isso que vamos substituir. A recolha de recompensas/captura no fim
**mantém-se** (a Switch 2 continua a apanhar o seu loot).

## 1. Contrato de combate (espelho do macro do host, já validado do lado da Switch 1)

O atacante tem de ganhar a raid com dano real (nada de pokes de HP — não funcionam em raid
hospedada). Sequência exata:

1. **Turno 1: Belly Drum** (move-slot `belly_drum_slot`, default 1). **Uma vez só** — já mete
   +6 Atk; repetir só cortava HP à toa.
2. **Turno 2+: Drain Punch** (move-slot `drain_punch_slot`, default 2), **em loop até o boss
   morrer**. Drain Punch também cura, por isso é seguro repetir indefinidamente.
3. **Tera (Fighting)**, se `use_tera` (default true): **re-tentar todos os turnos a partir do
   turno 2** até pegar. Em SV o Terastal é transformação única — voltar a carregar depois de já
   estar tera'd é no-op, logo é seguro tentar sempre. **Nunca no turno 1** (medidor vazio).
4. **Safety cap** (`combat_max_turns`, default 50): só para não ficar preso num loop infinito se
   a deteção de fim falhar. Se for atingido ainda em combate, **loga WARNING** ("boss não
   confirmado morto"), **não** finge vitória.

## 2. O mandato "não-cego": usa a visão que já existe

A stack de visão vive em `C:\Projects\switch-arduino\utils\pokemon_sv.py` e módulos irmãos.
Ferramentas disponíveis (coordenadas em **4K, 3840×2160**):

- `template_matcher.find_all(region, template_png, threshold=0.94, scales=(...))` — matching de
  imagem contra PNGs em `reference_images/`.
- `ocr.read_field(frame, x1, y1, x2, y2, invert=False, config=..., debug_name=...)` — Tesseract.
- `capture.region_is_mostly_color(x1,y1,x2,y2, "#rrggbb", ratio, tol)` e
  `capture.region_color_ratio(...)` — deteção por cor.
- `capture.get_latest_frame()`, `capture.get_frame_region(...)`, `capture.get_scaled_int(...)`.
- `_is_minimap_in_corner()` (em `pokemon_sv.py`) — deteta o minimapa por Hough → prova de que
  estás no **overworld** (usa isto para confirmar "raid acabou, voltei ao jogo").
- `_similarity(a, b)` — fuzzy match de strings (Levenshtein) para colar a saídas de OCR.
- `get_mode()` / `Mode` — máquina de estados de ecrã. **`Mode.IN_BATTLE = 3` está definido mas
  `get_mode()` nunca o devolve** (não há ramo que o compute). **Esse é o buraco a preencher.**

**Regra de ouro:** cada passo do macro espera pelo **estado visual esperado** antes de agir, e
faz **poll** entre turnos, em vez de `sleep(constante)`. Timeouts existem só como rede de
segurança (loga e sai, não finge).

## 3. Detetores de combate a construir (o coração do trabalho)

Adiciona funções (idealmente em `pokemon_sv.py`, ou num módulo novo tipo `raid_battle.py`) e os
respetivos PNGs em `reference_images/`. Detetores mínimos:

1. **`is_command_menu()`** — o menu "Fight / Bag / Pokémon / Run" está aberto e à espera do meu
   input (= é o meu turno). É o sinal para começar/continuar o macro. Template do botão "Fight"
   ou da moldura do menu de comando da raid.
2. **`is_move_select()`** — a lista dos 4 moves está aberta (para navegar até ao slot certo com
   confiança). Distingue-a do menu de comando.
3. **`tera_available()`** — o orbe/prompt de Terastal está aceso e disponível (medidor cheio).
   Enquanto for False, não gastes o turno a carregar no botão de Tera.
4. **`battle_result()` / `boss_defeated()`** — deteta o fim: ecrã de resultado da raid
   ("Caught!"/captura, écran de recompensas) **ou** regresso ao overworld
   (`_is_minimap_in_corner()`). É o sinal para **parar** o loop de Drain Punch.
5. **`battle_lost()`** — deteta derrota/tempo esgotado (foste KO, "You were blown out of the
   raid", timer a zero) para abortar de forma limpa e não ficar preso.

Para cada um: recorta a região no feed 4K, tira um PNG de referência, e valida `threshold`.
Segue o padrão exato dos detetores já existentes no `get_mode()` (mesmos helpers, mesmas escalas).

**Depois liga isto ao `get_mode()`**: adiciona um ramo que devolve `Mode.IN_BATTLE` quando
`is_command_menu()` (ou `is_move_select()`) é verdadeiro, para o resto do código passar a ver o
estado de combate corretamente.

## 4. Botões (primitivas Arduino já no repo)

`click(token, sleep=..., repeat=...)` de `controller`/`switch`. Mapeamento do D-Pad já usado
neste ficheiro (`type_link_code_on_keyboard`):

- `"1"` = D-Pad **Direita**, `"2"` = **Baixo**, `"3"` = **Esquerda**, `"4"` = **Cima**.
- `"A"` = confirmar, `"B"` = cancelar/voltar, `"X"` = menu, `"+"` = confirmar teclado/menu,
  `"H"` = Home.

**Navegar um move:** menu comando → `A` (Fight) → `A` (abre lista) → `2`×(slot−1) (desce até ao
slot) → `A` (seleciona) → `A` (confirma alvo; em raid o boss é auto-alvo, 1 confirm chega).
**Confirma o layout real live** — o nº de confirmações pode diferir.

**Tera:** o host fá-lo com `click R` no menu de comando (mesmo contrato). Do teu lado, confirma
**live** qual é o botão de Terastal na raid e que `controller.click` aceita esse token
(`"R"`/`"ZR"`/…); se não aceitar, adiciona-o na camada do controller. Idempotente: se já estás
tera'd, voltar a carregar não faz mal.

## 5. Opção recomendada — pedir a "morte do boss" ao host (híbrido visão + flag)

A Switch 1 (host) **continua a ler a flag de combate por RAM** (`peekMain 0x047D2020 == 0x02`),
que é a fonte **autoritativa** de "o boss ainda está vivo". Em vez de deteres a morte do boss
só por visão (mais frágil), podes **perguntar ao host** pela HTTP API que já usas
(`query_host_api`, base `host_api_url`, hoje `http://127.0.0.1:8080`).

Proposta: o host expõe um endpoint tipo `GET /api/battle_state` → `{"in_battle": true/false}`
(derivado da flag). A Switch 2 usa **visão para os turnos** (quando é o meu turno, navegar
moves, Tera disponível) e o **flag do host para a condição de paragem** (parar de dar Drain
Punch quando `in_battle` vira false). Isto reduz muito o peso da visão e é bem mais robusto.
> Nota: esse endpoint é do lado do host (a outra sessão / eu adiciono-o em `auto-raid-switch1`).
> Assume-o como **opcional**: se não existir, cai para deteção por visão do §3.4. Não bloqueies
> o teu trabalho à espera dele.

## 6. Config (`tera-raid-farmer/config.json`) — chaves a acrescentar/renomear

```jsonc
{
  // ... chaves existentes (host_api_url, poll_interval_sec, rewards_mash_count, auto_catch) ...
  "belly_drum_slot": 1,        // move-slot do Belly Drum
  "drain_punch_slot": 2,       // move-slot do Drain Punch
  "use_tera": true,
  "combat_max_turns": 50,      // safety cap anti-loop-infinito
  "turn_poll_timeout_sec": 20, // espera máx. por "é o meu turno" antes de logar e reavaliar
  "move_select_delay_sec": 0.6 // manter; calibrar live
}
```
Remove/deprecia `screech_move_slot` (o Screech passou para a Switch 1). Mantém `auto_catch` e a
recolha de recompensas/captura no fim — a Switch 2 continua a apanhar o loot.

## 7. Orquestração (não partir)

O handshake atual mantém-se: polling `GET /api/lobby` → entrar no lobby (`enter_tera_raid_lobby`)
→ `POST /api/action {"action":"start_battle"}` (`signal_host_ready`) → combate → recompensas.
Só o **bloco de combate** (`handle_battle_and_rewards` / `use_screech_on_boss`) é que muda para
o macro atacante guiado por visão.

## 8. Entregáveis

1. Detetores de combate do §3 (+ PNGs em `reference_images/`) e o ramo `Mode.IN_BATTLE` no
   `get_mode()`.
2. Novo macro atacante (substitui `use_screech_on_boss` por algo tipo
   `run_attacker_macro(...)`): Belly Drum turno 1 → Drain Punch em loop até morte → Tera todos
   os turnos ≥2 até pegar → safety cap com WARNING. Guiado pelos detetores, não por sleeps.
3. `handle_battle_and_rewards` reescrito para chamar o macro atacante e manter
   captura/recompensas no fim.
4. Config do §6.
5. (Se fizeres o híbrido) consumir `GET /api/battle_state` com fallback para visão.

## 9. Testes

- **Offline:** onde der, testa a lógica de decisão com frames/dumps gravados do capture card
  (fotos dos estados: menu de comando, lista de moves, Tera disponível, ecrã de resultado,
  overworld). Valida thresholds dos templates contra esses PNGs.
- **Live (bloqueado até haver raid a sério):** timings de navegação, botão de Tera, e afinar
  thresholds no feed real. Marca claramente o que ficou por validar live.

## 10. Não fazer

- Não mexer em `C:\Projects\auto-raid-switch1` (host é outra sessão).
- Não assumir sys-botbase na Switch 2 (não tem) — nada de peek/poke/flags por RAM deste lado.
- Não voltar a pôr Screech aqui (foi para a Switch 1).
- Não substituir a visão por `sleep`s fixos "para ir andando" — o objetivo é precisamente o
  contrário: combate reativo ao estado do ecrã.
