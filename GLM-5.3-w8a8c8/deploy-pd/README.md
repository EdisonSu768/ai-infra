# GLM-5.3 on Atlas A2/A3 · P/D 分离 · llmisvc 部署

把 `reference/` 里那几篇文档（vllm-ascend 的 GLM-5.3 / GLM-5.2 教程、KV Pool 指南、
zai-org 的 ascend.md）整理成可以直接 `kubectl apply` 的 KServe
`LLMInferenceService`（llmisvc）清单。两个平台都与上游参考的 PD 拓扑 1:1 对齐。

```
.
├── base/
│   ├── weights-pvc.yaml                            # 权重 PVC（RWX，需预先灌好）
│   └── llmisvcconfig-prefill-pipeline-parallel.yaml# 补 KServe 缺的 PP preset（A3 必需）
├── A2/llmisvc-glm53-pd.yaml     # 8 台 Atlas 800I/T A2：4P + 4D，RoCE
├── A3/llmisvc-glm53-pd.yaml     # 4 台 Atlas 800I/T A3：2P + 2D，HCCS/超节点
└── reference/                   # 原始资料链接
```

---

## 1. 拓扑

| | A2（8 卡 × 64GB / 节点） | A3（16 卡 × 64GB / 节点） |
|---|---|---|
| prefill | 4 节点，**DP4 × TP8**，dataLocal=1 | 2 节点，**PP2 × TP16**，层切分 `41,37` |
| decode | 4 节点，**DP8 × TP4**，dataLocal=2 | 2 节点，**DP16 × TP2**，dataLocal=8 |
| 合计 | 8 节点 / 64 卡 | 4 节点 / 64 卡 |
| 组网 | RoCE（`HCCL_INTRA_ROCE_ENABLE=1`） | **HCCS / 超节点**（`ASCEND_ENABLE_USE_FABRIC_MEM=1`） |
| 镜像 | `quay.io/ascend/vllm-ascend:v0.23.0` | `quay.io/ascend/vllm-ascend:v0.23.0-a3` |
| KV 连接器 | MooncakeConnectorV1 | MooncakeConnectorV1 + `use_ascend_direct` |
| kv_port | P 30000 / D 30100 | P 30000 / D 30200（engine_id 0 / 2） |
| max-model-len | 200000 | 202752 |

### 文档里的手工脚本 → llmisvc 字段

llmisvc 的多机 preset 和 vllm-ascend 文档里的多机脚本是同一套语义，
所以这些参数**不要**再写进 `VLLM_ADDITIONAL_ARGS`，由 `spec.parallelism` 生成：

| vllm-ascend 脚本 | llmisvc |
|---|---|
| `--tensor-parallel-size` | `parallelism.tensor` |
| `--data-parallel-size` / `-size-local` | `parallelism.data` / `parallelism.dataLocal` |
| `--pipeline-parallel-size` / `--nnodes` | `parallelism.pipeline` |
| `--enable-expert-parallel` | `parallelism.expert: true` |
| `--data-parallel-address` / `--master-addr` | preset 从 `LWS_LEADER_ADDRESS` 解析成 IP |
| `--data-parallel-start-rank` | preset 按 `LWS_WORKER_INDEX × dataLocal` 算 |
| `--node-rank` | preset 直接用 `LWS_WORKER_INDEX`（leader=0） |
| `--headless`（非 0 号节点） | preset 在 worker 模板里加 |
| 节点数 | DP：`data / dataLocal`；PP：`pipeline` —— 即 LeaderWorkerSet 的 size |
| `--served-model-name` / `--port` | preset 从 `spec.model.name` 生成 |
| `load_balance_proxy_server_example.py` | Gateway + InferencePool/EPP + 每个 decode leader 上的 `llm-d-routing-sidecar` |

其余所有 flag（`--quantization ascend`、`--additional-config`、`--speculative-config`、
`--kv-transfer-config` …）走 `VLLM_ADDITIONAL_ARGS` 环境变量——
preset 的启动脚本会把它原样 `eval` 进 `vllm serve`。

> DP preset 只有在 `VLLM_ADDITIONAL_ARGS` 里**没有** `--kv-transfer-config` 时才会注入
> 默认的 `NixlConnector`；Ascend 镜像里没有 nixl，那条路会走到
> `[kv-transfer] NIXL not available, P/D KV transfer stays disabled` ——
> 服务照起、请求照通，但 decode 会把 prefill 重算一遍，只留一行日志。
> 清单里都显式写了 Mooncake；PP preset 更进一步，检测不到就直接退出。

### A3 的 PP2 是怎么落地的

KServe 的 API **支持** pipeline parallel：`parallelism.pipeline` 字段在，
webhook 里有「pipeline 和 data 不能同时设」的校验，控制器里也有选择分支。
缺的只是那个 `LLMInferenceServiceConfig` 对象本身
（`config_merge.go` 里还挂着 `// FIXME move those presets to well-known when they're finally known :)`）。

`base/llmisvcconfig-prefill-pipeline-parallel.yaml` 就是补上的那个实现：
脚手架（volumes / probes / ports / 版本探测）照 v0.20.0 的
`kserve-config-llm-prefill-worker-data-parallel` 抄，只把 DP 的几个 flag 换成
`--pipeline-parallel-size / --nnodes / --node-rank / --master-addr /
--distributed-executor-backend mp`。

名字解析是确定的：它不在 `WellKnownDefaultConfigs` 集合里，
所以 `WellKnownConfigResolver` 不会给它打版本化别名，原样返回
`kserve-config-llm-prefill-worker-pipeline-parallel`；
`getConfig` 先查 LLMISVC 所在 namespace 再查 `kserve`，放在业务 namespace 就会命中。

**换 namespace 部署要跟着复制一份**，或者改成 `namespace: kserve` 让全集群共用。
等 KServe 自己发了这个 preset，删掉本文件即可。

---

## 2. 前置条件

集群侧：

- [ ] KServe ≥ **v0.20.0**，并且装了 llmisvc controller 与 `kserve-runtime-configs`
      （依赖 `kserve-config-llm-{prefill,decode}-worker-data-parallel` 两个 preset）
- [ ] LeaderWorkerSet（LWS）controller
- [ ] Gateway API + Gateway Inference Extension（InferencePool / EPP）
- [ ] Ascend device plugin，并确认 `kubectl describe node` 里的资源名
      ——清单里写的是 `huawei.com/Ascend910`，用 HAMi 的插件可能是
      `huawei.com/Ascend910B`，不一致就改掉 `resources` 里的两处

A3 / HCCS 超节点额外要求（vllm-ascend kv_pool.md §5.1）：

- [ ] HDK ≥ **26.0**（或 HDK ≥ 25.5 且 mooncake ≥ v0.3.11）、CANN ≥ **9.0.0**、
      灵衢计算网络 ≥ **1.5** —— `ASCEND_ENABLE_USE_FABRIC_MEM=1` 的统一内存地址直通方案依赖这些
- [ ] **同一个 LWS group 的 leader + worker 必须落在同一个 superPoD 里**，
      否则 HCCS fabric 走不通。A3 清单顶部留了
      `leaderworkerset.sigs.k8s.io/exclusive-topology` 注解的位置，
      填上集群里 superPoD 的节点标签键（带这个前缀的注解会被 KServe 透传到 LWS 对象）：
      ```bash
      kubectl get nodes --show-labels | tr ',' '\n' | grep -i -E 'super|pod-id|topology'
      ```
- [ ] A3 走的是 fabric mem，**不要**再设 `HCCL_INTRA_ROCE_ENABLE=1`（那是 A2 / RoCE 直通的子模式）

A2 / RoCE 额外要求：

- [ ] `hccn_tool` 已把 NPU 侧 RoCE 网卡配好、跨节点互通
- [ ] 宿主机大页：`echo 200000 > /proc/sys/vm/nr_hugepages`；
      如果宿主机开了大页，Pod 还要额外 `requests: hugepages-2Mi`

两个平台都要：

- [ ] 装了 `ascend-docker-runtime` 的话，可以把清单里那 7 个 `ascend-*`
      hostPath volume/volumeMount 整段删掉，由 runtime 自动注入
- [ ] 权重从 https://modelscope.cn/models/Eco-Tech/GLM-5.3-w8a8c8 下载，
      放到 RWX PVC 的 `GLM-5.3-w8a8c8/` 子目录；`pvc://` 是**直接只读挂载**到
      `/mnt/models`，不走 storage-initializer，所以 apply 之前权重必须已经就位

---

## 3. 部署

```bash
kubectl create namespace glm

# 1) 权重 PVC（改 storageClassName，然后把权重灌进去）
kubectl apply -f base/weights-pvc.yaml

# 2) A2
kubectl apply -f A2/llmisvc-glm53-pd.yaml

# 2') A3 —— 必须先 apply PP preset，再 apply 服务
kubectl apply -f base/llmisvcconfig-prefill-pipeline-parallel.yaml
kubectl apply -f A3/llmisvc-glm53-pd.yaml

# 3) 看状态
kubectl -n glm get llmisvc glm53-pd -o wide
kubectl -n glm get lws                        # prefill / decode 各一个 LeaderWorkerSet
kubectl -n glm get pods -w
```

如果 PP preset 没先 apply，llmisvc 的 Ready 条件会报
`LLMInferenceServiceConfig "kserve-config-llm-prefill-worker-pipeline-parallel" not found
in namespaces ["glm" "kserve"]`。

首次拉起会很慢：镜像几十 GB，权重从共享存储读进 64 张卡。清单里已经把 leader 的
`startupProbe` 放宽到 `180 × 20s = 60min`，还不够就继续加 `failureThreshold`。

验证：

```bash
URL=$(kubectl -n glm get llmisvc glm53-pd -o jsonpath='{.status.url}')
curl -s $URL/v1/chat/completions -H 'Content-Type: application/json' -d '{
  "model": "glm-5",
  "messages": [{"role": "user", "content": "Who are you?"}],
  "temperature": 0
}'
```

---

## 4. 已知风险

按重要性排序，上生产前逐条确认：

1. **上游没有在 GLM-5.3 上验证过 PD 分离。**
   vllm-ascend 的 GLM-5.3 文档 §5.2 原话是 *"Prefill-Decode disaggregation scenarios
   have not yet tested for GLM-5.3"*，让参考 GLM-5.2 的脚本。这里的所有 PD 参数
   （拓扑、kv-transfer、每个角色的 flag）都来自 GLM-5.2 的 PD 实测配置，
   只把权重路径换成了 GLM-5.3-w8a8c8。两者是同一个 base model
   （GLM-5.3 文档原话："uses the same base model as GLM-5.2 — every gain comes from post-training"）。

2. **A3 的 PP preset 是自己补的，没有上游实现可比对。**
   命令本身是照参考脚本逐行翻的，`--node-rank` 取自 LWS 的
   `LWS_WORKER_INDEX`（leader 恒 0、worker 1..size-1），`--master-addr` 取 leader Pod IP。
   但 vLLM 跨节点 `mp` executor 跑 PP 本来就是比较脆的一条路，且
   `--nnodes / --node-rank / --master-addr` 这组 flag 是 **v0.23.0** 的接口
   （GLM-5.3 文档自己提醒 "some params are not supported in main code"）——
   **换镜像版本前先确认这几个 flag 还在。**
   另外 preset 里 `--nnodes` 直接等于 `parallelism.pipeline`，
   这个等式只在 **TP == 单节点卡数** 时成立（A3：TP16 = 16 卡）。

3. **routing sidecar 写死 `--kv-connector=nixlv2`，而我们用的是 Mooncake。**
   看过两边代码：nixlv2 在 HTTP 层是连接器无关的——先带
   `kv_transfer_params:{do_remote_decode:true}`、`stream:false`、`max_tokens:1`
   打到 prefiller，把返回的 `kv_transfer_params` **原样**转给 decoder，
   全程不解析内容；和 vllm-ascend 自己的 `load_balance_proxy_server_example.py`
   是同一套握手。所以理论上能配合 `MooncakeConnectorV1` 工作，
   **但这是整套方案里最该先验的一环**。
   已知缺口：ascend 那个 proxy 在 decode 失败时会回收 prefiller 侧的 KV
   （日志里的 "releasing prefiller KV"），sidecar 没有这条清理路径 ——
   长时间压测要盯 prefill 侧的 KV 是否泄漏。
   验证方法：打一条请求，对照三处日志 ——
   prefill 收到 `max_tokens=1` 的请求、decode 出现 Mooncake `start_load_kv` +
   remote block ids、sidecar（`-v=5`）打出 `sending request to prefiller` → `to decoder`。
   如果 decode 侧是完整 prefill 而不是 KV load，说明握手没接上。

4. **A2 的 `--max-model-len 200000` 取自 GLM-5.2 的 PD 配置**，
   而 GLM-5.3 的 A2 共置脚本只到 `135000`。选前者是因为形状对得上
   （A2 + PD + 每角色 32 卡），而共置那个形状不同。如果 KV cache 分配失败
   （启动时报 max seq len 大于 KV cache 容量），降到 135000。

5. **去掉了 KV Pool（AscendStoreConnector）。**
   GLM-5.2 的 A2 PD 参考用的是 `MultiConnector` = `MooncakeConnectorV1` +
   `AscendStoreConnector`，那需要另外起 `mooncake_master`、准备 `mooncake.json`、
   配 `MOONCAKE_CONFIG_PATH`。这里只保留 P2P 的 `MooncakeConnectorV1`
   （和 A3 参考一致），先跑通再叠加。

6. **`LD_LIBRARY_PATH` 没有覆盖。** 参考脚本里会往里塞 `/usr/local/lib`、
   mooncake wheel 目录；在 K8s 里 `env` 是整体替换而不是追加，覆盖会打断
   镜像里的 CANN 路径。如果 Mooncake 报找不到 .so，再按镜像里的实际值补全一整串。

7. **`resources` 里的 cpu/memory 是估值**，按实际机型调；NPU 数量是按整机独占给的
   （A2 = 8，A3 = 16），不要改小 —— 它必须等于单节点实际参与的卡数
   （A2 decode：`tensor 4 × dataLocal 2 = 8`；A3 prefill：`tensor 16 = 16`）。

---

## 5. 调参入口

| 想改什么 | 改哪里 |
|---|---|
| 并行度 / 节点数 | `spec.parallelism`（decode）和 `spec.prefill.parallelism`（prefill） |
| PP 层切分 | prefill 的 `VLLM_PP_LAYER_PARTITION` —— **必须和 kv 配置里的 `pp_layer_partition` 一致** |
| PP rendezvous 端口 | prefill 的 `VLLM_PP_MASTER_PORT`（默认 7060） |
| 上下文长度、并发、显存占比、MTP、算子开关 | 对应角色的 `VLLM_ADDITIONAL_ARGS` |
| KV 连接器 | `VLLM_ADDITIONAL_ARGS` 里的 `--kv-transfer-config`；**改了拓扑记得同步改里面 `prefill`/`decode` 的 `dp_size`/`pp_size`/`tp_size`** |
| 组网 / 超时 | 对应角色的 `env`（A3 是 fabric mem，A2 是 RoCE） |
| 超节点亲和 | `metadata.annotations` 里的 `leaderworkerset.sigs.k8s.io/exclusive-topology` |
| 镜像 | `image` 锚点（`&ascendImage`），一处改四处生效 |
| 扩容 | `spec.replicas` / `spec.prefill.replicas`（整组 P 或整组 D 复制一份） |

> 清单用 YAML 锚点让 leader / worker 共用同一份配置。
> 唯一的区别是 leader 有 `startupProbe` —— worker 是 `--headless` 的，
> 不起 API server，加 HTTP 探针会把它探死。
