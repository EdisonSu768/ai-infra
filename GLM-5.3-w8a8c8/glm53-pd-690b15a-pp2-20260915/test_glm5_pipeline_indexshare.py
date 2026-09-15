"""GLM5 PP boundaries preserve index sharing, cache ownership and communication."""

import json
from collections import Counter
from pathlib import Path

import pytest
import torch

from tensor_cast.core import model_runner as runner_module
from tensor_cast.core.input_generator import RequestInfo, generate_inputs_varlen
from tensor_cast.core.user_config import UserInputConfig
from tensor_cast.layers.glm5 import resolve_glm5_indexer_source_layer
from tests.regression.tensor_cast.conftest import get_session_model


@pytest.mark.parametrize("mtp", [0, 1])
def test_indexshare_crosses_pipeline_stage_without_recomputation(tmp_path, monkeypatch, mtp):
    fixture = Path(__file__).resolve().parents[2] / "assets/model_config/glm5/config.json"
    config = json.loads(fixture.read_text())
    config.update(num_hidden_layers=4, indexer_types=["full", "shared", "shared", "full"])
    (tmp_path / "config.json").write_text(json.dumps(config))
    monkeypatch.setattr(runner_module, "build_model", get_session_model)
    runner = runner_module.ModelRunner(
        UserInputConfig(
            model_id=str(tmp_path),
            device="TEST_DEVICE",
            world_size=2,
            tp_size=1,
            pp_size=2,
            dp_size=1,
            ep_size=1,
            pp_layer_partition=(2, 2),
            num_mtp_tokens=mtp,
            disable_repetition=True,
        )
    )
    batch = [RequestInfo(query_len=16, seq_len=16, is_decode=False, num_input_tokens=16, num_output_tokens=8)]
    inputs = generate_inputs_varlen(runner.model, batch, block_size=128)
    assert set(inputs["indexer_cache_by_layers"]) == {0, 3} | set(range(4, 4 + mtp))
    assert runner.model.stages[0].model.model_config.pipeline_indexer_output
    assert runner.model.stages[1].model.model_config.pipeline_indexer_input

    calls = Counter()
    transfers = []

    def observe(runtime):
        for event in runtime.event_list:
            calls[str(event.op_invoke_info.func)] += 1
            if event.op_invoke_info.func == torch.ops.tensor_cast.pipeline_send_recv.default:
                transfers.append(event.op_invoke_info.args[0])

    metrics = runner.run_inference(batch, with_sampler=True, runtime_observer=observe)
    assert calls["tensor_cast.dsa_indexer.default"] == 2 + mtp
    assert calls["tensor_cast.pipeline_send_recv.default"] == 2
    assert transfers[0].is_floating_point()
    assert not transfers[1].is_floating_point()
    assert metrics.pipeline_profile.stages[0].outgoing_payload_bytes == sum(
        tensor.numel() * tensor.element_size() for tensor in transfers
    )
    assert metrics.execution_time_s["analytic"] > 0
    assert metrics.pipeline_profile.pp_size == 2


def test_leading_shared_indexer_requires_explicit_pipeline_input():
    with pytest.raises(ValueError, match="no preceding full"):
        resolve_glm5_indexer_source_layer(["shared", "full"], 0)
    assert resolve_glm5_indexer_source_layer(["shared", "full"], 0, allow_external_source=True) == -1
