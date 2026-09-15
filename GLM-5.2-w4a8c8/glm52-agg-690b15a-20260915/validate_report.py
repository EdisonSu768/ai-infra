"""Validate the standalone report and every embedded evidence attachment."""

import base64
import json
from collections import Counter
from html.parser import HTMLParser
from pathlib import Path

root = Path(__file__).resolve().parent
report = root.parent / "glm52_agg_simulation_report.html"


class Checker(HTMLParser):
    def __init__(self):
        super().__init__()
        self.stack = []
        self.counts = Counter()
        self.downloads = 0
        self.evidence = ""

    def handle_starttag(self, tag, attrs):
        self.counts[tag] += 1
        attrs = dict(attrs)
        if tag not in {"meta", "br", "hr", "img", "input", "link", "source", "wbr"}:
            self.stack.append(tag)
        if tag == "a":
            name = attrs["download"]
            data = attrs["href"]
            assert data.startswith("data:application/octet-stream;base64,")
            assert base64.b64decode(data.split(",", 1)[1], validate=True) == (root / name).read_bytes()
            self.downloads += 1

    def handle_endtag(self, tag):
        assert self.stack and self.stack.pop() == tag, f"Unbalanced {tag}"

    def handle_data(self, data):
        if self.stack and self.stack[-1] == "script":
            self.evidence += data


checker = Checker()
checker.feed(report.read_text())
assert not checker.stack
assert checker.counts["html"] == checker.counts["style"] == checker.counts["h1"] == 1
assert checker.downloads == 9
evidence = json.loads(checker.evidence)
result = json.loads((root / "result.json").read_text())
metadata = json.loads((root / "metadata.json").read_text())
verification = json.loads((root / "verification.json").read_text())
assert evidence["result"] == result
assert evidence["metadata"] == metadata
assert evidence["verification"] == verification
assert metadata["status"] == "completed"
assert result["overall_summary"]["total_requests"] == 8
assert result["overall_summary"]["total_input_tokens"] == 28000
assert result["overall_summary"]["total_output_tokens"] == 12000
print("HTML structure, 9 embedded attachments, and GLM-5.2 evidence: passed")
