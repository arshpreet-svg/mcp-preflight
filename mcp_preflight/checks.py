import json
import re
from dataclasses import asdict, dataclass
from urllib.parse import urlparse

from .client import Client

MIN_VERSION = "2025-11-25"
CLIENT_VERSION = "2025-11-25"
NAME_RE = re.compile(r"^[A-Za-z0-9_.\-]{1,128}$")
WRITE_WORDS = re.compile(r"(^|_|-)(add|create|save|update|edit|delete|remove|send|post|set|write|book|buy|order|pay|cancel|approve|review|submit)(_|-|$)", re.I)
MARKDOWN = re.compile(r"(\*\*[^*]+\*\*|^#{1,6}\s|^\s*\|.*\|\s*$|^```)", re.M)
URL = re.compile(r"https?://\S+")


@dataclass
class Finding:
    id: str
    area: str
    level: str
    title: str
    detail: str = ""
    fix: str = ""

    def to_dict(self):
        return asdict(self)


@dataclass
class Settings:
    warn_latency: float = 1.5
    fail_latency: float = 5.0
    call_tools: bool = False
    call_args: dict | None = None
    max_tools: int = 20
    max_spoken_words: int = 120


class Preflight:
    def __init__(self, url: str, settings: Settings | None = None, headers: dict | None = None, timeout: float = 15.0):
        self.url = url
        self.s = settings or Settings()
        self.client = Client(url, timeout=timeout, headers=headers)
        self.findings: list[Finding] = []
        self.server = {}
        self.tools: list[dict] = []

    def add(self, id, area, level, title, detail="", fix=""):
        self.findings.append(Finding(id, area, level, title, detail, fix))

    def latency(self, id, what, seconds):
        ms = round(seconds * 1000)
        if seconds > self.s.fail_latency:
            self.add(id, "Voice", "fail", f"{what} took {ms} ms",
                     "A voice turn stalls while the assistant waits on this.", "Cache, trim work, or move it off the request path.")
        elif seconds > self.s.warn_latency:
            self.add(id, "Voice", "warn", f"{what} took {ms} ms",
                     f"Over {round(self.s.warn_latency * 1000)} ms is noticeable in a spoken conversation.",
                     "Keep the hot path fast; cold starts count too.")
        else:
            self.add(id, "Voice", "pass", f"{what} in {ms} ms")

    def run(self) -> list[Finding]:
        try:
            if not self.connect():
                return self.findings
            self.protocol_checks()
            self.list_tools()
            self.tool_checks()
            if self.s.call_tools:
                self.call_checks()
        except OSError as e:
            self.add("T0", "Transport", "fail", "Connection failed mid-run", str(e))
        return self.findings

    def connect(self) -> bool:
        u = urlparse(self.url)
        local = u.hostname in ("localhost", "127.0.0.1", "::1")
        if u.scheme == "https":
            self.add("T1", "Transport", "pass", "Served over HTTPS")
        elif local:
            self.add("T1", "Transport", "info", "Plain HTTP on localhost", "Fine for development; remote clients need HTTPS.")
        else:
            self.add("T1", "Transport", "fail", "Not served over HTTPS", "Remote assistants will not connect to plain HTTP.",
                     "Put the server behind TLS.")
        params = {"protocolVersion": CLIENT_VERSION, "capabilities": {},
                  "clientInfo": {"name": "mcp-preflight", "version": "0.1.0"}}
        try:
            reply, msg = self.client.request("initialize", params)
        except OSError as e:
            self.add("T2", "Transport", "fail", "Server unreachable", str(e), "Check the URL, including the /mcp path.")
            return False
        if reply.status != 200 or not msg:
            hint = ""
            if reply.status == 404:
                hint = "Check the path (usually /mcp). On serverless hosts a rewrite can hide the real path."
            elif reply.status == 421:
                hint = "The server's DNS-rebinding protection rejected this Host; add it to the allowed hosts."
            elif reply.status == 406:
                hint = "The server must accept 'Accept: application/json, text/event-stream'."
            self.add("T2", "Transport", "fail", f"initialize failed with HTTP {reply.status}",
                     reply.body[:200].decode("utf-8", "replace"), hint)
            return False
        if "error" in msg:
            self.add("T2", "Protocol", "fail", "initialize returned an error", json.dumps(msg["error"])[:300])
            return False
        self.add("T2", "Transport", "pass", "Streamable HTTP initialize succeeded",
                 f"Response as {reply.content_type or 'unknown content type'}")
        if reply.content_type not in ("application/json", "text/event-stream"):
            self.add("T3", "Transport", "fail", f"Unexpected Content-Type {reply.content_type!r}",
                     "", "Respond with application/json or text/event-stream.")
        result = msg.get("result", {})
        self.server = result
        version = result.get("protocolVersion", "")
        self.client.protocol_version = version or None
        if not version:
            self.add("P1", "Protocol", "fail", "No protocolVersion in initialize result")
        elif version < MIN_VERSION:
            self.add("P1", "Protocol", "fail", f"Negotiated protocol {version}, below {MIN_VERSION}",
                     "Alexa+ requires MCP 2025-11-25 or later.", "Upgrade your MCP SDK.")
        else:
            self.add("P1", "Protocol", "pass", f"Negotiated protocol {version}")
        info = result.get("serverInfo") or {}
        if info.get("name") and info.get("version"):
            self.add("P2", "Protocol", "pass", f"Server identifies as {info['name']} {info['version']}")
        else:
            self.add("P2", "Protocol", "warn", "serverInfo is missing a name or version")
        if "tools" in (result.get("capabilities") or {}):
            self.add("P3", "Protocol", "pass", "Declares the tools capability")
        else:
            self.add("P3", "Protocol", "fail", "Does not declare the tools capability",
                     "Clients may never ask for tools.", "Register at least one tool.")
        if (result.get("instructions") or "").strip():
            self.add("P4", "Quality", "pass", "Provides server instructions",
                     f"{len(result['instructions'])} characters to guide the assistant")
        else:
            self.add("P4", "Quality", "warn", "No server instructions",
                     "Instructions tell the assistant when to use your tools and how to talk about them.",
                     "Pass instructions= when creating the server.")
        sid = reply.headers.get("mcp-session-id")
        if sid:
            self.client.session_id = sid
            self.add("P5", "Protocol", "info", "Stateful server (Mcp-Session-Id issued)")
        else:
            self.add("P5", "Protocol", "info", "Stateless server (no session id)", "Each request stands alone.")
        self.latency("V1", "initialize", reply.elapsed)
        note = self.client.notify("notifications/initialized")
        if note.status in (200, 202, 204):
            self.add("P6", "Protocol", "pass", f"Accepted the initialized notification (HTTP {note.status})")
        else:
            self.add("P6", "Protocol", "fail", f"initialized notification got HTTP {note.status}",
                     "", "Notifications should be answered with 202 Accepted.")
        return True

    def protocol_checks(self):
        reply, msg = self.client.request("ping")
        if msg and msg.get("result") == {}:
            self.add("P7", "Protocol", "pass", "Answers ping")
        else:
            self.add("P7", "Protocol", "warn", "ping did not return an empty result", f"HTTP {reply.status}")
        reply, msg = self.client.request("preflight/does-not-exist")
        code = (msg or {}).get("error", {}).get("code")
        if code == -32601:
            self.add("P8", "Protocol", "pass", "Unknown methods get JSON-RPC error -32601")
        elif reply.status >= 500:
            self.add("P8", "Protocol", "fail", f"Unknown method crashed the server (HTTP {reply.status})",
                     "", "Return a JSON-RPC 'Method not found' error instead.")
        else:
            self.add("P8", "Protocol", "warn", "Unknown method did not return -32601", f"HTTP {reply.status}, code {code}")
        reply = self.client.raw("POST", b"{not json")
        if reply.status >= 500:
            self.add("P9", "Protocol", "fail", f"Malformed JSON crashed the server (HTTP {reply.status})",
                     "", "Reply with HTTP 400 or a JSON-RPC parse error (-32700).")
        else:
            self.add("P9", "Protocol", "pass", f"Malformed JSON handled (HTTP {reply.status})")

    def list_tools(self):
        cursor, pages = None, 0
        total = 0.0
        while True:
            reply, msg = self.client.request("tools/list", {"cursor": cursor} if cursor else {})
            total += reply.elapsed
            pages += 1
            if not msg or "result" not in msg:
                self.add("Q1", "Tools", "fail", "tools/list failed", f"HTTP {reply.status}")
                return
            self.tools += msg["result"].get("tools", [])
            cursor = msg["result"].get("nextCursor")
            if not cursor or pages >= 20:
                break
        self.latency("V2", "tools/list", total)
        if self.tools:
            more = f" across {pages} pages" if pages > 1 else ""
            self.add("Q1", "Tools", "pass", f"Lists {len(self.tools)} tools{more}")
        else:
            self.add("Q1", "Tools", "fail", "No tools listed")

    def tool_checks(self):
        if not self.tools:
            return
        names = [t.get("name", "") for t in self.tools]
        dupes = sorted({n for n in names if names.count(n) > 1})
        if dupes:
            self.add("Q2", "Tools", "fail", "Duplicate tool names", ", ".join(dupes))
        if len(self.tools) > self.s.max_tools:
            self.add("Q3", "Tools", "warn", f"{len(self.tools)} tools exposed",
                     "Tool choice gets less reliable as the list grows.", "Merge related tools or split the server.")
        problems = {"name": [], "desc": [], "params": [], "schema": [], "annot": [], "write": []}
        for t in self.tools:
            n = t.get("name", "?")
            if not NAME_RE.match(n):
                problems["name"].append(n)
            d = (t.get("description") or "").strip()
            if len(d) < 20:
                problems["desc"].append(n)
            schema = t.get("inputSchema") or {}
            if schema.get("type") != "object":
                problems["schema"].append(f"{n} (type is not object)")
            props = schema.get("properties") or {}
            missing = [k for k in schema.get("required") or [] if k not in props]
            if missing:
                problems["schema"].append(f"{n} (required but undefined: {', '.join(missing)})")
            undocumented = [k for k, v in props.items() if not (isinstance(v, dict) and v.get("description"))]
            if undocumented:
                problems["params"].append(f"{n}: {', '.join(undocumented)}")
            ann = t.get("annotations") or {}
            if not any(k in ann for k in ("readOnlyHint", "destructiveHint")):
                problems["annot"].append(n)
                if WRITE_WORDS.search(n):
                    problems["write"].append(n)
        self.summarise("Q4", "fail", problems["name"], "Tool names follow the spec's character rules",
                       "Tool names break the spec's naming rules", "Use 1-128 of A-Z a-z 0-9 _ - .")
        self.summarise("Q5", "warn", problems["desc"], "Every tool has a meaningful description",
                       "Tools with missing or very short descriptions",
                       "The description is how the assistant decides to call a tool; say what it does and when.")
        self.summarise("Q6", "fail", problems["schema"], "Input schemas are valid objects", "Input schema problems",
                       "inputSchema must be a JSON Schema object whose required keys exist.")
        self.summarise("Q7", "warn", problems["params"], "Every parameter is described", "Parameters without descriptions",
                       "Describe each parameter so the assistant can fill it from speech.")
        self.summarise("Q8", "warn", problems["annot"], "Tools declare readOnlyHint/destructiveHint",
                       "Tools without behaviour annotations",
                       "Annotations let the assistant run safe tools freely and confirm risky ones out loud.")
        if problems["write"]:
            self.add("Q9", "Voice", "warn", "Tools that look like they change data have no destructiveHint",
                     ", ".join(problems["write"]),
                     "Mark them so the assistant asks for spoken confirmation first.")

    def summarise(self, id, level, bad, ok_title, bad_title, fix):
        if bad:
            self.add(id, "Tools", level, f"{bad_title} ({len(bad)})", "; ".join(bad[:12]), fix)
        else:
            self.add(id, "Tools", "pass", ok_title)

    def sample_args(self, tool):
        given = (self.s.call_args or {}).get(tool["name"])
        if given is not None:
            return given
        schema = tool.get("inputSchema") or {}
        args = {}
        for key in schema.get("required") or []:
            spec = (schema.get("properties") or {}).get(key) or {}
            for pick in ("default", "const"):
                if pick in spec:
                    args[key] = spec[pick]
                    break
            else:
                if spec.get("enum"):
                    args[key] = spec["enum"][0]
                elif spec.get("examples"):
                    args[key] = spec["examples"][0]
                else:
                    return None
        return args

    def call_checks(self):
        safe = [t for t in self.tools if (t.get("annotations") or {}).get("readOnlyHint")
                or t["name"] in (self.s.call_args or {})]
        if not safe:
            self.add("V3", "Voice", "info", "No tools called",
                     "Only tools marked readOnlyHint (or given --args) are called, so nothing was changed.")
            return
        for t in safe:
            args = self.sample_args(t)
            if args is None:
                self.add("V3", "Voice", "info", f"Skipped {t['name']}", "Needs arguments; pass them with --args.")
                continue
            reply, msg = self.client.request("tools/call", {"name": t["name"], "arguments": args})
            result = (msg or {}).get("result")
            if not result:
                self.add("V3", "Voice", "fail", f"{t['name']} call failed", f"HTTP {reply.status} {json.dumps((msg or {}).get('error'))[:200]}")
                continue
            self.latency("V4", f"{t['name']} call", reply.elapsed)
            if result.get("isError"):
                self.add("V5", "Voice", "warn", f"{t['name']} returned isError", self.text_of(result)[:200])
                continue
            self.spoken(t["name"], result)

    @staticmethod
    def text_of(result) -> str:
        return "\n".join(c.get("text", "") for c in result.get("content") or [] if c.get("type") == "text")

    def spoken(self, name, result):
        text = self.text_of(result)
        say = None
        sc = result.get("structuredContent")
        if isinstance(sc, dict):
            say = sc.get("say") or sc.get("speech") or sc.get("message")
        if not say:
            try:
                parsed = json.loads(text)
                if isinstance(parsed, dict):
                    say = parsed.get("say") or parsed.get("speech") or parsed.get("message")
            except ValueError:
                pass
        spoken = say if isinstance(say, str) else text
        words = len(spoken.split())
        issues = []
        if words > self.s.max_spoken_words:
            issues.append(f"{words} words (~{round(words / 2.6)} s spoken)")
        if MARKDOWN.search(spoken):
            issues.append("markdown that will be read out")
        if URL.search(spoken):
            issues.append("raw links")
        label = "a ready-to-speak line" if isinstance(say, str) else "plain content"
        if issues:
            self.add("V6", "Voice", "warn", f"{name}: response is hard to speak", "; ".join(issues),
                     "Return a short 'say' sentence alongside the data.")
        else:
            self.add("V6", "Voice", "pass", f"{name}: {label}, {words} words")


def score(findings: list[Finding]) -> int:
    weights = {"pass": 1.0, "warn": 0.5, "fail": 0.0}
    rated = [weights[f.level] for f in findings if f.level in weights]
    return round(100 * sum(rated) / len(rated)) if rated else 0
