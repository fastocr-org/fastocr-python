"""A local stand-in for the FastOCR API plus the S3 presigned URLs it hands out."""
import hashlib
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PAGES_TEXT = "Hello مرحبا 世界"
RESULT_PDF = b"%PDF-1.4 searchable"


class FakeApi:
    def __init__(self, polls_before_done=1, final_status="completed"):
        self.polls_before_done = polls_before_done
        self.final_status = final_status
        self.polls = 0
        self.api_requests = []
        self.uploads = []
        self.created = {}
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), self._handler())
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    @property
    def base_url(self):
        return f"http://127.0.0.1:{self.server.server_address[1]}"

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *exc):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)

    def _handler(self):
        api = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *args):
                pass

            def send_json(self, status, body, headers=None):
                data = json.dumps(body).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                for name, value in (headers or {}).items():
                    self.send_header(name, value)
                self.end_headers()
                self.wfile.write(data)

            def send_bytes(self, status, data, content_type):
                self.send_response(status)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def read_json_body(self):
                length = int(self.headers.get("Content-Length", 0))
                return json.loads(self.rfile.read(length) or b"{}")

            def record_api_request(self):
                api.api_requests.append(
                    {"method": self.command, "path": self.path, "headers": dict(self.headers)}
                )

            def do_POST(self):
                if self.path == "/v1/documents":
                    self.record_api_request()
                    body = self.read_json_body()
                    key = self.headers["Idempotency-Key"]
                    doc_id = api.created.setdefault(key, f"doc-{len(api.created) + 1}")
                    api.created[doc_id] = body
                    self.send_json(201, {
                        "id": doc_id,
                        "status": "awaiting_upload",
                        "upload_url": f"{api.base_url}/s3/{doc_id}?X-Amz-Signature=abc",
                        "expires_at": "2099-01-01T00:00:00+00:00",
                    })
                elif self.path.endswith("/start"):
                    self.record_api_request()
                    self.send_json(202, {"status": "processing"})
                else:
                    self.send_json(404, {"error": {"type": "not_found", "code": "not_found",
                                                   "message": "Route not found", "request_id": "r1"}})

            def do_PUT(self):
                chunked = "chunked" in self.headers.get("Transfer-Encoding", "").lower()
                upload = {
                    "path": self.path,
                    "headers": dict(self.headers),
                    "chunked": chunked,
                    "sha256": None,
                    "received": 0,
                }
                api.uploads.append(upload)
                if chunked:
                    self.send_bytes(501, b"<Error><Code>NotImplemented</Code></Error>", "application/xml")
                    self.close_connection = True
                    return
                remaining = int(self.headers["Content-Length"])
                digest = hashlib.sha256()
                while remaining:
                    chunk = self.rfile.read(min(65536, remaining))
                    if not chunk:
                        break
                    digest.update(chunk)
                    remaining -= len(chunk)
                    upload["received"] += len(chunk)
                upload["sha256"] = digest.hexdigest()
                self.send_bytes(200, b"", "text/plain")

            def do_GET(self):
                if self.path.startswith("/download/"):
                    if self.path.endswith("text"):
                        self.send_bytes(200, PAGES_TEXT.encode(), "text/plain")
                    else:
                        self.send_bytes(200, RESULT_PDF, "application/pdf")
                    return
                self.record_api_request()
                if "/output" in self.path:
                    kind = "pdf" if "format=pdf" in self.path else "text"
                    self.send_json(200, {
                        "format": kind,
                        "url": f"{api.base_url}/download/{kind}",
                        "expires_at": "2099-01-01T00:00:00+00:00",
                    })
                    return
                doc_id = self.path.rsplit("/", 1)[-1]
                api.polls += 1
                done = api.polls > api.polls_before_done
                self.send_json(200, {
                    "id": doc_id,
                    "status": api.final_status if done else "processing",
                    "external_id": None,
                    "pages_billed": 1,
                    "pages_total": 1,
                })

        return Handler
