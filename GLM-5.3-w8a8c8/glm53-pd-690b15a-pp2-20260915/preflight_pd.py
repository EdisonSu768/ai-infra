import argparse
import json
from collections import Counter

from serving_cast.config import Config
from serving_cast.model_runner import ModelRunner
from tensor_cast.core.input_generator import RequestInfo

root = '/Users/szg/Github/ai-infra/GLM-5.3-w8a8c8/glm53-pd-690b15a-pp2-20260915/a3'
config = Config(argparse.Namespace(
    instance_config_path=f'{root}/instances.yaml',
    common_config_path=f'{root}/common.yaml',
    enable_profiling=False,
))
for instance in config.instance_config_list:
    runner = ModelRunner(instance.parallel_config, instance.device_type, 0)
    blocks, block_size = runner.warmup()
    assert blocks > 0
    batch = [RequestInfo(
        seq_len=3500 if instance.pd_role == 'prefill' else 5000,
        query_len=3500 if instance.pd_role == 'prefill' else 6,
        is_decode=instance.pd_role == 'decode',
        num_input_tokens=3500, num_output_tokens=1500,
    )]
    calls = Counter()
    def observe(runtime):
        calls.update(str(event.op_invoke_info.func) for event in runtime.event_list)
    metrics = runner.tensor_cast_model_runner.run_inference(batch, with_sampler=True, runtime_observer=observe)
    assert metrics.execution_time_s['analytic'] > 0
    if instance.pd_role == 'prefill':
        assert metrics.pipeline_profile.pp_size == 2
        assert [(s.layer_start, s.layer_end) for s in metrics.pipeline_profile.stages] == [(0, 41), (41, 78)]
        assert calls['tensor_cast.dsa_indexer.default'] == 26
        assert calls['tensor_cast.pipeline_send_recv.default'] == 2
    print(json.dumps(dict(role=instance.pd_role, blocks=blocks, block_size=block_size,
                         time_s=metrics.execution_time_s,
                         stages=metrics.stage_latency_breakdown, indexer_calls=calls['tensor_cast.dsa_indexer.default'],
                         pp_transfer_calls=calls['tensor_cast.pipeline_send_recv.default'])), flush=True)
