"""
free-play/main.py — abre a janela de captura e o passthrough do controlador,
sem automação nenhuma a correr, mais um canal HTTP local para conduzir a
sessão de fora (screenshots, botões, sticks, ler zonas selecionadas).

    python free-play/main.py
    python free-play/main.py COM8   # forçar o porto serie

O `main()` fica só à espera: o jogo é teu até alguém mandar um comando por
    curl "http://127.0.0.1:8777/shot"
    curl "http://127.0.0.1:8777/press?keys=A,4&sleep=0.4"
Cada comando recebido aparece na consola (linhas `claude:`), e a aba do
browser em http://127.0.0.1:8777/ mostra a última screenshot tirada e o
último recorte selecionado.

Na janela de captura:
    q     fecha (termina o script)
    v     mostra/esconde a janela de selecao do controlador
    Tab   liga/desliga o controlo por teclado (mapa em controls.txt)
    s     screenshot   |   f  grava o recorte selecionado
    botão direito -> clique   seleciona uma zona (coordenadas para o /shot?zone=)
"""

import os
import sys
import time
import webbrowser

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from switch import capture, launch  # noqa: E402

import control_server  # noqa: E402

port = sys.argv[1] if len(sys.argv) > 1 else None
OPEN_TAB = "--no-tab" not in sys.argv


def main():
    url = control_server.start()
    print(f"Control server: {url}")
    if OPEN_TAB:
        webbrowser.open(url)

    # Nada automatizado por agora — só manter o processo vivo enquanto a
    # janela de captura estiver aberta, para poder jogar à mão. Os comandos
    # que chegam ao servidor correm nas threads dele.
    while not capture.is_stopped():
        time.sleep(0.2)

    control_server.stop()


if __name__ == "__main__":
    launch(main, port=port)
