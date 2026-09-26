# Runs inside agent: record the first Codex /responses request body, reply 400.
import http.server, json
class H(http.server.BaseHTTPRequestHandler):
    def do_POST(self):
        body = self.rfile.read(int(self.headers.get("content-length", 0)))
        open("/tmp/codex_req.json", "wb").write(body)
        self.send_response(400); self.send_header("content-type", "application/json"); self.end_headers()
        self.wfile.write(b'{"error":{"message":"capture"}}')
    def do_GET(self):
        self.send_response(200); self.send_header("content-type","application/json"); self.end_headers(); self.wfile.write(b'{"data":[]}')
    def log_message(self, *a): pass
http.server.HTTPServer(("127.0.0.1", 9999), H).serve_forever()
