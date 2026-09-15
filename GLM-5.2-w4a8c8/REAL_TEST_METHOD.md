# GLM-5.2-w4a8c8 真实测试方法

## 5. aiperf 固定负载测试

压测客户端使用独立 Pod 或独立机器，不要在推理 Pod 内 `kubectl exec` 运行 aiperf。客户端与引擎共用 host memory 可能导致误判为引擎 OOM。

以下命令使用 aiperf 0.12.0 的合成输入，三次 profile run 每次发送 8 个 profiling 请求：

```bash
aiperf profile \
  --url http://<leader-service>:8000 \
  --model glm-52 \
  --endpoint-type chat \
  --streaming \
  --request-rate 2 \
  --request-rate-mode constant \
  --concurrency 4 \
  --request-count 8 \
  --isl 3500 \
  --isl-stddev 0 \
  --osl 1500 \
  --osl-stddev 0 \
  --num-profile-runs 3 \
  --profile-run-cooldown-seconds 30 \
  --use-server-token-count \
  --random-seed 42 \
  --request-timeout-seconds 1800 \
  --output-artifact-dir ./reports/real-<date>/baseline \
  --export-level records \
  --ui-type none
```

`--isl 3500` 和 `--osl 1500` 是目标长度。`--osl 1500` 是输出上限，模型可能提前 EOS；不要把实际输出强行当成 1500。最终输入/输出 token 数以服务端 `usage` 为准。若必须让每个请求输出正好 1500，再单独增加 `ignore_eos=true` 变体，并在报告中注明它改变了业务行为：

```text
--extra-inputs '{"ignore_eos":true}'
```

## 7. 与仿真对比

仿真报告中的基线预测为：

| 指标 | 仿真预测 |
| --- | ---: |
| 平均客户端 TTFT | 7.633 s |
| 平均服务端 TTFT | 1.421 s |
| 平均准入等待 | 6.213 s |
| 平均 TPOT | 33.220 ms/token |
| 平均 E2E | 57.429 s |
| 输出吞吐 | 115.421 token/s |

真实结果必须采用相同定义：

```text
client_ttft = first_token_time - request_start_time
tpot = (last_token_time - first_token_time) / (completion_tokens - 1)
e2e = last_token_time - request_start_time
output_throughput = total_output_tokens / (last_finish_time - first_start_time)
```

每个变体至少汇总：平均值、P50/P90/P99、实际输入/输出 token 数、错误率、实际请求速率、Pod/EngineCore 重启次数、NPU/host OOM 和 HCCL/RoCE 错误。

先比较无 MTP 实测与仿真，再比较 MTP 相对无 MTP 的变化。仿真没有复现真实 vLLM-Ascend kernel、MC2、跨节点 HCCL/RoCE、图回放、实际显存和网关开销，因此差异应作为模型校准信号，不能直接解释为硬件故障或生产容量。
