# mcp-preflight

**Is your MCP server ready for Alexa+?** One command checks a remote MCP server the way a voice assistant will use
it: does it speak the current spec over Streamable HTTP, will the assistant pick the right tool and fill its
arguments from speech, and will the answers sound right when read aloud.

```bash
pip install git+https://github.com/arshpreet-svg/mcp-preflight
mcp-preflight https://your-server.example.com/mcp --call
```

Real output against the deliberately broken test server in `tests/servers.py` (trimmed):

```
mcp-preflight  http://127.0.0.1:52409/mcp
server         weather-bad   ·  protocol 2025-03-26

Protocol
  ✗ Negotiated protocol 2025-03-26, below 2025-11-25
      Alexa+ requires MCP 2025-11-25 or later.
      → Upgrade your MCP SDK.
  ✗ Unknown method crashed the server (HTTP 500)
      → Return a JSON-RPC 'Method not found' error instead.
  ✗ Malformed JSON crashed the server (HTTP 500)
      → Reply with HTTP 400 or a JSON-RPC parse error (-32700).

Tools
  ✓ Lists 3 tools
  ✗ Tool names break the spec's naming rules (1)
      get weather!
  ✗ Input schema problems (1)
      get weather! (required but undefined: city)
  ! Tools without behaviour annotations (2)
      get weather!; send_alert
      → Annotations let the assistant run safe tools freely and confirm risky ones out loud.

Quality
  ! No server instructions
      → Pass instructions= when creating the server.

Voice
  ! Tools that look like they change data have no destructiveHint
      send_alert
      → Mark them so the assistant asks for spoken confirmation first.
  ! today: response is hard to speak
      279 words (~107 s spoken); markdown that will be read out; raw links
      → Return a short 'say' sentence alongside the data.

Score 55/100   7 passed, 8 warnings, 5 failed
```

The well-built test server scores 97 with the same command.

No dependencies: it's a single standard-library Python package, so it runs anywhere Python 3.10+ does.

## Why

Alexa+ connects to self-hosted MCP servers that implement spec **2025-11-25 or later over Streamable HTTP**. The
official MCP Inspector lets you click through a server by hand, but nothing tells you, in one run, whether a server is
*ready*: right protocol version, sane error handling, tools an assistant can choose between, and responses that work as
speech. Most problems only show up later as "Alexa picked the wrong tool", "it read out a markdown table", or a turn
that stalls on a cold start. mcp-preflight turns those into named checks with a fix for each, and a score you can
gate CI on.

## What it checks

| Area | Check | Level |
|---|---|---|
| Transport | HTTPS for remote servers; `initialize` over Streamable HTTP; JSON or SSE responses; useful hints for 404 (path or serverless rewrite), 406 (Accept header) and 421 (Host check) | fail |
| Protocol | Negotiated version is 2025-11-25 or later | fail |
| | `serverInfo` name and version; `tools` capability declared | warn / fail |
| | Stateful sessions (`Mcp-Session-Id`) are followed; stateless servers are reported | info |
| | `notifications/initialized` accepted; `ping` answered | fail / warn |
| | Unknown method returns JSON-RPC `-32601`, malformed JSON doesn't cause a 5xx | fail |
| Tools | `tools/list` works, with pagination; no duplicate names | fail |
| | Names follow the spec's rules (1-128 of `A-Z a-z 0-9 _ - .`) | fail |
| | Descriptions long enough to choose by; every parameter described | warn |
| | `inputSchema` is an object and its `required` keys exist | fail |
| | `readOnlyHint` / `destructiveHint` annotations, so the assistant can confirm risky actions out loud | warn |
| | More than 20 tools (selection gets less reliable) | warn |
| Quality | Server `instructions` present to guide the assistant | warn |
| Voice | Latency of `initialize`, `tools/list` and each call (warn over 1.5 s, fail over 5 s) | warn / fail |
| | Tools whose names suggest they change data but aren't marked `destructiveHint` | warn |
| | With `--call`: each read-only tool's response checked for length when spoken, markdown and raw links, and whether it offers a ready-to-speak `say` line | warn |

`--call` only ever calls tools annotated `readOnlyHint: true`, filling required arguments from each schema's
`default`, `const`, `enum` or `examples`. To call anything else, name it explicitly with `--args`.

## Usage

```bash
mcp-preflight URL [--call] [--args args.json] [--header "Authorization: Bearer ..."]
                  [--json] [--markdown report.md] [--min-score 80] [--strict] [--warn-ms 1500]
```

- `--args args.json`: `{"get_forecast": {"city": "Pune"}}`. Tools listed here are called even if not read-only.
- `--json`: machine-readable report. `--markdown`: a table for PRs and job summaries.
- Exit code is 1 on any failure, any warning with `--strict`, or a score below `--min-score`.

From Python:

```python
from mcp_preflight import Preflight, Settings, score
findings = Preflight("https://example.com/mcp", Settings(call_tools=True)).run()
print(score(findings), [f.title for f in findings if f.level == "fail"])
```

## GitHub Action

```yaml
- uses: arshpreet-svg/mcp-preflight@main
  with:
    url: https://my-server.example.com/mcp
    args: --call --min-score 80
```

The Markdown report is added to the job summary, and the step fails when the server isn't ready.

## Making a server pass (MCP Python SDK)

```python
from typing import Annotated
from pydantic import Field
from mcp.types import ToolAnnotations

mcp = MCPServer("weather", instructions="Answer spoken weather questions in one sentence.")

@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
def get_forecast(city: Annotated[str, Field(description="City as the user said it", examples=["Pune"])]) -> dict:
    """Get tomorrow's forecast for a city, with a short sentence ready to speak."""
    return {"say": f"Tomorrow in {city}: sunny, high of 31.", "high_c": 31}
```

## Tests

```bash
pip install -e ".[test]"
python -m unittest -v
```

16 tests run two real servers in-process: a well-built one made with the MCP Python SDK (stateful sessions,
annotations, described parameters) that must score 95+, and a deliberately broken one (old protocol version, 500 on
unknown methods and bad JSON, invalid tool names, a schema requiring an undefined key, an unannotated `send_alert`,
and a long markdown answer with links) where every problem must be caught. CI runs them on Python 3.11 to 3.13.

## License

MIT
