import threading
import logging
import socket
import sys

from enum import Enum
from mailbox import db
from utils import recv_response
from typing import Tuple, Union


class POP3State(Enum):
    AUTHORIZATION = 1
    TRANSACTION = 2


class POP3Command:
    def __init__(self,
                 raw_command: str,
                 command: str,
                 args: Tuple[str] = tuple()):
        self.raw_command = raw_command
        self.command = command
        self.args = args

    @classmethod
    def from_str(cls, raw_command: str) -> 'POP3Command':
        if ' ' in raw_command:
            command_split = raw_command.split(' ')
            return cls(raw_command, command_split[0].upper(),
                       command_split[1:])
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
        logging.info(f'POP3Server is now running')
        while True:
            conn, address = s.accept()
            logging.info(f'POP3Server accepted new connection from {address}')
            thread = POP3ServerThread(conn, self)
            thread.start()


class POP3ServerThread(threading.Thread):
    def __init__(self, connection: socket.socket, server: POP3Server):
        super().__init__()
        self._connection = connection
        self._server = server
        self._state = POP3State.AUTHORIZATION

        self._connection.settimeout(10)

        self._dispatcher = {
            'QUIT': self._quit,
            'USER': self._user,
            'PASS': self._pass,
            'STAT': self._stat,
            'LIST': self._list,
            'RETR': self._retr,
            'DELE': self._dele,
            'NOOP': self._noop,
            'RSET': self._rset,
            'TOP': self._top,
        }

        self._command_state = {
            'QUIT': (POP3State.AUTHORIZATION, POP3State.TRANSACTION),
            'USER': (POP3State.AUTHORIZATION, ),
            'PASS': (POP3State.AUTHORIZATION, ),
            'STAT': (POP3State.TRANSACTION, ),
            'LIST': (POP3State.TRANSACTION, ),
            'RETR': (POP3State.TRANSACTION, ),
            'DELE': (POP3State.TRANSACTION, ),
            'NOOP': (POP3State.TRANSACTION, ),
            'RSET': (POP3State.TRANSACTION, ),
            'TOP': (POP3State.TRANSACTION, ),
        }

        self._got_username = False

    def _dispatch(self, command: POP3Command) -> Union[bool, None]:
        if command.command in self._dispatcher and \
           self._state in self._command_state[command.command]:
            return self._dispatcher[command.command](command.args)
        else:
            self._send_err()

    def _recv_command(self) -> POP3Command:
        try:
            data = recv_response(self._connection)
            logging.info(
                f'POP3ServerThread received command from {self._connection.getpeername()}: {data}'
            )
            return POP3Command.from_str(data)
        except Exception:
            self._exit()

    def _quit(self, args: Tuple[str]) -> Union[bool, None]:
        self._send_ok()
        return True

    def _user(self, args: Tuple[str]) -> Union[bool, None]:
        if len(args) == 1 and args[0] == self._server.username:
            self._got_username = True
            self._send_ok()
        else:
            self._got_username = False
            self._send_err()

    def _pass(self, args: Tuple[str]) -> Union[bool, None]:
        if len(args) == 1 and \
           self._got_username and \
           args[0] == self._server.password:
            self._send_ok()
            # ! where the state changes
            self._state = POP3State.TRANSACTION
            db.aquire()
        else:
            self._send_err()

    def _stat(self, args: Tuple[str]) -> Union[bool, None]:
        message_num, message_length = db.get_stat()
        self._send_ok(f'{message_num} {message_length}')

    def _list(self, args: Tuple[str]) -> Union[bool, None]:
        if len(args) == 0:
            message_length_list = db.get_message_length_list()
            response = f'{len(message_length_list)} messages\r\n'
            for i, length in message_length_list:
                response += f'{i} {length}\r\n'
            response += '.'

            self._send_ok(response)

        elif len(args) == 1:
            try:
                message_length = db.get_message_length_with_id(int(args[0]))
                self._send_ok(f'{args[0]} {message_length}')
            except Exception as e:
                self._send_err(str(e))

        else:
            self._send_err()

    def _retr(self, args: Tuple[str]) -> Union[bool, None]:
        if len(args) == 1:
            try:
                message = db.get_message_with_id(int(args[0]))
                self._send_ok(f'{len(message)} octets\r\n{message}\r\n.')
            except Exception as e:
                self._send_err(str(e))
        else:
            self._send_err()

    def _dele(self, args: Tuple[str]) -> Union[bool, None]:
        if len(args) == 1:
            try:
                db.delete_message_with_id(int(args[0]))
                self._send_ok()
            except Exception as e:
                self._send_err(str(e))
        else:
            self._send_err()

    def _noop(self, args: Tuple[str]) -> Union[bool, None]:
        self._send_ok()

    def _rset(self, args: Tuple[str]) -> Union[bool, None]:
        db.reset_messages()
        self._send_ok()

    def _top(self, args: Tuple[str]) -> Union[bool, None]:
        if len(args) == 2 and args[0].isdecimal() and args[1].isdecimal():
            response = '\r\n'

            msg_id = int(args[0])
            line_num = int(args[1])

            message = db.get_message_with_id(msg_id)
            total_line_num = message.count('\r\n') + 1

            if line_num >= total_line_num:
                response += message
            else:
                response += '\r\n'.join(message.split('\r\n')[:line_num])

            response += '\r\n.'
            self._send_ok(response)

        else:
            self._send_err()

    def _send_response(self, success: bool, message: str = ''):
        response = f'{"+OK" if success else "-ERR"}{" " + message if message else ""}\r\n'
        self._connection.sendall(response.encode())
        logging.info(
            f'POP3ServerThread sent response to {self._connection.getpeername()}: {response}'
        )

    def _send_ok(self, message: str = ''):
        self._send_response(True, message)

    def _send_err(self, message: str = ''):
        self._send_response(False, message)

    def _exit(self):
        if self._state == POP3State.TRANSACTION:
            db.release()
        logging.info(
            f'POP3ServerThread closing connetion with {self._connection.getpeername()}'
        )
        self._connection.close()
        sys.exit()

    def run(self):
        # greeting
        self._send_ok()
        command = self._recv_command()
        # if the dispatcher return True, terminate the loop
        while not self._dispatch(command):
            command = self._recv_command()

        self._exit()
