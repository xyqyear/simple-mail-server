import threading
import logging

import config

from pop3 import POP3Server
from smtp import SMTPServer

logging.basicConfig()
logging.getLogger().setLevel(logging.INFO)


def main():
    smtp_server = SMTPServer(config.domain, config.username, config.password)
    pop3_server = POP3Server(f'{config.username}@{config.domain}',
                             config.password)

    smtp_server_main_thread = threading.Thread(target=smtp_server.run)
    pop3_server_main_thread = threading.Thread(target=pop3_server.run)

    smtp_server_main_thread.start()
    pop3_server_main_thread.start()

    smtp_server_main_thread.join()
    pop3_server_main_thread.join()


if __name__ == '__main__':
    main()
