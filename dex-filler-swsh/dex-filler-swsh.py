import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "utils"))
from telegram_notifier import TelegramNotifier
from controller import *


start_box = 2
start_col = 2
start_row = 1

end_box = 14
end_col = 4
end_row = 3
notifier = TelegramNotifier(
    bot_token="8270624295:AAF6vixgzXNJq6FV-7jsOiDcewm1V2POgUQ",
    chat_id="1789658618",
)


def main():
    global start_col, start_row
    connect()
    click("-", 1)
    global box, row, col
    box = 0
    row = 0
    col = 0

    notifier.register_command(
        "status", send_cmd_status, "Show the status of the dex filler"
    )
    notifier.start_command_listener()
    for box in range(start_box, end_box + 1):
        for row in range(start_row, 6 if box != end_box else (end_row + 1)):
            for col in range(
                start_col, 7 if box != end_box or row != end_row else (end_col + 1)
            ):
                click("1")
                click("A", 0.5, 20)
                time.sleep(30)
            print(f"EndRow {col}")
            start_col = 0
            click("1",1)
            click("2",1)
        start_row = 0
        click("1")
        click("2", 0.5, 2)
        click("3")
        notifier.send_message(
            f"Finalizado caixa {box}. {box-start_box+1}/{end_box - start_box + 1} caixas completas"
        )
    notifier.send_message(f"Finalizado")


def send_cmd_status(_: str):
    notifier.send_message(f"Caixa {box}, Linha {row}, Coluna {col}")


if __name__ == "__main__":
    main()
