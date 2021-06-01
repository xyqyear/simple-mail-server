import threading
import socket

from enum import Enum
from mailbox import db


class POP3State(Enum):
    AUTHORIZATION = 1
    TRANSACTION = 2
    UPDATE = 3


class POP3Server:
    def __init__(self, username, password):
        self.username = username
        self.password = password

    def run(self):
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.bind(('0.0.0.0', 110))
        s.listen()
        while True:
            conn, _ = s.accept()
            thread = POP3ServerThread(conn, self)
            thread.run()


class POP3ServerThread(threading.Thread):
    def __init__(self, connection: socket.socket, server: POP3Server):
        super().__init__()
        self.connection = connection
        self.server = server
        self.state = POP3State.AUTHORIZATION

        db.aquire()

    def run(self):
        pass
