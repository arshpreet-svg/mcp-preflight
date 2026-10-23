import json
import socket
import threading
import time
from typing import Annotated

import uvicorn
from mcp.server.mcpserver import MCPServer
from mcp.types import ToolAnnotations
from pydantic import Field

good = MCPServer("weather-good", instructions="Use these tools to answer spoken weather questions in one sentence.")


@good.tool(annotations=ToolAnnotations(readOnlyHint=True))
def get_forecast(city: Annotated[str, Field(description="City name as the user said it", examples=["Pune"])]) -> dict:
    """Get tomorrow's forecast for a city, with a short sentence ready to speak."""
    return {"say": f"Tomorrow in {city}: sunny, high of 31.", "high_c": 31}


@good.tool(annotations=ToolAnnotations(destructiveHint=True))
def delete_saved_city(city: Annotated[str, Field(description="City to forget")]) -> str:
    """Remove a city from the user's saved list after they confirm."""
    return f"Removed {city}."


LONG = "**Forecast**\n\n| day | temp |\n|---|---|\n" + "It will be warm and pleasant across the region. " * 30 + "See https://example.com/weather"


async def bad(scope, receive, send):
    if scope["type"] == "lifespan":
        while (await receive())["type"] != "lifespan.shutdown":
            await send({"type": "lifespan.startup.complete"})
        await send({"type": "lifespan.shutdown.complete"})
        return
    body = b""
    while True:
        m = await receive()
        body += m.get("body", b"")
        if not m.get("more_body"):
            break

    async def reply(status, payload):
        data = json.dumps(payload).encode() if payload is not None else b""
        await send({"type": "http.response.start", "status": status, "headers": [(b"content-type", b"application/json")]})
        await send({"type": "http.response.body", "body": data})

    try:
        msg = json.loads(body)
    except ValueError:
        return await reply(500, {"oops": "crash"})
    method, rid = msg.get("method"), msg.get("id")
    if rid is None:
        return await reply(200, None)
    if method == "initialize":
        return await reply(200, {"jsonrpc": "2.0", "id": rid, "result": {
            "protocolVersion": "2025-03-26", "capabilities": {"tools": {}}, "serverInfo": {"name": "weather-bad"}}})
    if method == "tools/list":
        time.sleep(0.3)
        return await reply(200, {"jsonrpc": "2.0", "id": rid, "result": {"tools": [
            {"name": "get weather!", "description": "weather", "inputSchema": {"type": "object", "properties": {"q": {}}, "required": ["city"]}},
            {"name": "send_alert", "description": "Send a severe weather alert to every subscriber.", "inputSchema": {"type": "object", "properties": {}}},
            {"name": "today", "description": "Today's forecast for the default city, in detail.", "inputSchema": {"type": "object", "properties": {}},
             "annotations": {"readOnlyHint": True}},
        ]}})
    if method == "tools/call":
        return await reply(200, {"jsonrpc": "2.0", "id": rid, "result": {"content": [{"type": "text", "text": LONG}]}})
    return await reply(500, {"error": "unhandled"})


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def serve(app):
    port = free_port()
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error"))
    threading.Thread(target=server.run, daemon=True).start()
    for _ in range(100):
        if server.started:
            break
        time.sleep(0.05)
    return server, port
