import json
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field

ACCEPT = "application/json, text/event-stream"


@dataclass
class Reply:
    status: int
    headers: dict
    body: bytes
    elapsed: float
    messages: list = field(default_factory=list)

    @property
    def content_type(self) -> str:
        return self.headers.get("content-type", "").split(";")[0].strip().lower()

    def result_for(self, rid):
        for m in self.messages:
            if isinstance(m, dict) and m.get("id") == rid:
                return m
        return None


def parse_messages(content_type: str, body: bytes) -> list:
    text = body.decode("utf-8", "replace")
    if content_type == "text/event-stream":
        out, data = [], []
        for line in text.splitlines() + [""]:
            if line.startswith("data:"):
                data.append(line[5:].lstrip())
            elif not line.strip() and data:
                try:
                    out.append(json.loads("\n".join(data)))
                except ValueError:
                    pass
                data = []
        return out
    try:
        parsed = json.loads(text)
    except ValueError:
        return []
    return parsed if isinstance(parsed, list) else [parsed]


class Client:
    def __init__(self, url: str, timeout: float = 15.0, headers: dict | None = None):
        self.url = url
        self.timeout = timeout
        self.extra = dict(headers or {})
        self.session_id = None
        self.protocol_version = None
        self._next = 0

    def next_id(self) -> int:
        self._next += 1
        return self._next

    def raw(self, method: str, body: bytes | None = None, headers: dict | None = None) -> Reply:
        h = {"accept": ACCEPT, "user-agent": "mcp-preflight", **self.extra, **(headers or {})}
        if body is not None:
            h.setdefault("content-type", "application/json")
        if self.session_id:
            h["mcp-session-id"] = self.session_id
        if self.protocol_version:
            h["mcp-protocol-version"] = self.protocol_version
        req = urllib.request.Request(self.url, data=body, method=method, headers=h)
        start = time.perf_counter()
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as r:
                status, hdrs, data = r.status, {k.lower(): v for k, v in r.headers.items()}, r.read()
        except urllib.error.HTTPError as e:
            with e:
                status, hdrs, data = e.code, {k.lower(): v for k, v in e.headers.items()}, e.read()
        elapsed = time.perf_counter() - start
        reply = Reply(status, hdrs, data, elapsed)
        reply.messages = parse_messages(reply.content_type, data)
        return reply

    def request(self, method: str, params: dict | None = None) -> tuple[Reply, dict | None]:
        rid = self.next_id()
        msg = {"jsonrpc": "2.0", "id": rid, "method": method}
        if params is not None:
            msg["params"] = params
        reply = self.raw("POST", json.dumps(msg).encode())
        return reply, reply.result_for(rid)

    def notify(self, method: str, params: dict | None = None) -> Reply:
        msg = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            msg["params"] = params
        return self.raw("POST", json.dumps(msg).encode())
