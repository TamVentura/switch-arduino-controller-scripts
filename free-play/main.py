"""
free-play/main.py — abre a janela de captura e o passthrough do controlador,
sem automação nenhuma a correr.

    python free-play/main.py
    python free-play/main.py COM8   # forçar o porto serie

Serve de ponto de partida: o `main()` fica em espera para o processo não morrer,
e o código de automação entra aqui quando houver algum.

Na janela de captura:
    q     fecha (termina o script)
    v     mostra/esconde a janela de selecao do controlador
    Tab   liga/desliga o controlo por teclado (mapa em controls.txt)
    s     screenshot
    t     liga/desliga a gravacao
"""

import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from switch import capture, launch  # noqa: E402

port = sys.argv[1] if len(sys.argv) > 1 else None


def main():
    # Nada automatizado por agora — só manter o processo vivo enquanto a
    # janela de captura estiver aberta, para poder jogar à mão.
    while not capture.is_stopped():
        time.sleep(0.2)


if __name__ == "__main__":
    launch(main, port=port)
