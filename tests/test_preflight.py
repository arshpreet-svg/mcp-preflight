import io
import json
import unittest

from mcp_preflight import Preflight, Settings, score
from mcp_preflight.cli import main
from mcp_preflight.client import parse_messages
from mcp_preflight.report import markdown_report

from .servers import bad, good, serve


def by_id(findings):
    out = {}
    for f in findings:
        out.setdefault(f.id, []).append(f)
    return out


class Good(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server, port = serve(good.streamable_http_app(json_response=True))
        cls.url = f"http://127.0.0.1:{port}/mcp"
        pf = Preflight(cls.url, Settings(call_tools=True))
        cls.findings = pf.run()
        cls.f = by_id(cls.findings)

    @classmethod
    def tearDownClass(cls):
        cls.server.should_exit = True

    def test_no_failures_and_high_score(self):
        self.assertEqual([x.title for x in self.findings if x.level == "fail"], [])
        self.assertGreaterEqual(score(self.findings), 95)

    def test_negotiates_current_protocol(self):
        self.assertEqual(self.f["P1"][0].level, "pass")
        self.assertIn("2025-11-25", self.f["P1"][0].title)

    def test_stateful_session_is_followed(self):
        self.assertIn("Stateful", self.f["P5"][0].title)
        self.assertEqual(self.f["P7"][0].level, "pass")

    def test_only_read_only_tool_is_called_with_example_args(self):
        spoken = self.f["V6"]
        self.assertEqual(len(spoken), 1)
        self.assertIn("get_forecast", spoken[0].title)
        self.assertIn("ready-to-speak", spoken[0].title)

    def test_annotations_and_parameter_docs_pass(self):
        for id in ("Q5", "Q7", "Q8"):
            self.assertEqual(self.f[id][0].level, "pass", id)
        self.assertNotIn("Q9", self.f)

    def test_cli_exit_code_and_markdown(self):
        out = io.StringIO()
        import contextlib
        import tempfile
        with tempfile.NamedTemporaryFile(suffix=".md") as md, contextlib.redirect_stdout(out):
            code = main([self.url, "--markdown", md.name])
            report = open(md.name).read()
        self.assertEqual(code, 0)
        self.assertIn("Score", out.getvalue())
        self.assertIn("| ✓ |", report)


class Bad(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server, port = serve(bad)
        cls.url = f"http://127.0.0.1:{port}/mcp"
        cls.findings = Preflight(cls.url, Settings(call_tools=True, warn_latency=0.2)).run()
        cls.f = by_id(cls.findings)

    @classmethod
    def tearDownClass(cls):
        cls.server.should_exit = True

    def level(self, id):
        return self.f[id][0].level

    def test_old_protocol_fails(self):
        self.assertEqual(self.level("P1"), "fail")
        self.assertIn("2025-03-26", self.f["P1"][0].title)

    def test_crashes_are_reported(self):
        self.assertEqual(self.level("P8"), "fail")
        self.assertEqual(self.level("P9"), "fail")

    def test_tool_definition_problems(self):
        self.assertEqual(self.level("Q4"), "fail")
        self.assertIn("get weather!", self.f["Q4"][0].detail)
        self.assertEqual(self.level("Q6"), "fail")
        self.assertIn("city", self.f["Q6"][0].detail)
        self.assertEqual(self.level("Q5"), "warn")
        self.assertIn("send_alert", self.f["Q9"][0].detail)

    def test_missing_instructions_and_slow_list(self):
        self.assertEqual(self.level("P4"), "warn")
        self.assertEqual(self.f["V2"][0].level, "warn")

    def test_long_markdown_answer_is_hard_to_speak(self):
        spoken = self.f["V6"][0]
        self.assertEqual(spoken.level, "warn")
        for part in ("words", "markdown", "raw links"):
            self.assertIn(part, spoken.detail)

    def test_cli_fails_build(self):
        import contextlib
        with contextlib.redirect_stdout(io.StringIO()) as out:
            code = main([self.url, "--json"])
        self.assertEqual(code, 1)
        self.assertLess(json.loads(out.getvalue())["score"], 60)


class Unreachable(unittest.TestCase):
    def test_wrong_url_fails_cleanly(self):
        f = Preflight("http://127.0.0.1:9/mcp", timeout=2).run()
        self.assertEqual([x.id for x in f if x.level == "fail"], ["T2"])

    def test_plain_http_remote_fails(self):
        f = Preflight("http://example.invalid/mcp", timeout=2).run()
        self.assertEqual(f[0].level, "fail")


class Parsing(unittest.TestCase):
    def test_sse_and_batches(self):
        sse = b'event: message\ndata: {"jsonrpc":"2.0","id":1,"result":{}}\n\n'
        self.assertEqual(parse_messages("text/event-stream", sse)[0]["id"], 1)
        self.assertEqual(len(parse_messages("application/json", b'[{"id":1},{"id":2}]')), 2)

    def test_markdown_escapes_pipes(self):
        from mcp_preflight.checks import Finding
        md = markdown_report("u", {}, [Finding("X", "Tools", "warn", "a|b", "c", "d")])
        self.assertIn("a\\|b", md)


if __name__ == "__main__":
    unittest.main()
