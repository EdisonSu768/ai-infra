import difflib
import hashlib
import json
import math
from pathlib import Path
import re
import subprocess
import sys
from datetime import datetime, timezone

root = Path(__file__).resolve().parent
metadata = json.loads((root / "metadata.json").read_text())
log = (root / "run.log").read_text(errors="replace")
times = [float(x) for x in re.findall(r"\[\s*(\d+\.\d+)\]\[T", log)]
progress = {
    "status": metadata["status"],
    "wall_minutes": round((datetime.now(timezone.utc) - datetime.fromisoformat(metadata["started_at"])).total_seconds() / 60, 1),
    "simulated_seconds": max(times, default=0),
    "computed_batches": log.count("process batch, batch length:"),
    "completed_requests": len(re.findall(r"decode done callback \d+", log)),
    "memory_accounting_warnings": log.count("Negative activation memory estimate"),
}
print(json.dumps(progress))
if "--report" not in sys.argv:
    raise SystemExit(0)
assert metadata["status"] == "completed", metadata
assert progress["completed_requests"] == 8, progress
for name, expected in metadata["sha256"].items():
    assert hashlib.sha256((root / name).read_bytes()).hexdigest() == expected, name
result = json.loads((root / "result.json").read_text())
metrics = result["per_metric_summary"]
overall = result["overall_summary"]
assert overall["total_requests"] == 8
assert overall["total_input_tokens"] == 28000
assert overall["total_output_tokens"] == 12000
assert all(math.isfinite(value) and value >= 0 for value in overall.values())
assert math.isclose(overall["output_token_throughput(tok/s)"], 12000 / overall["benchmark_duration(s)"])
assert math.isclose(metrics["CLIENT_TTFT(s)"]["AVERAGE"], metrics["SERVER_TTFT(s)"]["AVERAGE"] + metrics["ADMISSION_WAIT(s)"]["AVERAGE"])

changes = []
for relative in ("serving_cast/config.py", "serving_cast/model_runner.py"):
    before = subprocess.check_output(["git", "show", f"{metadata['source_commit']}:{relative}"], cwd="/Users/szg/Github/msmodeling", text=True)
    after = (root / "source" / relative).read_text()
    changes.extend(difflib.unified_diff(before.splitlines(keepends=True), after.splitlines(keepends=True), fromfile=f"a/{relative}", tofile=f"b/{relative}"))
(root / "source.patch").write_text("".join(changes))

rows = []
for label, key, scale in (
    ("客户端 TTFT (s)", "CLIENT_TTFT(s)", 1),
    ("服务端 TTFT (s)", "SERVER_TTFT(s)", 1),
    ("准入等待 (s)", "ADMISSION_WAIT(s)", 1),
    ("TPOT (ms/token)", "TPOT(s)", 1000),
    ("端到端时延 (s)", "E2E_TIME(s)", 1),
):
    rows.append("| " + label + " | " + " | ".join(f"{metrics[key][stat] * scale:.3f}" for stat in ("AVERAGE", "MEDIAN", "P90", "P99")) + " |")
report = f"""# GLM-5.2 W4A8C8 聚合部署仿真

状态：完整完成 8/8 请求。所有性能数值均为 **predicted / pending runtime validation**。
本次覆盖固定负载下的服务调度和模型性能估算，不是部署验收或容量寻优。

## 配置

| 项目 | 本次实际配置 |
| --- | --- |
| 原始清单 | [deployment.yaml](deployment.yaml) |
| 模型 | GLM-5.2；78 层，256 routed experts，Top-8；仅使用本地 config.json |
| 拓扑 | 16 设备，TP8 × DP2，EP16，PP1；一个聚合服务组 |
| 硬件画像 | ATLAS_800_A2_376T_64G；64 GiB/设备；尚未核实与目标 910B3 完全对应 |
| 负载 | 3500 输入 / 1500 输出；8 请求；配置 2 req/s；全局并发 4 |
| 调度 | block_size=128；max_tokens_budget=4096（来自部署清单） |
| 量化 | 专家 W4A8_DYNAMIC；非专家 W8A8_DYNAMIC；attention INT8；lm_head 不量化 |
| MTP | 5 speculative tokens；接受率 [0.9,0.6,0.4,0.2,0.0]；模型每步平均接受 3.1 tokens |
| 方法 | analytic；do_compile=false；interpolation=false；单进程；nice=10 |
| 源码 | `{metadata['source_commit']}`；独立快照，临时补丁见 [source.patch](source.patch) |

## 预测结果

| 指标 | 平均 | P50 | P90 | P99 |
| --- | ---: | ---: | ---: | ---: |
{chr(10).join(rows)}

- 总输出吞吐：**{overall['output_token_throughput(tok/s)']:.3f} token/s**；实际完成请求吞吐：**{overall['request_throughput(req/s)']:.5f} req/s**。
- 输入/输出对账：28,000 / 12,000 tokens；8 次完成回调；{progress['computed_batches']} 次 batch 计算。
- 仿真时间跨度：{overall['benchmark_duration(s)']:.3f} s；本机实际计算耗时：{metadata['elapsed_seconds'] / 60:.2f} 分钟。

## 结果边界

- 2 req/s 是固定间隔注入参数。达到并发 4 后，当前实现暂停后续注入；这不是持续开环压测，8 请求不足以代表稳态容量或稳定的尾延迟。
- 量化使用框架的通用混合 W4/W8 + INT8 attention 建模；未逐层复现量化配方中 C8 排除层、投影例外及真实 checkpoint 的全部元数据。本地没有权重分片，因此未测量真实权重大小。
- 未复现 vLLM-Ascend v0.23.0 的 FULL_DECODE_ONLY 图回放、MC2 kernel、shared-expert overlap、网关及真实跨节点网络；profile 内的通信效率为框架默认值。TensorCast do_compile=false 不等同于部署中的图执行方式。
- 出现 {progress['memory_accounting_warnings']} 次 negative activation memory estimate；框架将负激活估算钳制为 0。未把 YAML 的 gpu-memory-utilization=0.95、图内存、40K 上下文上限转化为真实显存验收，不能据此证明可装载或无 OOM。

## 复现与证据

```sh
nice -n 10 /Users/szg/Github/msmodeling/.venv/bin/python -u {root / 'run.py'}
```

[完整配置](common.yaml) · [实例拓扑](instances.yaml) · [指标 JSON](result.json) · [运行元数据](metadata.json) · [日志](run.log)

运行脚本将 YAML 量化字符串转换为 TensorCast 枚举。临时源码补丁仅增加非专家量化字段及其传递；未改变调度或延迟计算算法。源码快照位于 `{root / 'source'}`；报告目录保留源码版本号、补丁、部署清单、模型配置与日志。未修改主仓库源代码，未操作 Kubernetes 集群。
"""
(root / "report.md").write_text(report, encoding="utf-8")
(root / "verification.json").write_text(json.dumps({**progress, "config_hashes_verified": True, "request_and_token_counts_verified": True, "ttft_identity_verified": True, "throughput_identity_verified": True}, indent=2) + "\n")
print(root / "report.md")
