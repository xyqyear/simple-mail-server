import threading
import logging
import socket
import config
import re

from utils import get_mx, recv_response
from mailbox import db


class SMTPResponse:
    def __init__(self, raw_response: str, code: int, content: str):
        self.raw_response = raw_response
        self.code = code
        self.success = 200 <= code < 300 or code == 354

    @classmethod
    def from_str(cls, raw_response: str) -> 'SMTPResponse':
        return cls(raw_response, int(raw_response[:3]),
                   raw_response[3:].strip())

    def to_str(self) -> str:
        return self.raw_response


class SMTPCommand:
    def __init__(self, raw_command: str, command: str, argument: str):
        self.raw_command = raw_command
        self.command = command.upper()
        self.argument = argument

        if self.command == 'MAIL':
            self.from_address = re.search(r'<(.*)>', self.argument).group(1)
            self.from_username, self.from_domain = self.from_address.split('@')
        elif self.command == 'RCPT':
            self.to_address = re.search(r'<(.*)>', self.argument).group(1)
            self.to_username, self.to_domain = self.to_address.split('@')

    @classmethod
    def from_str(cls, raw_command: str) -> 'SMTPCommand':
        split_result = raw_command.split(' ', 1)
        return cls(raw_command, split_result[0].strip(),
                   split_result[1].strip() if len(split_result) > 1 else '')

    def to_str(self) -> str:
        return self.raw_command


class SMTPSender:
    def __init__(self, mail_from: str, rcpt_to: str):
        self._mail_from = mail_from
        self._rcpt_to = rcpt_to

        self._socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._socket.settimeout(10)

    def _send_command(self, command: str):
        logging.info(f'sending command: {command}')
        self._socket.sendall(command.encode())

    def _recv_response(self, ends_with: str = '\r\n') -> SMTPResponse:
        data = recv_response(self._socket, ends_with)
        if data:
            return SMTPResponse.from_str(data)
        else:
            return None

    def _check_response(self, raise_message: str, ends_with: str = '\r\n'):
        """
        check if the response of the server is positive.
        otherwise raise an Exception.
        """
        response = self._recv_response(ends_with)
        if not response.success:
            raise Exception(f'{raise_message}: {response.raw_response}')

    def connect(self):
        domain = self._mail_from.split('@')[1]

        # resolve destination mailbox mx record
        hostname = self._rcpt_to.split('@')[1]
        if re.match(r'^\[\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}\]$', hostname):
            self._socket.connect((hostname[1:][:-1], 25))
            logging.log(0, f'connecting to {hostname}')
        else:
            hostname = get_mx(hostname)
            if not hostname:
                raise Exception('MX Record not found.')
            self._socket.connect((hostname, 25))
            logging.log(0, f'connecting to {hostname}')

        # receive initial server message
        self._check_response('Invalid response from server while connecting.')

        # greeting
        self._send_command(f'HELO {domain}\r\n')
        self._check_response('Failed while greeting.')

        # MAIL FROM
        self._send_command(f'MAIL FROM:<{self.mail_from}>\r\n')
        self._check_response('Failed while stating source mailbox.')

        # RCPT TO
        self._send_command(f'RCPT TO:<{self.rcpt_to}>\r\n')
        self._check_response('Failed while stating destination mailbox.')

    def send(self, content: str):
        """
        content argument should be without the ending .\r\n line
        """
        # DATA
        self._send_command("DATA\r\n")
        self._check_response('Failed while initializing data transfer.')

        # Actual content
        self._send_command(content + '\r\n.\r\n')
        self._check_response('Failed while sending mail.')

    def close(self):
        # QUIT
        self._send_command("QUIT\r\n")
        recv_response(self._socket)
        self._socket.close()


class SMTPServer:
    def __init__(self, domain, username, password):
        self.domain = domain
        self.username = username
        self.password = password
        self.address = f'{self.username}@{self.domain}'

    def run(self):
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.bind(('0.0.0.0', 25))
        s.listen()
        while True:
            conn, _ = s.accept()
            thread = SMTPServerThread(conn, self)
            thread.run()


INVALID_COMMAND_MESSAGE = "550 Invalid command in current state.\r\n"
SYNTAX_ERROR_MESSAGE = "501 Syntax error in coomand or arguments.\r\n"
OK_MESSAGE = "250 OK.\r\n"


class SMTPServerThread(threading.Thread):
    def __init__(self, connection: socket.socket, server: SMTPServer):
        super().__init__()
        self._connection = connection
        self._server = server

        self._rcpt_to: str
        self._relay: bool
        self._mail_content: str

    def _send_response(self, content: str):
        self._connection.sendall(content.encode())

    def _recv_command(self, ends_with='\r\n') -> SMTPCommand:
        raw_command = recv_response(self._connection, ends_with)
        return SMTPCommand.from_str(raw_command)

    def _process_command(self, func):
        while not func():
            pass

    def _helo(self) -> bool:
        c = self._recv_command()
        if c.command == 'HELO':
            if not c.argument:
                self._send_response(SYNTAX_ERROR_MESSAGE)
                return False
            self._send_response(OK_MESSAGE)
            return True
        else:
            if c.command == 'EHLO':
                self._send_response("502 EHLO Not Supported\r\n")
            else:
                self._send_response(INVALID_COMMAND_MESSAGE)
            return False

    def _mail_from(self) -> bool:
        c = self._recv_command()

        if c.command == 'MAIL':
            self._send_response(OK_MESSAGE)
            return True
        else:
            self._send_response(INVALID_COMMAND_MESSAGE)
            return False

    def _rcpt_to(self) -> bool:
        c = self._recv_command()

        if c.command == 'RCPT':
            self._rcpt_to = c.to_address
            self._send_response(OK_MESSAGE)
            return True
        else:
            self._send_response(INVALID_COMMAND_MESSAGE)
            return False

    def _data(self) -> bool:
        c = self._recv_command()
        if c.command == 'DATA':
            self._send_response("354 End with <CRLF>.<CRLF>.\r\n")
            return True
        else:
            self._send_response(INVALID_COMMAND_MESSAGE)
            return False

    def _actual_data(self) -> bool:
        data = recv_response(self._connection, '\r\n.\r\n')
        self._send_response(OK_MESSAGE)
        self._mail_content = data
        return True

    def _quit(self) -> bool:
        c = self._recv_command()
        if c.command == 'QUIT':
            self._send_response("221 Bye.\r\n")
            return True
        else:
            self._send_response(INVALID_COMMAND_MESSAGE)
            return False

    def run(self):
        self._send_response(f'220 {self._server.domain} Demo SMTP Server\r\n')
        for func in (self._helo, self._mail_from, self._rcpt_to, self._data,
                     self._actual_data, self._quit):
            self._process_command(func)

        if self._rcpt_to == self._server.address:
            db.aquire()
            db.insert_message(self._mail_content)
            db.release()
        else:
            client = SMTPSender(self._server.address, self._rcpt_to)
            client.connect()
            client.send(self._mail_content)
            client.close()
