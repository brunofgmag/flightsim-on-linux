import http.client
import http.server
import sys

AAO_HOST = "127.0.0.1"
AAO_PORT = 43380
LISTEN_PORT = 43390
DROPPED = {"connection", "keep-alive", "transfer-encoding", "content-length", "access-control-allow-origin", "access-control-allow-headers", "access-control-allow-methods", "server", "date"}


def handler_for(upstream_host, upstream_port):
    class AaoCorsProxy(http.server.BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def send_cors_headers(self):
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Access-Control-Allow-Headers", self.headers.get("Access-Control-Request-Headers", "*"))
            self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")

        def do_OPTIONS(self):
            self.send_response(204)
            self.send_cors_headers()
            self.send_header("Content-Length", "0")
            self.end_headers()

        def ask_upstream(self, body):
            try:
                upstream = http.client.HTTPConnection(upstream_host, upstream_port, timeout=5)
                upstream.request(self.command, self.path, body=body, headers={"Content-Type": self.headers.get("Content-Type", "text/plain")})
                response = upstream.getresponse()
                result = response.status, response.getheaders(), response.read()
                upstream.close()
                return result
            except OSError:
                return 502, [], b""

        def forward(self):
            length = int(self.headers.get("Content-Length") or 0)
            status, headers, data = self.ask_upstream(self.rfile.read(length) if length else None)
            self.send_response(status)
            seen = set()
            for name, value in headers:
                if name.lower() in DROPPED or name.lower() in seen:
                    continue
                seen.add(name.lower())
                self.send_header(name, value)
            self.send_cors_headers()
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            try:
                self.wfile.write(data)
            except BrokenPipeError:
                pass

        do_GET = forward
        do_POST = forward

        def log_message(self, *args):
            pass

    return AaoCorsProxy


def make_server(listen_port=LISTEN_PORT, upstream_port=AAO_PORT, upstream_host=AAO_HOST):
    return http.server.ThreadingHTTPServer(("127.0.0.1", listen_port), handler_for(upstream_host, upstream_port))


if __name__ == "__main__":
    make_server(*[int(value) for value in sys.argv[1:3]]).serve_forever()
