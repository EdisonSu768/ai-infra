"""Check the standalone report against its local JSON and YAML evidence."""

import base64
from collections import Counter
from html.parser import HTMLParser
import json
from pathlib import Path

root = Path(__file__).resolve().parent
report = root.parent / 'glm53_pd_simulation_report.html'

class CheckReport(HTMLParser):
    def __init__(self):
        super().__init__()
        self.stack = []
        self.counts = Counter()
        self.downloads = 0
        self.evidence = ''

    def handle_starttag(self, tag, attributes):
        self.counts[tag] += 1
        attrs = dict(attributes)
        if tag not in {'meta', 'br', 'hr', 'img', 'input', 'link', 'source', 'wbr'}:
            self.stack.append(tag)
        if tag == 'a':
            name = attrs['download']
            target = root / name
            for platform in ('a2', 'a3'):
                if name.startswith(platform + '-'):
                    target = root / platform / name[len(platform) + 1:]
            href = attrs['href']
            assert href.startswith('data:application/octet-stream;base64,')
            assert base64.b64decode(href.split(',', 1)[1], validate=True) == target.read_bytes()
            self.downloads += 1

    def handle_endtag(self, tag):
        assert self.stack and self.stack.pop() == tag, f'Unbalanced {tag}'

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if self.stack and self.stack[-1] == tag:
            self.handle_endtag(tag)

    def handle_data(self, text):
        if self.stack and self.stack[-1] == 'script':
            self.evidence += text

checker = CheckReport()
checker.feed(report.read_text())
assert not checker.stack
assert checker.counts['html'] == checker.counts['style'] == checker.counts['h1'] == 1
assert checker.downloads == 9
evidence = json.loads(checker.evidence)
for platform in ('a2', 'a3'):
    assert evidence[platform]['result'] == json.loads((root / platform / 'result.json').read_text())
    assert evidence[platform]['metadata']['status'] == 'completed'
    assert evidence[platform]['completed_callbacks'] == 8
    assert evidence[platform]['result']['overall_summary']['total_output_tokens'] == 12000
print('HTML structure, 9 embedded attachments, and A2/A3 evidence: passed')
