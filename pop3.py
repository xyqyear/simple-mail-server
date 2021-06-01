import threading
import socket

from enum import Enum
from mailbox import db
from utils import recv_response
from typing import Tuple


class POP3State(Enum):
    AUTHORIZATION = 1
    TRANSACTION = 2
    UPDATE = 3


class POP3Command:
    def __init__(self,
                 raw_command: str,
                 command: str,
                 args: Tuple[int] = tuple()):
        self.raw_command = raw_command
        self.command = command
        self.args = args

    @classmethod
    def from_str(cls, raw_command: str) -> 'POP3Command':
        if ' ' in raw_command:
            command_split = raw_command.split(' ')
            return cls(raw_command, command_split[0].upper(),
                       tuple(map(lambda x: int(x), command_split[1:])))
        else:
            return cls(raw_command, raw_command.upper())


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

        self.dispatcher = {
            'QUIT': self._quit,
            'USER': self._user,
            'PASS': self._pass,
            'STAT': self._stat,
            'LIST': self._list,
            'RETR': self._retr,
            'DELE': self._dele,
            'NOOP': self._noop,
            'RSET': self._rset
        }

    def _recv_command(self) -> POP3Command:
        data = recv_response(self.connection)
        return POP3Command.from_str(data)

    def _quit(self, args: Tuple(int)):
        pass

    def _user(self, args: Tuple(int)):
        pass

    def _pass(self, args: Tuple(int)):
        pass

    def _stat(self, args: Tuple(int)):
        pass

    def _list(self, args: Tuple(int)):
        pass

    def _retr(self, args: Tuple(int)):
        pass

    def _dele(self, args: Tuple(int)):
        pass

    def _noop(self, args: Tuple(int)):
        pass

    def _rset(self, args: Tuple(int)):
        pass

    def run(self):
        while True:
            command = self._recv_command()
            self.dispatcher[command.command](command.args)
