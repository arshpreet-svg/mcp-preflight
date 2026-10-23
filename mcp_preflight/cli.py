import argparse
import json
import sys
from pathlib import Path

from . import __version__
from .checks import Preflight, Settings, score
from .report import json_report, markdown_report, text_report


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="mcp-preflight",
                                description="Check an MCP server is ready for Alexa+ and other voice assistants.")
    p.add_argument("url", help="Streamable HTTP endpoint, e.g. https://example.com/mcp")
    p.add_argument("--call", action="store_true", help="also call read-only tools and check their spoken responses")
    p.add_argument("--args", metavar="FILE", help="JSON file of {tool_name: arguments} to call (implies --call)")
    p.add_argument("--header", action="append", default=[], metavar="K:V", help="extra request header (repeatable)")
    p.add_argument("--json", action="store_true", help="print a JSON report instead of text")
    p.add_argument("--markdown", metavar="FILE", help="also write a Markdown report")
    p.add_argument("--min-score", type=int, default=0, help="exit 1 if the score is below this")
    p.add_argument("--strict", action="store_true", help="exit 1 on warnings as well as failures")
    p.add_argument("--warn-ms", type=int, default=1500, help="latency that counts as slow (default 1500)")
    p.add_argument("--timeout", type=float, default=15.0)
    p.add_argument("--version", action="version", version=f"mcp-preflight {__version__}")
    a = p.parse_args(argv)

    headers = dict(h.split(":", 1) for h in a.header)
    headers = {k.strip(): v.strip() for k, v in headers.items()}
    call_args = json.loads(Path(a.args).read_text()) if a.args else None
    settings = Settings(warn_latency=a.warn_ms / 1000, call_tools=a.call or bool(call_args), call_args=call_args)
    pf = Preflight(a.url, settings, headers=headers, timeout=a.timeout)
    findings = pf.run()

    if a.json:
        print(json_report(a.url, pf.server, findings))
    else:
        text_report(a.url, pf.server, findings)
    if a.markdown:
        Path(a.markdown).write_text(markdown_report(a.url, pf.server, findings))

    failed = any(f.level == "fail" for f in findings) or (a.strict and any(f.level == "warn" for f in findings))
    return 1 if failed or score(findings) < a.min_score else 0


if __name__ == "__main__":
    sys.exit(main())
