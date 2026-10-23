import json
import os
import sys

from .checks import Finding, score

ICON = {"pass": "✓", "warn": "!", "fail": "✗", "info": "·"}
COLOR = {"pass": "32", "warn": "33", "fail": "31", "info": "90"}
AREAS = ["Transport", "Protocol", "Tools", "Quality", "Voice"]


def _paint(text, code, on):
    return f"\033[{code}m{text}\033[0m" if on else text


def text_report(url, server, findings: list[Finding], stream=None):
    stream = stream or sys.stdout
    on = stream.isatty() and not os.environ.get("NO_COLOR")
    info = server.get("serverInfo") or {}
    w = stream.write
    w(f"\nmcp-preflight  {url}\n")
    if info:
        w(f"server         {info.get('name', '?')} {info.get('version', '')}  ·  protocol {server.get('protocolVersion', '?')}\n")
    for area in AREAS:
        rows = [f for f in findings if f.area == area]
        if not rows:
            continue
        w(f"\n{_paint(area, '1', on)}\n")
        for f in rows:
            w(f"  {_paint(ICON[f.level], COLOR[f.level], on)} {f.title}\n")
            if f.detail and f.level != "pass":
                w(f"      {_paint(f.detail, '90', on)}\n")
            if f.fix and f.level in ("warn", "fail"):
                w(f"      → {f.fix}\n")
    counts = {k: sum(f.level == k for f in findings) for k in ICON}
    s = score(findings)
    tone = "32" if s >= 85 else "33" if s >= 60 else "31"
    w(f"\n{_paint(f'Score {s}/100', tone + ';1', on)}   "
      f"{counts['pass']} passed, {counts['warn']} warnings, {counts['fail']} failed\n\n")


def markdown_report(url, server, findings: list[Finding]) -> str:
    info = server.get("serverInfo") or {}
    lines = [f"# MCP preflight: {info.get('name', url)}", "",
             f"- URL: `{url}`", f"- Protocol: `{server.get('protocolVersion', '?')}`",
             f"- **Score: {score(findings)}/100**", "", "| | Check | Area | Details | Fix |", "|---|---|---|---|---|"]
    for f in findings:
        cells = [ICON[f.level], f.title, f.area, f.detail, f.fix if f.level in ("warn", "fail") else ""]
        lines.append("| " + " | ".join(c.replace("|", "\\|").replace("\n", " ") for c in cells) + " |")
    return "\n".join(lines) + "\n"


def json_report(url, server, findings: list[Finding]) -> str:
    return json.dumps({"url": url, "server": server.get("serverInfo"), "protocolVersion": server.get("protocolVersion"),
                       "score": score(findings), "findings": [f.to_dict() for f in findings]}, indent=2)
