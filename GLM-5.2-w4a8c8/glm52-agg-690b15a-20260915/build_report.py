"""Build the standalone GLM-5.2 simulation report from archived evidence."""

import base64
import hashlib
import html
import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

root = Path(__file__).resolve().parent
model_dir = root.parent
report_path = model_dir / "glm52_agg_simulation_report.html"
esc = html.escape

result = json.loads((root / "result.json").read_text())
metadata = json.loads((root / "metadata.json").read_text())
verification = json.loads((root / "verification.json").read_text())
instances = (root / "instances.yaml").read_text()
common = (root / "common.yaml").read_text()
deployment = (root / "deployment.yaml").read_text()
quantization = (root / "quantization-recipe.yaml").read_text()
model_config = (root / "model/config.json").read_text()

assert metadata["status"] == "completed"
overall = result["overall_summary"]
assert (overall["total_requests"], overall["total_input_tokens"], overall["total_output_tokens"]) == (8, 28000, 12000)
assert verification["completed_requests"] == 8
for metric in result["per_metric_summary"].values():
    assert all(isinstance(value, (int, float)) and value >= 0 for value in metric.values())
for name, digest in metadata["sha256"].items():
    assert hashlib.sha256((root / name).read_bytes()).hexdigest() == digest


def metric(key, stat="AVERAGE", scale=1):
    return result["per_metric_summary"][key][stat] * scale


def number(value, digits=3):
    return f"{value:,.{digits}f}"


def attachment(name, label):
    data = base64.b64encode((root / name).read_bytes()).decode()
    return f'<a download="{esc(name)}" href="data:application/octet-stream;base64,{data}">{esc(label)}</a>'


def local_time(value):
    return datetime.fromisoformat(value).astimezone(timezone(timedelta(hours=8))).strftime("%Y-%m-%d %H:%M:%S")


def elapsed(seconds):
    minutes, remainder = divmod(round(seconds), 60)
    hours, minutes = divmod(minutes, 60)
    return (f"{hours} 小时 " if hours else "") + f"{minutes} 分 {remainder} 秒"


metrics = [
    ("请求 E2E 平均", "E2E_TIME(s)", "AVERAGE", 1, "s"),
    ("请求 E2E P99", "E2E_TIME(s)", "P99", 1, "s"),
    ("客户端 TTFT 平均", "CLIENT_TTFT(s)", "AVERAGE", 1000, "ms"),
    ("服务端 TTFT 平均", "SERVER_TTFT(s)", "AVERAGE", 1000, "ms"),
    ("准入等待平均", "ADMISSION_WAIT(s)", "AVERAGE", 1, "s"),
    ("TPOT 平均", "TPOT(s)", "AVERAGE", 1000, "ms/token"),
    ("TPOT P99", "TPOT(s)", "P99", 1000, "ms/token"),
    ("整体输出吞吐", "output_token_throughput(tok/s)", "OVERALL", 1, "token/s"),
    ("完成请求吞吐", "request_throughput(req/s)", "OVERALL", 1, "req/s"),
    ("整轮模拟时间跨度", "benchmark_duration(s)", "OVERALL", 1, "s"),
]
rows = []
for label, key, stat, scale, unit in metrics:
    value = overall[key] if stat == "OVERALL" else metric(key, stat, scale)
    rows.append(f'<tr><th scope="row">{esc(label)}</th><td>{number(value)} <span class="unit">{unit}</span></td></tr>')

payload = json.dumps({"result": result, "metadata": metadata, "verification": verification}, ensure_ascii=False).replace("<", "\\u003c")
generated = datetime.now(timezone(timedelta(hours=8))).strftime("%Y-%m-%d %H:%M:%S %z")
repro_command = "cd " + metadata["source_snapshot"] + "\n" + " ".join(metadata["argv"])

report = f'''<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>GLM-5.2 W4A8C8 聚合仿真报告</title>
<style>
:root{{--ink:#20282c;--muted:#5b696b;--line:#d9e2e1;--green:#087d75;--red:#ac4164}}
*{{box-sizing:border-box}}body{{margin:0;background:#fff;color:var(--ink);font:15px/1.7 -apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC","Microsoft YaHei",sans-serif;letter-spacing:0}}
main{{max-width:1060px;margin:auto;padding:38px 26px 64px}}header{{padding:0 0 28px;border-bottom:3px solid var(--green)}}h1{{font-size:30px;line-height:1.4;margin:7px 0 12px}}h2{{font-size:21px;line-height:1.4;margin:0 0 14px}}p{{margin:9px 0}}.kicker{{font-size:13px;color:var(--green);font-weight:650}}.muted,.unit{{color:var(--muted)}}.unit{{font-size:12px}}section{{padding:27px 0;border-bottom:1px solid var(--line)}}.lead{{font-size:17px}}.notice{{background:#f4f8f7;border-left:3px solid var(--green);padding:12px 16px;margin-top:15px}}.limits{{border-left-color:var(--red);background:#fcf6f8}}.summary{{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:14px;margin:22px 0 0}}.summary div{{border-top:1px solid var(--line);padding-top:12px}}.summary strong{{display:block;font-size:22px;font-variant-numeric:tabular-nums}}.summary span{{color:var(--muted);font-size:13px}}table{{border-collapse:collapse;width:100%;font-size:14px}}td,th{{border-bottom:1px solid var(--line);padding:10px 12px;text-align:left;vertical-align:top}}thead th{{background:#f0f5f4}}tbody th{{font-weight:500}}td{{font-variant-numeric:tabular-nums}}.table-wrap{{overflow-x:auto}}.wide{{min-width:720px}}code,pre{{font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-size:12px}}code{{background:#eef2f2;padding:2px 4px;overflow-wrap:anywhere}}pre{{padding:16px;background:#f0f4f3;overflow:auto;line-height:1.65;white-space:pre}}a{{color:#12685f;text-underline-offset:3px}}details{{margin:13px 0;border-top:1px solid var(--line);padding-top:12px}}summary{{cursor:pointer;font-weight:600}}.small{{font-size:13px}}li{{margin:7px 0}}footer{{padding-top:22px;color:var(--muted);font-size:12px;overflow-wrap:anywhere}}
@media(max-width:650px){{main{{padding:24px 16px 40px}}h1{{font-size:24px}}.summary{{grid-template-columns:1fr 1fr}}td,th{{padding:9px 8px}}}}
@media print{{main{{max-width:none;padding:0}}body{{font-size:11px}}section{{break-inside:avoid}}details{{display:none}}.table-wrap{{overflow:visible}}a{{color:inherit}}}}
</style></head><body><main>
<header><div class="kicker">固定负载 · 聚合仿真 · analytic 预测</div><h1>GLM-5.2 W4A8C8 聚合仿真报告</h1>
<p class="lead">本次完成 8 个请求，每请求 3500 输入 / 1500 输出 token。</p>
<p class="muted">代码基线 <code>{esc(metadata['source_commit'])}</code> + 非专家量化字段传递补丁 · {local_time(metadata['started_at'])}（UTC+8） · ServingCast / TensorCast</p>
<div class="summary"><div><strong>8 / 8</strong><span>完成请求</span></div><div><strong>16</strong><span>计算设备</span></div><div><strong>28,000</strong><span>总输入 token</span></div><div><strong>12,000</strong><span>总输出 token</span></div></div></header>
<section><h2>结果与范围</h2><p>结果来自一次完整的固定负载回放，使用一个 TP8 × DP2 × EP16 聚合服务组。所有性能数值均为 <strong>predicted / pending runtime validation</strong>，用于仿真筛选，不代表实卡验收或容量寻优。</p>
<div class="notice"><strong>运行状态：完整完成。</strong>8/8 请求回调、28,000 输入 token 和 12,000 输出 token 均已对账；本机计算耗时 {elapsed(metadata['elapsed_seconds'])}，模拟时间跨度 {number(overall['benchmark_duration(s)'])} 秒。</div>
<div class="table-wrap"><table><thead><tr><th>指标</th><th>本次结果</th></tr></thead><tbody>{''.join(rows)}</tbody></table></div>
<p class="small muted">P99 仅由本轮 8 个请求计算，不代表稳定高并发尾延迟。整体输出吞吐 = 12,000 / 整轮模拟时间跨度，不是最大容量或满足 SLO 的服务吞吐。</p></section>
<section><h2>拓扑与负载</h2><div class="table-wrap"><table class="wide"><thead><tr><th>配置</th><th>本次实际值</th></tr></thead><tbody>
<tr><th>硬件画像</th><td>ATLAS_800_A2_376T_64G，16 设备，64 GiB/设备</td></tr>
<tr><th>并行策略</th><td>TP8 × DP2 × EP16，PP1；一个聚合服务组</td></tr>
<tr><th>模型</th><td>GLM-5.2，78 层，256 routed experts，Top-8，MTP 5</td></tr>
<tr><th>量化</th><td>专家 W4A8_DYNAMIC；非专家 W8A8_DYNAMIC；attention INT8；lm_head 不量化</td></tr>
<tr><th>负载</th><td>8 请求 × (3500 input / 1500 output)，固定请求间隔 2 req/s</td></tr>
<tr><th>调度</th><td>max_concurrency=4；max_tokens_budget=4096；block_size=128</td></tr>
<tr><th>计算方式</th><td>analytic；do_compile=false；enable_interpolate=false；enable_multi_process=false</td></tr>
<tr><th>MTP 接受率</th><td>[0.9, 0.6, 0.4, 0.2, 0.0]，模型每步平均接受 3.1 token</td></tr>
</tbody></table></div></section>
<section><h2>模型口径与未覆盖项</h2><ul>
<li><strong>量化：</strong>框架按通用混合 W4/W8 + INT8 attention 建模，未逐层复现量化配方中 C8 排除层、投影例外和真实 checkpoint 元数据；本地没有权重分片，因此未测量真实权重大小。</li>
<li><strong>运行时：</strong>未复现 vLLM-Ascend v0.23.0 的 FULL_DECODE_ONLY 图、MC2 kernel、shared-expert overlap、网关或真实跨节点网络；通信效率使用 profile 默认值。</li>
<li><strong>显存：</strong>出现 {verification['memory_accounting_warnings']:,} 次 negative activation memory estimate，框架将负激活估计钳制为 0。不能据此证明真实权重可装载或无 OOM。</li>
<li><strong>负载：</strong>达到并发 4 后，当前实现会暂停后续注入；8 请求不足以代表稳态容量或稳定尾延迟。</li>
</ul><div class="notice limits">这是一份完整执行的固定场景仿真报告；预测精度、目标硬件复现程度和生产 SLO 仍需实卡验证。</div></section>
<section><h2>证据与复现</h2><p>{attachment('result.json', '指标 JSON')} · {attachment('metadata.json', '运行元数据')} · {attachment('verification.json', '验证记录')}</p>
<p>{attachment('instances.yaml', '实例拓扑')} · {attachment('common.yaml', '负载配置')} · {attachment('deployment.yaml', '部署清单')}</p>
<p>{attachment('quantization-recipe.yaml', '量化配方')} · {attachment('model/config.json', '模型配置')} · {attachment('source.patch', '源码补丁')}</p>
<p class="small muted">日志和全部原始文件保存在 <code>glm52-agg-690b15a-20260915/</code>。本页内嵌 JSON、YAML、模型配置和补丁，可独立下载。</p>
<details><summary>执行环境</summary><p>Python {esc(metadata['python'].split()[0])}；torch {esc(metadata['packages']['torch'])}；transformers {esc(metadata['packages']['transformers'])}；salabim {esc(metadata['packages']['salabim'])}；numpy {esc(metadata['packages']['numpy'])}。</p><p>配置 SHA-256 已由生成脚本复核；本次完成 {verification['computed_batches']:,} 个 batch 计算，运行前后源码和配置证据均保留。</p></details>
<details><summary>等价复现命令</summary><pre>{esc(repro_command)}</pre></details></section>
<footer>模型配置 SHA-256：{hashlib.sha256((root / 'model/config.json').read_bytes()).hexdigest()}<br>报告生成于 {generated} · MindStudio-Modeling</footer>
<script id="simulation-evidence" type="application/json">{payload}</script></main></body></html>'''

report_path.write_text(report, encoding="utf-8")
print(report_path)
