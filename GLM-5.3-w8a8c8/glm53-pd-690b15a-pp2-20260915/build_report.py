import base64
import hashlib
import html
import json
import math
import re
from datetime import datetime, timezone, timedelta
from pathlib import Path

import yaml

root = Path(__file__).resolve().parent
report_dir = root.parent
evidence_dir = root
evidence_dir.mkdir(parents=True, exist_ok=True)
esc = html.escape
cases = {}
for platform in ('a2', 'a3'):
    folder = root / platform
    metadata = json.loads((folder / 'run.json').read_text())
    result = json.loads((folder / 'result.json').read_text())
    assert metadata['status'] == 'completed'
    overall = result['overall_summary']
    assert (overall['total_requests'], overall['total_input_tokens'], overall['total_output_tokens']) == (8, 28000, 12000)
    for metric in result['per_metric_summary'].values():
        assert all(math.isfinite(value) and value >= 0 for value in metric.values())
    for name, expected in metadata['config_sha256'].items():
        assert hashlib.sha256((folder / name).read_bytes()).hexdigest() == expected
    log = (folder / 'run.log').read_text(errors='replace')
    cases[platform] = {
        'metadata': metadata, 'result': result,
        'common': yaml.safe_load((folder / 'common.yaml').read_text()),
        'instances': yaml.safe_load((folder / 'instances.yaml').read_text()),
        'memory_warnings': log.count('Negative activation memory estimate'),
        'computed_batches': len(re.findall('process batch, batch length:', log)),
        'completed_callbacks': len(re.findall(r'decode done callback \d+', log)),
    }
    assert cases[platform]['completed_callbacks'] == 8

assert cases['a2']['metadata']['commit'] == cases['a3']['metadata']['commit']
assert cases['a2']['metadata']['local_patch_sha256'] == cases['a3']['metadata']['local_patch_sha256']
assert hashlib.sha256((root / 'source.patch').read_bytes()).hexdigest() == cases['a3']['metadata']['local_patch_sha256']
prefill = next(item for item in cases['a3']['instances']['instance_groups'] if item['pd_role'] == 'prefill')
assert prefill['num_devices_per_instance'] == 32
assert (prefill['parallel_config']['pp_size'], prefill['parallel_config']['tp_size']) == (2, 16)
assert prefill['parallel_config']['pp_layer_partition'] == [41, 37]
source = Path(cases['a3']['metadata']['source_snapshot'])

def metric(platform, key, stat='AVERAGE'):
    return cases[platform]['result']['per_metric_summary'][key][stat]

def overall(platform, key):
    return cases[platform]['result']['overall_summary'][key]

def elapsed(value):
    seconds = round(value)
    hours, rem = divmod(seconds, 3600)
    minutes, seconds = divmod(rem, 60)
    return (f'{hours} 小时 ' if hours else '') + f'{minutes} 分 {seconds} 秒'

def local(value):
    return datetime.fromisoformat(value).astimezone(timezone(timedelta(hours=8))).strftime('%Y-%m-%d %H:%M:%S')

def attachment(platform, name, label):
    data = base64.b64encode((root / platform / name).read_bytes()).decode()
    download_name = name if platform == '.' else f'{platform}-{name}'
    return f'<a download="{download_name}" href="data:application/octet-stream;base64,{data}">{esc(label)}</a>'

metrics = [
    ('请求 E2E 平均', 'E2E_TIME(s)', 'AVERAGE', 1, 's'),
    ('请求 E2E P99', 'E2E_TIME(s)', 'P99', 1, 's'),
    ('客户端 TTFT 平均', 'CLIENT_TTFT(s)', 'AVERAGE', 1000, 'ms'),
    ('服务端 TTFT 平均', 'SERVER_TTFT(s)', 'AVERAGE', 1000, 'ms'),
    ('准入等待平均', 'ADMISSION_WAIT(s)', 'AVERAGE', 1, 's'),
    ('TPOT 平均', 'TPOT(s)', 'AVERAGE', 1000, 'ms/token'),
    ('TPOT P99', 'TPOT(s)', 'P99', 1000, 'ms/token'),
]
rows = ''.join('<tr><th scope="row">' + label + '</th>' + ''.join(f'<td>{metric(p, key, stat)*scale:,.3f} <span class="unit">{unit}</span></td>' for p in cases) + '</tr>' for label, key, stat, scale, unit in metrics)
for label, key, unit in (
    ('整体输出吞吐', 'output_token_throughput(tok/s)', 'token/s'),
    ('完成请求吞吐', 'request_throughput(req/s)', 'req/s'),
    ('整轮模拟时间跨度', 'benchmark_duration(s)', 's'),
):
    rows += '<tr><th scope="row">' + label + '</th>' + ''.join(f'<td>{overall(p, key):,.3f} <span class="unit">{unit}</span></td>' for p in cases) + '</tr>'

def chart(title, values, unit):
    maximum = max(values) * 1.12
    bars = []
    for i, (name, value, color) in enumerate(zip(('A2', 'A3 PP2'), values, ('#087d75', '#ac4164'))):
        y = 38 + i * 47
        width = 360 * value / maximum
        bars.append(f'<text x="0" y="{y+17}" font-size="14">{name}</text><rect x="80" y="{y}" width="{width:.2f}" height="26" fill="{color}"/><text x="{90+width:.2f}" y="{y+17}" font-size="13">{value:.2f}</text>')
    return f'<figure><figcaption>{title} <span class="unit">({unit})</span></figcaption><svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 530 126" role="img" aria-label="{title}: A2 {values[0]:.3f}, A3 {values[1]:.3f} {unit}"><line x1="80" x2="80" y1="30" y2="116" stroke="#bbc7c5"/>{"".join(bars)}</svg></figure>'

charts = chart('平均请求 E2E', [metric(p, 'E2E_TIME(s)') for p in cases], 's') + chart('整体输出吞吐', [overall(p, 'output_token_throughput(tok/s)') for p in cases], 'token/s')
timing_rows = ''.join(f'<tr><th scope="row">{p.upper()}</th><td>{local(c["metadata"]["started_at"])}</td><td>{local(c["metadata"]["finished_at"])}</td><td>{elapsed(c["metadata"]["elapsed_seconds"])}</td><td>{c["computed_batches"]:,}</td></tr>' for p,c in cases.items())
evidence_links = ''.join(f'<p><strong>{p.upper()}：</strong>{attachment(p,"result.json","结果 JSON")} · {attachment(p,"run.json","运行元信息")} · {attachment(p,"instances.yaml","实例配置")} · {attachment(p,"common.yaml","负载配置")}</p>' for p in cases)
config_details = ''.join(f'<details><summary>{p.upper()} 本次完整配置</summary><pre>{esc((root/p/"instances.yaml").read_text())}\n{esc((root/p/"common.yaml").read_text())}</pre></details>' for p in cases)
command = f'''cd {source}
/Users/szg/Github/msmodeling/.venv/bin/python -m serving_cast.main \\
  --instance_config_path={root / 'a2/instances.yaml'} \\
  --common_config_path={root / 'a2/common.yaml'} \\
  --output_json=/tmp/glm53-a2-replay.json

/Users/szg/Github/msmodeling/.venv/bin/python -m serving_cast.main \\
  --instance_config_path={root / 'a3/instances.yaml'} \\
  --common_config_path={root / 'a3/common.yaml'} \\
  --output_json=/tmp/glm53-a3-replay.json'''
payload = json.dumps(cases, ensure_ascii=False).replace('<', '\\u003c')
report = f'''<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>GLM-5.3 A2/A3 PD 分离正式仿真报告 · A3 PP2 TP16</title>
<style>
:root{{--ink:#20282c;--muted:#5b696b;--line:#d9e2e1;--green:#087d75;--red:#ac4164}}
*{{box-sizing:border-box}}body{{margin:0;background:#fff;color:var(--ink);font:15px/1.7 -apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC","Microsoft YaHei",sans-serif;letter-spacing:0}}
main{{max-width:1100px;margin:auto;padding:38px 26px 64px}}header{{padding:0 0 28px;border-bottom:3px solid var(--green)}}h1{{font-size:30px;line-height:1.4;margin:7px 0 12px;font-weight:700}}h2{{font-size:21px;line-height:1.4;margin:0 0 14px}}p{{margin:9px 0}}.kicker{{font-size:13px;color:var(--green);font-weight:650}}.muted,.unit{{color:var(--muted)}}.unit{{font-size:12px}}section{{padding:27px 0;border-bottom:1px solid var(--line)}}.lead{{font-size:17px}}.notice{{background:#f4f8f7;border-left:3px solid var(--green);padding:12px 16px;margin-top:15px}}.limits{{border-left-color:var(--red);background:#fcf6f8}}.summary{{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:14px;margin:22px 0 0}}.summary div{{border-top:1px solid var(--line);padding-top:12px}}.summary strong{{display:block;font-size:22px;font-variant-numeric:tabular-nums}}.summary span{{color:var(--muted);font-size:13px}}table{{border-collapse:collapse;width:100%;font-size:14px}}td,th{{border-bottom:1px solid var(--line);padding:10px 12px;text-align:left;vertical-align:top}}thead th{{background:#f0f5f4}}tbody th{{font-weight:500}}.metrics td{{font-variant-numeric:tabular-nums}}.metrics td:nth-child(2){{color:var(--green)}}.metrics td:nth-child(3){{color:var(--red)}}.table-wrap{{overflow-x:auto}}.wide{{min-width:760px}}.plots{{display:grid;grid-template-columns:1fr 1fr;gap:30px}}figure{{margin:18px 0 0;min-width:0}}figcaption{{font-weight:600;font-size:14px}}svg{{width:100%;height:auto;display:block}}svg text{{fill:var(--ink)}}code,pre{{font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-size:12px}}code{{background:#eef2f2;padding:2px 4px;overflow-wrap:anywhere}}pre{{padding:16px;background:#f0f4f3;overflow:auto;line-height:1.65;white-space:pre}}a{{color:#12685f;text-underline-offset:3px}}details{{margin:13px 0;border-top:1px solid var(--line);padding-top:12px}}summary{{cursor:pointer;font-weight:600}}.small{{font-size:13px}}li{{margin:7px 0}}footer{{padding-top:22px;color:var(--muted);font-size:12px;overflow-wrap:anywhere}}
@media(max-width:650px){{main{{padding:24px 16px 40px}}h1{{font-size:24px}}.summary{{grid-template-columns:1fr 1fr}}.plots{{grid-template-columns:1fr;gap:0}}td,th{{padding:9px 8px}}.metrics{{min-width:510px}}}}
@media print{{main{{max-width:none;padding:0}}body{{font-size:11px}}section{{break-inside:avoid}}details{{display:none}}.wide,.metrics{{min-width:0}}.table-wrap{{overflow:visible}}a{{color:inherit}}}}
</style></head><body><main>
<header><div class="kicker">固定负载 · 端到端 PD 仿真 · analytic 预测</div><h1>GLM-5.3 A2/A3 PD 分离仿真报告</h1>
<p class="lead">两组均完成 8 个请求，每请求 3500 输入 / 1500 输出 token。</p>
<p class="muted">代码基线 <code>{cases['a3']['metadata']['commit']}</code> + 本地 PP / IndexShare 修复补丁 · 2026-09-15 · ServingCast / TensorCast</p>
<div class="summary"><div><strong>8 / 8</strong><span>A2 完成请求</span></div><div><strong>8 / 8</strong><span>A3 PP2 场景完成请求</span></div><div><strong>28,000</strong><span>每组总输入 token</span></div><div><strong>12,000</strong><span>每组总输出 token</span></div></div></header>
<section><h2>结果与范围</h2><p>本报告仅使用本轮完整负载产生的新 JSON。每个平台都在一次 ServingCast 运行内包含 Prefill 池、KV 传输和 Decode 池。结果反映这组有限请求的端到端时延与吞吐。</p>
<div class="notice"><strong>A3 Prefill：PP2 × TP16，41/37 层切分。</strong>两组均配置 32 个 Prefill + 32 个 Decode 计算单元。A3 每个单元对应 DeviceProfile 中的 DIE。结果来自本次完整负载回放，旧的 TP16 单 stage 结果已清理。</div>
<div class="table-wrap"><table class="metrics"><thead><tr><th>指标</th><th>A2</th><th>A3（PP2 × TP16）</th></tr></thead><tbody>{rows}</tbody></table></div>{'<div class="plots">'+charts+'</div>'}
<p class="small muted">P99 仅由本轮 8 个请求计算，不代表稳定高并发下的尾延迟。整体输出吞吐 = 12,000 / 整轮模拟时间跨度；它不是最大容量或满足 SLO 的服务吞吐。</p></section>
<section><h2>仿真程序耗时</h2><div class="table-wrap"><table class="wide"><thead><tr><th>平台</th><th>开始（UTC+8）</th><th>结束（UTC+8）</th><th>本机实际耗时</th><th>计算批次</th></tr></thead><tbody>{timing_rows}</tbody></table></div>
<p>两组并行运行。这里使用计时器记录进入 <code>serving_cast.main</code> 到退出的墙钟耗时，包含初始化、预热和回放；它与上表预测的请求 E2E、整轮模拟时间不同。</p></section>
<section><h2>拓扑与负载</h2><p>部署清单里的 P/D 数量是节点数；ServingCast 的 <code>num_instances</code> 是一个完整并行逻辑池的副本数。本次每个角色设为 1 个逻辑池，节点数由并行度展开：A2 的 DP4 × TP8 和 DP8 × TP4 分别占 4P、4D 节点；A3 的 PP2 × TP16 和 DP16 × TP2 分别占 2P、2D 节点。</p><div class="table-wrap"><table class="wide"><thead><tr><th>配置</th><th>A2</th><th>A3</th></tr></thead><tbody>
<tr><th>DeviceProfile</th><td>ATLAS_800_A2_376T_64G</td><td>ATLAS_800_A3_752T_128G_DIE</td></tr><tr><th>Prefill 池</th><td>1 实例，32 单元；TP8 × DP4，EP32</td><td>1 实例，32 单元；PP2 × TP16 × DP1；每 stage EP16；41/37 层</td></tr><tr><th>Decode 池</th><td>1 实例，32 单元；TP4 × DP8，EP32</td><td>1 实例，32 单元；TP2 × DP16，EP32</td></tr><tr><th>MTP 验证窗口</th><td>3 个推测 token + 1；平均接受 2.9 token/步</td><td>5 个推测 token + 1；平均接受 3.1 token/步</td></tr><tr><th>接受率假设</th><td>[0.9, 0.6, 0.4]，仓库默认前三项</td><td>[0.9, 0.6, 0.4, 0.2, 0]，第 5 项不计加速收益</td></tr><tr><th>输入与输出</th><td colspan="2">8 请求 × (3500 input / 1500 output)，固定长度；配置请求速率 2 req/s</td></tr><tr><th>准入与调度</th><td colspan="2">全局 max_concurrency=4；max_tokens_budget=8192；block_size=128</td></tr><tr><th>计算方式</th><td colspan="2">analytic；do_compile=false；enable_interpolate=false；enable_multi_process=false；完整逐批次回放</td></tr><tr><th>预处理 / KV</th><td colspan="2">均开启；host2device 有效带宽 5 GB/s，device2device 有效带宽 2 GB/s（仓库默认参数）</td></tr></tbody></table></div>
<p class="small muted">A3 profile 名称中的 128G 指产品命名；代码定义的单个 DIE 为 64 GiB。本表的计算单元按该 profile 计数。2 req/s 为配置间隔，准入门限会推迟提交，未执行稳定开放到达率压测。</p>
<p class="small">接受率用于估算提前猜出的 token 有多少通过验证：平均每轮产出 = 1 + 各位置接受率之和。A3 为 1 + 0.9 + 0.6 + 0.4 + 0.2 + 0 = 3.1 token/轮；它不是请求成功率或回答准确率。</p></section>
<section><h2>模型口径与未覆盖项</h2><ul>
<li><strong>本地修复：</strong>上游 ServingCast 配置未透传 PP；GLM5 IndexShare 还缺少跨 stage 索引传递和 PP + MTP 的缓存元数据。已通过本地补丁补齐，未提交或合入上游。不能仅用上述 commit 复现，必须应用归档的 <code>source.patch</code>。</li>
<li><strong>PP 调度：</strong>两段分别建模权重、KV、算子和 stage 间 hidden states / top-k 索引传输。每个 ServingCast batch 仍依次通过各 stage，没有模拟多个 batch 在不同 stage 间同时推进的流水线调度；不能从本结果推导真实 PP 饱和吞吐或气泡率。</li>
<li><strong>量化：</strong>使用 GLM-5.3 的本地 78 层、256 专家配置，linear=W8A8_DYNAMIC；attention 量化关闭，未复现原部署的稀疏 SFA / LI C8 优化。文件名中的 W8A8C8 不代表本次已完整模拟 C8。</li>
<li><strong>算子性能：</strong>部分算子缺少性能属性时，框架采用默认内存带宽估计。本次没有使用实测 profiling 数据库，也未复现真实 vLLM 的编译融合图。</li>
<li><strong>P/D 独立设置：</strong>当前入口只有全局 model_config / serving_config；本次两边共享 MTP3（A2）或 MTP5（A3）。原清单的 Prefill 是 MTP1；P/D 的 max-num-seqs 和 token budget 也并非本次共享值。</li>
<li><strong>网络：</strong>本次使用字节数 / 有效带宽计算 KV 传输时间，带宽没有根据 A2 RoCE 或 A3 fabric 实测校准；未建模完整 Mooncake/KServe/网关实现。没有进行关闭 KV 的对照运行，不报告其净影响百分比。</li>
<li><strong>TTFT 定义：</strong>仓库在 Prefill 完成时记录 TTFT，早于 KV 传输完成；端到端 E2E 和后续 TPOT 包含该传输等待。这里的 TTFT 不等同于真实网关首字节或流式首 token 的测量。</li>
<li><strong>MTP 接受率：</strong>属于仿真输入假设，未经过目标业务语料校准。A3 第 5 项设为 0 保留其计算成本，只是不计其加速收益。</li>
<li><strong>显存记账：</strong>A2 出现 {cases['a2']['memory_warnings']:,} 次、A3 出现 {cases['a3']['memory_warnings']:,} 次 negative activation memory estimate 警告；框架将负激活估计钳制为 0。结果不能证明真实模型一定装得下。</li>
</ul><div class="notice">这是一份完整执行的固定场景仿真报告；预测精度、原始部署复现程度和生产 SLO 仍需实卡验证。没有用小样例外推长负载结果，也没有把阶段级 optimizer 结果当作端到端数据。</div></section>
<section><h2>证据与复现</h2>{evidence_links}<p>{attachment('.', 'source.patch', 'PP / IndexShare 源码补丁')}</p><p class="small">日志、部署清单、源码基线、补丁和验证记录归档于 <code>{esc(str(evidence_dir))}/</code>。本页内嵌的 JSON、YAML 和补丁可独立下载。</p>
<details><summary>执行环境与计时方式</summary><p>Python {esc(cases['a2']['metadata']['python'].split()[0])}；torch {esc(cases['a2']['metadata']['packages']['torch'])}；transformers {esc(cases['a2']['metadata']['packages']['transformers'])}；salabim {esc(cases['a2']['metadata']['packages']['salabim'])}。使用已安装虚拟环境和固定隔离工作树。</p><p>131 项回归测试及 15 个子测试通过；完整 78 层 A3 预热、3500-token Prefill 和末尾 Decode 检查通过。算子对账确认 Prefill 为 26 次 indexer 调用（21 个 full 层 + MTP5），stage 间包含 hidden states 与 top-k 索引两次传输；没有新增 indexer 重算。</p><p>外层脚本记录时间、开启 ServingCast 调试日志，并通过 runpy 执行 <code>serving_cast.main</code>。校验运行前后源码哈希一致，以及 8 个完成回调、28,000 输入、12,000 输出和配置 SHA-256。未执行全仓构建、全量测试或实卡验证。</p></details>
<details><summary>等价模块复现命令</summary><pre>{esc(command)}</pre></details>{config_details}</section>
<footer>模型配置 SHA-256：{cases['a2']['metadata']['model_config_sha256']}<br>报告生成于 {datetime.now(timezone(timedelta(hours=8))).strftime('%Y-%m-%d %H:%M:%S %z')} · MindStudio-Modeling</footer>
<script id="simulation-evidence" type="application/json">{payload}</script></main></body></html>'''
target = report_dir / 'glm53_pd_simulation_report.html'
target.write_text(report, encoding='utf-8')
(evidence_dir / 'results.json').write_text(json.dumps(cases, ensure_ascii=False, indent=2) + '\n')
print(target)
print(json.dumps({p: c['result']['overall_summary'] for p,c in cases.items()}, indent=2))
