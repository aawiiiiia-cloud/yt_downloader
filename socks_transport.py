"""Requests transport using the SOCKS implementation already bundled with yt-dlp."""
from urllib.parse import urlparse


def install_socks_transport(session, proxy):
    from requests.adapters import HTTPAdapter
    from urllib3.connection import HTTPConnection, HTTPSConnection
    from urllib3.connectionpool import HTTPConnectionPool, HTTPSConnectionPool
    from yt_dlp.socks import ProxyType, sockssocket
    import socket

    parsed = urlparse(proxy)
    kinds = {'socks5': ProxyType.SOCKS5, 'socks5h': ProxyType.SOCKS5,
             'socks4': ProxyType.SOCKS4, 'socks4a': ProxyType.SOCKS4A}
    if (parsed.scheme not in kinds or not parsed.hostname or not parsed.port
            or parsed.username or parsed.password or parsed.path not in ('', '/')
            or parsed.query or parsed.fragment):
        raise ValueError('Invalid SOCKS proxy')

    def new_connection(connection):
        # Resolve only the proxy locally; retain the destination for SNI and DNS.
        family = socket.getaddrinfo(parsed.hostname, parsed.port, type=socket.SOCK_STREAM)[0][0]
        sock = sockssocket(family, socket.SOCK_STREAM)
        try:
            sock.settimeout(connection.timeout)
            sock.setproxy(kinds[parsed.scheme], parsed.hostname, parsed.port,
                          rdns=parsed.scheme in ('socks5h', 'socks4a'))
            for option in connection.socket_options or []:
                sock.setsockopt(*option)
            sock.connect((connection._dns_host, connection.port))
            return sock
        except BaseException:
            sock.close()
            raise

    class SocksHTTP(HTTPConnection):
        def _new_conn(self):
            return new_connection(self)

    class SocksHTTPS(HTTPSConnection):
        def _new_conn(self):
            return new_connection(self)

    class HTTPPool(HTTPConnectionPool):
        ConnectionCls = SocksHTTP

    class HTTPSPool(HTTPSConnectionPool):
        ConnectionCls = SocksHTTPS

    class Adapter(HTTPAdapter):
        def init_poolmanager(self, *args, **kwargs):
            super().init_poolmanager(*args, **kwargs)
            self.poolmanager.pool_classes_by_scheme = {'http': HTTPPool, 'https': HTTPSPool}

    session.trust_env = False
    session.proxies.clear()
    session.mount('http://', Adapter(max_retries=0))
    session.mount('https://', Adapter(max_retries=0))
