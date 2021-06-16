import threading
import logging

import configparser

from pop3 import POP3Server
from smtp import SMTPServer

logging.basicConfig()
logging.getLogger().setLevel(logging.INFO)


def main():
    config = configparser.ConfigParser()
    config.read("config.ini")

    smtp_server = SMTPServer(config['config']['domain'],
                             config['config']['username'],
                             config['config']['password'])
    pop3_server = POP3Server(
        f"{config['config']['username']}@{config['config']['domain']}",
        config['config']['password'])

    smtp_server_main_thread = threading.Thread(target=smtp_server.run)
    pop3_server_main_thread = threading.Thread(target=pop3_server.run)

    smtp_server_main_thread.start()
    pop3_server_main_thread.start()

    smtp_server_main_thread.join()
    pop3_server_main_thread.join()


if __name__ == '__main__':
    main()
