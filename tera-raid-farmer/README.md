# 🎮 Pokémon SV Tera Raid Farmer (Switch 2 via Arduino/Joy-Con)

Automação para a **Switch 2 (Farmer)** utilizando emulação de comando/Joy-Con por Arduino (`switch-arduino`). Sincroniza diretamente com a API do **Host Wrapper (Switch 1)** para entrar automaticamente nas raids abertas e recolher Pokémon/Herba Mystica em loop infinito.

---

## ⚡ Como Funciona

> **Sem polling.** A comunicação com o host é toda por **push WebSocket** (`GET /ws` da
> Switch 1). O host empurra o lobby assim que abre e o estado do FSM assim que muda
> (`IN_BATTLE` → `BATTLE_REWARDS` quando o boss cai). Ver `host_link.py`. As únicas
> chamadas HTTP que restam são POSTs one-shot (`start_battle`, `caught`) — eventos, não polling.

1. **Push do lobby:** liga-se a `ws://<HOST_IP>:8080/ws` e espera que o host empurre um lobby aberto (código de 4 caracteres).
2. **Navegação Automática:** Ao receber o código, o script abre o Poké Portal -> Tera Raid Battles.
3. **Digitação no Teclado Virtual:** Navega com precisão no teclado do Switch via comandos D-Pad e insere o código de 4 dígitos.
4. **Sinal de Início:** Notifica a Switch 1 via `POST /api/action` (`start_battle`) assim que confirma "Ready!".
5. **Macro Atacante (Iron Hands, guiado por visão):** a Switch 2 é o **ATACANTE**. O macro reage
   ao estado do ecrã (menu de comando, lista de moves, orbe de Tera, ecrã de resultado) em vez de
   dormir tempos fixos: **Belly Drum** no turno 1 → **Drain Punch** em loop até o boss morrer →
   **Tera (Fighting)** a partir do turno 2 até pegar. Condição de paragem autoritativa: a flag do
   host em `GET /api/battle_state`, com fallback para visão. Ver `attacker.py`.
6. **Loot:** no fim, captura (se `auto_catch`) e limpa os ecrãs de recompensa.

> ⚠️ **Fase 2 — calibração de visão pendente.** As deteções de combate têm coordenadas
> *placeholder* (a Switch 2 estava offline). O macro só combate depois de calibradas. Ver
> [`CALIBRATION.md`](CALIBRATION.md).

---

## ⚙️ Configuração (`config.json`)

```json
{
  "host_api_url": "http://127.0.0.1:8080",
  "poll_interval_sec": 1.0,
  "rewards_mash_count": 25,
  "auto_catch": true,
  "belly_drum_slot": 1,
  "drain_punch_slot": 2,
  "use_tera": true,
  "tera_button": "R",
  "combat_max_turns": 50,
  "turn_poll_timeout_sec": 20,
  "battle_start_timeout_sec": 45,
  "move_select_delay_sec": 0.6,
  "com_port": "COM6",
  "baudrate": 115200
}
```

| Chave | Efeito |
|---|---|
| `belly_drum_slot` / `drain_punch_slot` | Slot (1-indexado) dos moves na lista de Fight |
| `use_tera` / `tera_button` | Terastalizar (Fighting) a partir do turno 2; token do controller |
| `combat_max_turns` | Rede de segurança anti-loop-infinito (loga WARNING, não finge vitória) |
| `turn_poll_timeout_sec` | Espera máx. por "é o meu turno" por turno |
| `battle_start_timeout_sec` | Espera máx. até o combate começar |
| `move_select_delay_sec` | Atraso entre botões na navegação dos menus (**calibrar live**) |

---

## 🧪 Testes

Lógica de decisão do macro (sem hardware):
```bash
python -m pytest tera-raid-farmer/tests/ -q
```

---

## 🚀 Como Executar

No diretório `C:\Projects\switch-arduino`:
```bash
python tera-raid-farmer/main.py
```
