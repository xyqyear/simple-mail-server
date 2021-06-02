import threading
import logging
import socket
import base64
import sys
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
        self._socket.sendall(command.encode())
        logging.info(
            f'SMTPSender sent from {self._mail_from} to {self._rcpt_to}: {command}'
        )

    def _recv_response(self, ends_with: str = '\r\n') -> str:
        data = recv_response(self._socket, ends_with)
        logging.info(f'SMTPSender received data from {self._rcpt_to}: {data}')
        return data

    def _check_response(self, raise_message: str, ends_with: str = '\r\n'):
        """
        check if the response of the server is positive.
        otherwise raise an Exception.
        """
        try:
            response = SMTPResponse.from_str(self._recv_response(ends_with))
        except Exception as e:
            raise Exception(f'{raise_message}: {e}')

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
        self._send_command(f'MAIL FROM:<{self._mail_from}>\r\n')
        self._check_response('Failed while stating source mailbox.')

        # RCPT TO
        self._send_command(f'RCPT TO:<{self._rcpt_to}>\r\n')
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
    def __init__(self, domain: str, username: str, password: str):
        self.domain = domain
        self.username = username
        self.password = password
        self.address = f'{self.username}@{self.domain}'

    def run(self):
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.bind(('0.0.0.0', 25))
        s.listen()
        logging.info(f'SMTPServer is now running')
        while True:
            conn, address = s.accept()
            logging.info(f'SMTPServer accepted new connection from {address}')
            thread = SMTPServerThread(conn, self)
            thread.start()


INVALID_COMMAND_MESSAGE = "550 Invalid command in current state."
SYNTAX_ERROR_MESSAGE = "501 Syntax error in coomand or arguments."
OK_MESSAGE = "250 OK."


class SMTPServerThread(threading.Thread):
    def __init__(self, connection: socket.socket, server: SMTPServer):
        super().__init__()
        self._connection = connection
        self._server = server

        self._as_submission_server = False
        self._rcpt_to: str
        self._mail_content: str

        self._connection.settimeout(10)
        # for logging purpose
        self._peer_name = self._connection.getpeername()

    def _send_response(self, content: str):
        self._connection.sendall(f'{content}\r\n'.encode())
        logging.info(
            f'SMTPServerThread sent response to {self._peer_name}: {content}')

    def _recv_response(self, ends_with='\r\n') -> str:
        try:
            response = recv_response(self._connection, ends_with)
            return response
        except Exception:
            self._exit()

    def _recv_command(self, ends_with='\r\n') -> SMTPCommand:
        raw_command = self._recv_response(ends_with)
        logging.info(
            f'SMTPServerThread received command from {self._peer_name}: {raw_command}'
        )
        return SMTPCommand.from_str(raw_command)

    def _process_command(self, func):
        while not func():
            pass

    def _helo(self) -> bool:
        c = self._recv_command()
        if c.command in ('HELO', 'EHLO'):
            if not c.argument:
                self._send_response(SYNTAX_ERROR_MESSAGE)
                return False
            self._send_response('250-AUTH LOGIN\r\n250 OK.')
            return True
        else:
            self._send_response(INVALID_COMMAND_MESSAGE)
            return False

    def _auth(self):
        # Username:
        self._send_response(f'334 VXNlcm5hbWU6')
        username_base64 = self._recv_response()
        # Password:
        self._send_response(f'334 UGFzc3dvcmQ6')
        password_base64 = self._recv_response()

        if base64.b64encode(self._server.address.encode()).decode() == username_base64 and \
           base64.b64encode(self._server.password.encode()).decode() == password_base64:
            self._as_submission_server = True
            self._send_response('235 Login successful.')
        else:
            self._send_response('535 Login fail.')
            self._exit()

    def _mail_from(self) -> bool:
        """
        the client may send auth login before mail from
        this means the client is using this server as a mail submission server
        """
        c = self._recv_command()

        if c.raw_command.upper() == 'AUTH LOGIN':
            self._auth()
        elif c.command == 'MAIL':
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
            if not self._as_submission_server and self._rcpt_to != self._server.address:
                self._send_response("550 This is not an open relay server.")
                self._exit()
            else:
                self._send_response("354 End with <CRLF>.<CRLF>.")
                return True
        else:
            self._send_response(INVALID_COMMAND_MESSAGE)
            return False

    def _actual_data(self) -> bool:
        data = self._recv_response('\r\n.\r\n')
        logging.info(
            f'SMTPServerThread received data from {self._peer_name}: {data}')
        self._send_response(OK_MESSAGE)
        self._mail_content = data
        return True

    def _quit(self) -> bool:
        c = self._recv_command()
        if c.command == 'QUIT':
            self._send_response("221 Bye.")
            return True
        else:
            self._send_response(INVALID_COMMAND_MESSAGE)
            return False

    def _exit(self):
        if self._mail_content:
            if self._as_submission_server:
                try:
                    client = SMTPSender(self._server.address, self._rcpt_to)
                    client.connect()
                    client.send(self._mail_content)
                    client.close()
                except Exception as e:
                    logging.error(
                        f'failed sending mail to {self._rcpt_to}: {e}')
            else:
                db.aquire()
                db.insert_message(self._mail_content)
                db.release()

        logging.info(
            f'POP3ServerThread closing connetion with {self._peer_name}')
        self._connection.close()
        sys.exit()

    def run(self):
        self._send_response(f'220 {self._server.domain} Demo SMTP Server')
        for func in (self._helo, self._mail_from, self._rcpt_to, self._data,
                     self._actual_data, self._quit):
            self._process_command(func)

        self._exit()
