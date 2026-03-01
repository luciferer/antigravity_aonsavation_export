import datetime
import http.server
import json
import os
import sqlite3
import threading

PORT = 18888
MD_FILE = os.path.expanduser("~/Desktop/conversation_log.md")
DB_FILE = os.path.expanduser("~/Desktop/conversation_log.db")
WRITE_LOCK = threading.Lock()

class RequestHandler(http.server.BaseHTTPRequestHandler):
    def _json_response(self, status_code, payload):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status_code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path == "/health":
            self._json_response(
                200,
                {
                    "status": "ok",
                    "service": "conversation-watchdog-legacy-plus",
                    "port": PORT,
                    "time": datetime.datetime.now().isoformat(timespec="seconds"),
                },
            )
            return
        self._json_response(404, {"status": "error", "message": "not found"})

    def do_POST(self):
        if self.path == "/log":
            content_length = int(self.headers.get("Content-Length", "0"))
            post_data = self.rfile.read(content_length)

            try:
                data = json.loads(post_data.decode("utf-8")) if post_data else {}
                role = data.get("role", "UNKNOWN")
                content = data.get("content", "")
                if not str(content).strip():
                    self._json_response(400, {"status": "error", "message": "empty content"})
                    return

                ts = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

                with WRITE_LOCK:
                    # Append to Markdown file and fsync for stronger sync semantics.
                    with open(MD_FILE, "a", encoding="utf-8") as f:
                        f.write(f"\n**{role}** [{ts}]:\n{content}\n")
                        f.write("-" * 40 + "\n")
                        f.flush()
                        os.fsync(f.fileno())

                    # Insert to SQLite DB with FULL sync for stronger durability.
                    conn = sqlite3.connect(DB_FILE, timeout=5)
                    try:
                        cursor = conn.cursor()
                        cursor.execute("PRAGMA journal_mode=WAL")
                        cursor.execute("PRAGMA synchronous=FULL")
                        cursor.execute(
                            """CREATE TABLE IF NOT EXISTS messages
                               (id INTEGER PRIMARY KEY AUTOINCREMENT,
                                role TEXT,
                                content TEXT,
                                timestamp DATETIME DEFAULT CURRENT_TIMESTAMP)"""
                        )
                        cursor.execute(
                            "INSERT INTO messages (role, content) VALUES (?, ?)",
                            (str(role), str(content)),
                        )
                        message_id = cursor.lastrowid
                        conn.commit()
                    finally:
                        conn.close()

                self._json_response(
                    200,
                    {
                        "status": "success",
                        "message_id": message_id,
                        "timestamp": ts,
                    },
                )
            except Exception as e:
                self._json_response(500, {"status": "error", "message": str(e)})
        else:
            self._json_response(404, {"status": "error", "message": "not found"})

    def log_message(self, format, *args):
        # Keep this daemon quiet unless explicitly inspected.
        return

def run(server_class=http.server.HTTPServer, handler_class=RequestHandler):
    server_address = ("", PORT)
    httpd = server_class(server_address, handler_class)
    print(f"Watchdog daemon running on port {PORT}...")
    httpd.serve_forever()

if __name__ == '__main__':
    run()
