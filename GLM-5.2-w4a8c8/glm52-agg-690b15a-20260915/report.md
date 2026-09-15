# GLM-5.2 W4A8C8 聚合部署仿真

[独立 HTML 报告](../glm52_agg_simulation_report.html)

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
| 源码 | `690b15a5e3d20f2829813987cf53a3c5e3eb0ceb`；独立快照，临时补丁见 [source.patch](source.patch) |

## 预测结果

| 指标 | 平均 | P50 | P90 | P99 |
| --- | ---: | ---: | ---: | ---: |
| 客户端 TTFT (s) | 7.633 | 1.561 | 16.410 | 47.524 |
| 服务端 TTFT (s) | 1.421 | 1.421 | 1.594 | 1.594 |
| 准入等待 (s) | 6.213 | 0.000 | 14.910 | 46.221 |
| TPOT (ms/token) | 33.220 | 33.220 | 33.635 | 33.635 |
| 端到端时延 (s) | 57.429 | 51.217 | 66.610 | 97.921 |

- 总输出吞吐：**115.421 token/s**；实际完成请求吞吐：**0.07695 req/s**。
- 输入/输出对账：28,000 / 12,000 tokens；8 次完成回调；1944 次 batch 计算。
- 仿真时间跨度：103.967 s；本机实际计算耗时：53.92 分钟。

## 结果边界

- 2 req/s 是固定间隔注入参数。达到并发 4 后，当前实现暂停后续注入；这不是持续开环压测，8 请求不足以代表稳态容量或稳定的尾延迟。
- 量化使用框架的通用混合 W4/W8 + INT8 attention 建模；未逐层复现量化配方中 C8 排除层、投影例外及真实 checkpoint 的全部元数据。本地没有权重分片，因此未测量真实权重大小。
- 未复现 vLLM-Ascend v0.23.0 的 FULL_DECODE_ONLY 图回放、MC2 kernel、shared-expert overlap、网关及真实跨节点网络；profile 内的通信效率为框架默认值。TensorCast do_compile=false 不等同于部署中的图执行方式。
- 出现 1940 次 negative activation memory estimate；框架将负激活估算钳制为 0。未把 YAML 的 gpu-memory-utilization=0.95、图内存、40K 上下文上限转化为真实显存验收，不能据此证明可装载或无 OOM。

## 复现与证据

```sh
nice -n 10 /Users/szg/Github/msmodeling/.venv/bin/python -u /private/tmp/msmodeling-glm52-w4a8c8-20260915/run.py
```

[完整配置](common.yaml) · [实例拓扑](instances.yaml) · [指标 JSON](result.json) · [运行元数据](metadata.json) · [日志](run.log)

运行脚本将 YAML 量化字符串转换为 TensorCast 枚举。临时源码补丁仅增加非专家量化字段及其传递；未改变调度或延迟计算算法。源码快照位于 `/private/tmp/msmodeling-glm52-w4a8c8-20260915/source`；报告目录保留源码版本号、补丁、部署清单、模型配置与日志。未修改主仓库源代码，未操作 Kubernetes 集群。
