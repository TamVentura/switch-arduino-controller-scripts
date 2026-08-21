# Handoff — Fase 2 (passagem LIVE de timing de botões)

Para uma sessão nova no repo `C:\Projects\switch-arduino`. Contexto: automação de
Tera Raids com 2 consolas. A **Switch 2** (Arduino/Joy-Con + captura Elgato 1080p,
sem sys-botbase) é o **ATACANTE** (Iron Hands: Belly Drum slot 1, Drain Punch slot 2,
Tera Fighting). Corre `tera-raid-farmer/main.py`. A **Switch 1** é o host/suporte e
empurra estado por WebSocket `/ws` (projeto `C:\Projects\auto-raid-switch1` — **não
mexer**).

## O que já está feito (fase 1 + calibração de visão da fase 2)

- **Macro atacante** (`tera-raid-farmer/attacker.py`): Belly Drum turno 1 → Drain
  Punch em loop até o boss morrer → Tera (R) a partir do turno 2 → safety cap com
  WARNING (nunca finge vitória). Reativo à visão, sem sleeps fixos. Paragem pela
  flag do host (`/ws` `fsm_state==IN_BATTLE`, via `host_link.py`) com fallback para
  visão. **Sem polling HTTP.**
- **Detetores de visão** (`utils/pokemon_sv.py`, `_BATTLE_DETECTORS_CALIBRATED=True`),
  validados contra frames reais (templates em `reference_images/raid_*.png`):
  - `is_command_menu` (botão "Battle") — POS 0.98–1.0, NEG 0.0
  - `is_move_select` ("Move Info") — POS 0.996–1.0, NEG ≤0.74
  - `tera_available` ("TERASTALLIZE!") — POS 1.0 (só 1 frame; confirmar)
  - `battle_ended` (resultado "Rewards" + minimapa) — POS 1.0, NEG 0.0
  - `get_mode()` já devolve `Mode.IN_BATTLE`.
- **Captura com Timer Ball** (`attacker.catch_pokemon`): aceita "Catch", navega o
  carrossel de bolas por OCR do nome centrado até "Timer Ball", A para lançar.
  Config `catch_ball` (default "Timer Ball").
- **41 testes** de lógica pura passam: `python -m pytest tera-raid-farmer/tests/ -q`.

Factos confirmados live: menu de raid é **Battle/Cheer/Run** (1×A abre moves);
**Tera=R** e o prompt aparece **no ecrã de moves**; carrossel de bolas navega-se
com D-pad Direita (`"1"`), nome por OCR (região 4K 1720,1476,2340,1590).

## Tooling para "eu vejo, tu conduzes"

`python tera-raid-farmer/calibrate.py` (num terminal do utilizador, não no `!`):
abre a janela **Capture** + passthrough Xbox (ambos opcionais/robustos), grava a
frame atual em `tera-raid-farmer/calib_frames/live.png` a cada ~0.7s, arquiva cada
ecrã **distinto** em `calib_frames/<HHMMSS_mmm>.png`, e loga em `calibrate.log`.
- O assistente "vê" lendo `calib_frames/live.png` (tool Read lê imagens).
- Para varrer muitos frames de uma vez: montar uma **folha de contacto** (grelha de
  thumbnails com cv2) e ler numa só imagem; filtrar ecrãs estáticos pelo intervalo
  de tempo até ao frame seguinte (menu = hold longo).
- **Coordenadas:** o feed é **1080p**; o código usa **4K** (auto-escala ×0.5). Um
  pixel (x,y) medido num frame → 4K = (2x, 2y).
- **Validar um detetor:** cortar o template do frame positivo, correr
  `template_matcher.find_all(region, tpl, threshold=0.4, scales=(1,))` contra frames
  POSITIVOS (deve dar score alto) e NEGATIVOS (deve dar ~0), e fixar o threshold.
  Integração real: pôr `capture._latest_frame = cv2.imread(frame)` e chamar as
  funções de `pokemon_sv` diretamente.

## ESTADO (atualizado 2026-08-19)

- **Item 5 (keypad + navegação de lobby) — FEITO e verificado ao vivo.** Ver secção
  "Lobby entry (item 5)" no `CALIBRATION.md`. Resumo: navegação dos menus agora é
  dinâmica (`goto_menu_option(PORTAL)` + o novo `goto_portal_option(TERA_RAID)`/
  `get_portal_option` em `pokemon_sv.py`); o keypad é numérico 3×4 (novo
  `type_link_code_numeric` em `main.py`, QWERTY removido); `+`=OK e já pesquisa.
  41 testes passam.
- **Item 1 (navegação de moves) — REDESENHADO 2026-08-19 (raid real).** Descoberta
  chave: o combate de raid é **tempo-real** — o boss age sozinho e as ações dele
  **fecham o menu** a meio. Uma sequência cega de botões dessincroniza e re-lança Belly
  Drum (o default é Battle→slot 1). Reescrito para **navegação guiada por visão e
  auto-corretiva**: novo `pokemon_sv.get_move_slot` (rácio de gold `#d3b013`, tol 55,
  por slot, calibrado de frames reais) + `attacker.open_move_list`/`goto_move_slot`/
  `commit_move` e um `take_turn` em máquina de estados com retries limitados. Coberto
  por testes (incl. recuperação de interrupções do boss). Design em
  `docs/superpowers/specs/2026-08-19-raid-combat-vision-gated-navigation-design.md`.
  **55 testes passam.** Falta a confirmação ao vivo (slots 2-4 do detetor + máquina
  completa).
- **Itens 2-4 (Tera / captura / derrota) — POR CONFIRMAR AO VIVO.** Precisam de uma
  **raid a sério** com o host a empurrar a lobby. **Switch 2 fica OFFLINE** (a junção é
  por link-code local — a nota antiga de que era preciso "online" estava ERRADA;
  confirmado ao vivo a juntar o código 2303 com a Switch 2 em Offline Mode).

### Ferramenta de calibração (TEMP — apagar no fim da fase)
- `tera-raid-farmer/calib_drive.py` — servidor de comandos por ficheiros: detém
  COM6 + captura, abre a janela "Capture", e executa comandos largados em
  `calib_frames/q/` (`click TOKEN [sleep] [repeat]`, `hold`, `sleep`, `shot LABEL`,
  `burst`, `stop`). Screenshots → `calib_frames/shot_<label>_<t>.png`; heartbeat
  ~2s → `live.png`; log → `calib_frames/drive.log`. O assistente escreve ficheiros
  de comando e lê as screenshots. **Correr num terminal do utilizador** (abre GUI).
  Como calib_drive e main.py não podem partilhar a Elgato (DSHOW), usa-se um OU outro.
- `main.py` tem bits `TEMP-CALIB`: a thread `_calib_frame_dumper` (dá olhos ao
  assistente via `live.png` enquanto o main.py conduz) e um entrypoint de teste
  `--enter <code>` (`_test_enter_lobby`) que corre o `launch()`+`enter_tera_raid_lobby`
  reais sem host e mantém a janela aberta. **Apagar tudo o que está marcado TEMP-CALIB
  no fim da fase** (o `calib_drive.py` inteiro + estes bits no `main.py`).
- **Gotchas ao lançar Python em background aqui:** usar `PYTHONIOENCODING=utf-8` (o
  stdout de um processo em background é cp1252 e rebenta com `→`/emojis nos prints)
  e `PYTHONBREAKPOINT=0` (o `goto_menu_option` tem um `breakpoint()` perdido).
  Ex.: `PYTHONIOENCODING=utf-8 PYTHONBREAKPOINT=0 python tera-raid-farmer/main.py --enter 9015`.

### Como retomar o combate (nova sessão)
1. Ler `CALIBRATION.md` (fonte de verdade) e este handoff.
2. Switch 2 **online** (obrigatório para juntar — hoje estava "Offline Mode") + host
   a criar/empurrar uma raid.
3. Escolher: (a) conduzir via `calib_drive` (control fino + screenshots on-demand; os
   ecrãs de moves/catch esperam por input, sem pressão de timer); ou (b) `main.py`
   autónomo (entra pelo push do host e luta) observando pelo dumper. Para testar só a
   entrada: `python main.py --enter <code>`.

## O QUE ESTA PASSAGEM LIVE PRECISA DE VERIFICAR/AFINAR

Requisitos: Switch 2 ligada, em Pokémon Violet, feed Elgato OK; Arduino em `COM6`
(ver `config.json`); idealmente o host a correr e a empurrar `/ws` (senão o macro
cai para visão). É preciso uma raid a sério para atacar. A ideia é deixar o
**`main.py` conduzir** (não o utilizador) e afinar timings observando `live.png`.

1. **Navegação de moves (`attacker.take_turn`)** — ✅ REDESENHADO (ver ESTADO acima).
   Já não depende de wrap nem de contar A's: `goto_move_slot` lê o slot destacado
   (`get_move_slot`) e move pelo delta exato; `commit_move` confirma pela saída da
   lista. Falta ao vivo: confirmar `get_move_slot` nos slots 2-4 e a máquina completa
   sob interrupções reais do boss.
2. **Tera (`pokemon_sv.tera_available`)** — só houve 1 frame positivo (o glow verde
   anima). Capturar mais frames com/sem o prompt "TERASTALLIZE!", confirmar o
   threshold (atual 0.80) e que carregar **R** terastaliza mesmo (idempotente).
3. **Captura Timer Ball (`attacker.catch_pokemon`)** — confirmar que: o A que aceita
   "Catch" **não faz throw** antes de o carrossel ser detetado (`at_ball_select`);
   que Direita (`"1"`) move o carrossel; que a OCR do nome acerta na Timer Ball; e
   que o prompt de **alcunha** no fim é dispensado (No). Afinar `move_select_delay_sec`,
   `catch_timeout_sec`, `ball_carousel_max_steps`.
4. **`battle_lost` / vitória-vs-derrota** — MECANISMO CONFIRMADO (2026-08-19): ao
   **perder, volta ao overworld sozinho, sem prompt de Catch nem recompensas**. O FSM
   do host não tem estado de derrota, por isso a derrota é um facto de visão do lado da
   Switch 2. Bug atual: host sai de IN_BATTLE numa derrota → `boss_defeated` True → falsa
   vitória → `main.py` corre a captura e faz `signal_caught` de um mon que não apanhou.
   Design correto: distinguir pelo ecrã pós-batalha (VITÓRIA → prompt/carrossel/
   recompensas; DERROTA → direto ao overworld). É um detetor de **sequência** — precisa
   de capturar uma DERROTA ao vivo (e o frame do prompt de Catch da vitória) para o fazer
   em segurança. NÃO implementado às cegas (uma falsa derrota saltaria uma captura real).
   Inerte por agora (seguro). **TODO ao vivo: perder uma raid de propósito para apanhar a
   cauda de derrota.**
5. **Teclado do link-code (`enter_tera_raid_lobby`)** — ✅ **FEITO 2026-08-19.**
   Teclado numérico 3×4 mapeado e `type_link_code_numeric` escrito+testado; navegação
   dos menus agora dinâmica. Ver "Lobby entry (item 5)" no `CALIBRATION.md`. Falta só,
   quando houver raid online, confirmar a cauda online (join/lobby/"Ready!") — dobra
   nos itens 1-3.

## Ficheiros-chave
- `tera-raid-farmer/main.py` — orquestração (WS lobby → entrar → sinalizar host →
  macro → captura/recompensas). `attacker.py` — macro + `take_turn` + `catch_pokemon`
  + `real_io`/`BattleIO` (seams injetáveis, por isso os testes correm sem hardware).
  `host_link.py` — cliente WS. `calibrate.py` — tooling live. `config.json`.
- `utils/pokemon_sv.py` — detetores + `get_mode()`. `utils/{capture,controller,
  template_matcher,ocr}.py` — primitivas (coords em 4K, auto-escaladas).
- `docs/superpowers/specs/2026-08-18-switch2-attacker-vision-design.md` — design.
- `tera-raid-farmer/CALIBRATION.md` — estado da calibração (fonte de verdade).

## Botões (tokens `controller.click`)
D-pad: `"1"`=Direita, `"2"`=Baixo, `"3"`=Esquerda, `"4"`=Cima. `"A"` confirma,
`"B"` cancela, `"R"`=Terastal, `"+"` confirma teclado. `click(token, sleep=, repeat=)`.

## Não fazer
Não mexer em `auto-raid-switch1`. Não reintroduzir polling HTTP nem sleeps fixos no
combate. Não assumir sys-botbase na Switch 2.
