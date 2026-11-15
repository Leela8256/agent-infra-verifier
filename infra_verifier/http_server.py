"""Loopback server that never performs reverse-DNS discovery on startup."""

from http.server import ThreadingHTTPServer
from socketserver import TCPServer


class LoopbackHTTPServer(ThreadingHTTPServer):
    def server_bind(self):
        # HTTPServer.server_bind calls getfqdn(), which can stall for tens of
        # seconds on hosted macOS. These endpoints already bind a literal IP.
        TCPServer.server_bind(self)
        self.server_name = self.server_address[0]
        self.server_port = self.server_address[1]
