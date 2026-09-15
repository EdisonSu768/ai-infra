"""Time and audit an unmodified ServingCast main invocation in the fixed worktree."""

import hashlib
import importlib.metadata
import json
import logging
import os
from pathlib import Path
import runpy
import subprocess
import sys
import time
from datetime import datetime, timezone


root = Path(__file__).resolve().parent
platform = sys.argv[1]
assert platform in ('a2', 'a3')
case = root / platform
source = Path('/private/tmp/msmodeling-glm53-pp')
os.chdir(source)
sys.path.insert(0, str(source))
logging.getLogger('serving_cast').setLevel(logging.DEBUG)

from serving_cast.config import Config

common = Config._parse_common_config(str(case / 'common.yaml'))
instances = Config._parse_instance_config(str(case / 'instances.yaml'))
assert {instance.pd_role for instance in instances} == {'prefill', 'decode'}
assert (common.load_gen.num_requests, common.load_gen.num_input_tokens, common.load_gen.num_output_tokens) == (8, 3500, 1500)
assert common.model_config.enable_kv_transfer_modeling
if platform == 'a3':
    prefill = next(instance for instance in instances if instance.pd_role == 'prefill')
    assert (prefill.parallel_config.pp_size, prefill.parallel_config.tp_size) == (2, 16)
    assert list(prefill.parallel_config.pp_layer_partition) == [41, 37]

def source_hashes():
    return {str(path.relative_to(source)): hashlib.sha256(path.read_bytes()).hexdigest()
            for folder in ('serving_cast', 'tensor_cast', 'cli') for path in sorted((source / folder).rglob('*.py'))}

initial_source_hashes = source_hashes()
sys.argv = ['serving_cast.main', f'--instance_config_path={case / "instances.yaml"}',
            f'--common_config_path={case / "common.yaml"}', f'--output_json={case / "result.json"}']
metadata = {
    'run_id': root.name, 'platform': platform, 'pid': os.getpid(),
    'commit': subprocess.check_output(['git', 'rev-parse', 'HEAD'], text=True).strip(),
    'source_snapshot': str(source), 'local_patch_sha256': hashlib.sha256((root / 'source.patch').read_bytes()).hexdigest(),
    'python': sys.version,
    'packages': {name: importlib.metadata.version(name) for name in ('torch', 'transformers', 'salabim', 'numpy')},
    'argv': sys.argv,
    'config_sha256': {name: hashlib.sha256((case / name).read_bytes()).hexdigest() for name in ('common.yaml', 'instances.yaml')},
    'model_config_sha256': hashlib.sha256((Path(common.model_config.name) / 'config.json').read_bytes()).hexdigest(),
    'started_at': datetime.now(timezone.utc).isoformat(), 'status': 'running',
}
(case / 'run.json').write_text(json.dumps(metadata, indent=2) + '\n')
print(json.dumps(metadata), flush=True)
started = time.perf_counter()
try:
    runpy.run_module('serving_cast.main', run_name='__main__')
    result = json.loads((case / 'result.json').read_text())
    overall = result['overall_summary']
    assert (overall['total_requests'], overall['total_input_tokens'], overall['total_output_tokens']) == (8, 28000, 12000)
    assert source_hashes() == initial_source_hashes, 'Source changed during simulation'
    metadata['status'] = 'completed'
except BaseException as error:
    metadata['status'] = 'failed'
    metadata['error'] = repr(error)
    raise
finally:
    metadata['elapsed_seconds'] = time.perf_counter() - started
    metadata['finished_at'] = datetime.now(timezone.utc).isoformat()
    (case / 'run.json').write_text(json.dumps(metadata, indent=2) + '\n')
    (case / 'source-hashes.json').write_text(json.dumps(initial_source_hashes, indent=2) + '\n')
    print(json.dumps(metadata), flush=True)
