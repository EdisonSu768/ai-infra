import hashlib
import importlib.metadata
import json
import logging
import os
from pathlib import Path
import runpy
import sys
import time
from datetime import datetime, timezone


root = Path(__file__).resolve().parent
sys.path.insert(0, str(root / "source"))
os.environ.setdefault("OMP_NUM_THREADS", "1")
os.environ.setdefault("MKL_NUM_THREADS", "1")
os.environ.setdefault("OPENBLAS_NUM_THREADS", "1")

from serving_cast.config import Config
from serving_cast.main import parse_command_line_args
from tensor_cast.core.quantization.datatypes import QuantizeAttentionAction, QuantizeLinearAction


sys.argv = [
    "serving_cast.main",
    f"--instance_config_path={root / 'instances.yaml'}",
    f"--common_config_path={root / 'common.yaml'}",
    f"--output_json={root / 'result.json'}",
]
config = Config(parse_command_line_args())
# The YAML loader leaves quantization as strings; TensorCast requires enums.
model = config.common_config.model_config
model.quantize_attention_action = QuantizeAttentionAction(model.quantize_attention_action)
model.quantize_linear_action = QuantizeLinearAction(model.quantize_linear_action)
model.quantize_non_expert_linear_action = QuantizeLinearAction(model.quantize_non_expert_linear_action)

logging.getLogger("serving_cast").setLevel(logging.DEBUG)
handler = logging.FileHandler(root / "events.log", mode="w")
handler.setFormatter(logging.Formatter("%(asctime)s %(name)s %(levelname)s %(message)s"))
logging.getLogger("serving_cast").addHandler(handler)
logging.getLogger("serving_cast").propagate = False

metadata = {
    "run_id": root.name,
    "pid": os.getpid(),
    "source_commit": "690b15a5e3d20f2829813987cf53a3c5e3eb0ceb",
    "source_snapshot": str(root / "source"),
    "source_patch": "ServingCast ModelConfig + runner forward quantize_non_expert_linear_action (2 added lines)",
    "config_bridge": "Convert YAML quantization strings to TensorCast enums before invoking main",
    "python": sys.version,
    "packages": {name: importlib.metadata.version(name) for name in ("torch", "transformers", "salabim", "numpy")},
    "argv": sys.argv,
    "sha256": {name: hashlib.sha256((root / name).read_bytes()).hexdigest() for name in ("common.yaml", "instances.yaml", "model/config.json", "run.py")},
    "started_at": datetime.now(timezone.utc).isoformat(),
    "status": "running",
}

def save_metadata():
    (root / "metadata.json").write_text(json.dumps(metadata, indent=2) + "\n")

save_metadata()
print(json.dumps(metadata, indent=2), flush=True)
started = time.monotonic()
try:
    runpy.run_module("serving_cast.main", run_name="__main__")
    result = json.loads((root / "result.json").read_text())
    overall = result["overall_summary"]
    assert overall["total_requests"] == 8
    assert overall["total_input_tokens"] == 28000
    assert overall["total_output_tokens"] == 12000
    metadata["status"] = "completed"
except BaseException as error:
    metadata["status"] = "failed"
    metadata["error"] = repr(error)
    raise
finally:
    metadata["elapsed_seconds"] = time.monotonic() - started
    metadata["finished_at"] = datetime.now(timezone.utc).isoformat()
    save_metadata()
