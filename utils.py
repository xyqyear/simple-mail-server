import dns.resolver
import logging
import socket


def get_mx(domain: str) -> str:
    for resolve_result in dns.resolver.resolve(domain, 'mx'):
        return resolve_result.to_text().split(' ')[1][:-1]

    return ''


def recv_response(s: socket.socket, ends_with='\r\n') -> str:
    data = b''
    while True:
        try:
            raw_data = s.recv(1024)
            logging.debug(f'received data: {raw_data}')

            data += raw_data
            if data.endswith(ends_with.encode()):
                return data.decode().strip()
        except TimeoutError:
            return ''
