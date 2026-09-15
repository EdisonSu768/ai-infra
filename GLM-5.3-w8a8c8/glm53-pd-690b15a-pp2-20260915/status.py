"""Read run status without touching either simulation process."""

import json
import re
from datetime import datetime, timezone
from pathlib import Path

root = Path(__file__).resolve().parent
for platform in ('a2', 'a3'):
    case = root / platform
    meta = json.loads((case / 'run.json').read_text())
    log = (case / 'run.log').read_text(errors='replace')
    batches = len(re.findall('process batch, batch length:', log))
    times = re.findall(r'^\[\s*([\d.]+)\]', log, re.MULTILINE)
    finished = len(re.findall(r'decode done callback \d+', log))
    seconds = meta.get('elapsed_seconds', (datetime.now(timezone.utc) - datetime.fromisoformat(meta['started_at'])).total_seconds())
    print(json.dumps(dict(platform=platform.upper(), status=meta['status'],
                          elapsed_min=round(seconds / 60, 1), batches=batches,
                          completed_requests=finished, last_simulated_s=float(times[-1]) if times else 0,
                          traceback='Traceback (most recent call last)' in log)))
