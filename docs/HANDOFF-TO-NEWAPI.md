# Amor AI 2API → new-api 对接交接书

> 本文件自包含，不需要任何会话上下文即可执行。
> 面向：要在 new-api 里把 `D:\apk-analysis\amor\2api` 这个网关接成渠道的人。

---

## 0. 先读这三句

1. **本网关不是"白嫖上游"**：Amor AI 自己没有 AI 后端，它就是 OpenAI 客户端。网关的价值 = 把「102 个角色人格 + 固定参数（gpt-4o-mini / max_tokens=120 / temp=1.0）」封装成 OpenAI 兼容接口，让你**用自己的 key** 提供"AI 女友"角色化服务。
2. **不要用 APK 里 Remote Config 下发的 `sk-` 值**：那是第三方的 OpenAI 凭证，用它等于刷别人的账单。
3. 网关默认 `mock` 模式（零外部调用、零成本），`real` 模式才需要你自己的 `OPENAI_API_KEY`。

---

## 1. 任务契约

| 项 | 内容 |
|---|---|
| 目标 | new-api 里出现一个可选模型集合 `amor-1 … amor-102`，对话走角色人格 |
| 不可改动区 | `D:\apk-analysis\amor\2api\` 下的协议逻辑（`amor_client.py` 的 prompt 组装、请求参数） |
| 交付标准 | new-api 渠道测试通过 + 一次真实对话成功 + 流式与非流式均可用 |
| 分支 | 无需改 new-api 代码（路线 A）；若走路线 B 则单独会话实现并自行编译验证 |
| 授权边界 | 生产化前需你确认上游 key 与费用归属 |

---

## 2. 协议速查（5 条，复刻时照抄）

1. 端点：`POST https://api.openai.com/v1/chat/completions`（标准 OpenAI，无自定义头、无签名）
2. 模型：`gpt-4o-mini`，`max_tokens=120`，`temperature=1.0`，`frequency_penalty=0.5`，`presence_penalty=1.0`
3. **人格注入**：角色 prompt 作为 `messages[0]` 的 `role:"user"` 消息，**不是 system**
4. 历史裁剪：只带最近 6 条
5. 鉴权：`Authorization: Bearer <OpenAI key>`（App 里来自 Firebase Remote Config；网关里来自你的 `--api-key`）

完整证据链见 `docs/PROTOCOL.md`。

---

## 3. 启动网关

```bash
cd D:\apk-analysis\amor\2api

# A. 零成本演示 / 压测 / 联调（推荐先跑这个）
python server.py --port 8946

# B. 真实能力（你自己的 key）
python server.py --port 8946 --mode real --api-key sk-你自己的key

# C. 需要走代理访问 api.openai.com 时（注意：默认忽略环境代理）
python server.py --port 8946 --mode real --api-key sk-xxx --proxy http://127.0.0.1:10808

# D. 需要本地鉴权（强烈建议，特别是监听非 127.0.0.1 时）
python server.py --port 8946 --auth-token sk-amor-local
```

自检：

```bash
curl http://127.0.0.1:8946/healthz
curl http://127.0.0.1:8946/v1/models
curl -X POST http://127.0.0.1:8946/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{"model":"amor-1","messages":[{"role":"user","content":"hi"}]}'
```

带鉴权时所有 `/v1/*` 都要加 `-H "Authorization: Bearer sk-amor-local"`。

---

## 4. 路线 A（推荐，零 Go 代码）：OpenAI 渠道旁挂

在 new-api 后台 → 渠道 → 新建：

| 字段 | 值 | 说明 |
|---|---|---|
| 类型 | `OpenAI`（type=1） | 网关完全兼容 OpenAI 协议 |
| 名称 | `Amor AI (local)` | 任意 |
| 密钥 | `sk-amor-local`（与你 `--auth-token` 一致） | 网关只做等值比对，不是真实 OpenAI key |
| 代理/BaseURL | `http://127.0.0.1:8946` | **不要**加 `/v1`，new-api 会自己拼 |
| 模型 | 见下 | 逐行填入，或点"获取模型列表" |
| 模型重定向 | 不需要 | 网关已把 `amor-N` 映射到角色 |
| 分组 | 自定 | — |

模型列表（102 个，可直接粘贴）：

```
amor-1,amor-2,amor-3,…（连续到 amor-102）
```
也可以只开放少量，例如 `amor-1,amor-3,amor-7,amor-12`。别名也可用：`nicole`、`scarlett` 等（= 角色名 slug，大小写不敏感）。

**超时设置**：real 模式建议渠道超时 ≥ 60s（`max_tokens=120` 的单轮通常 1–5s，但上游抖动时会更久）。网关侧默认 60s。

**参数注意**：new-api 会把客户端传的 `temperature`/`max_tokens` 透传给网关，网关再覆盖到上游。如果你希望严格复刻 App 的短回复，在渠道里别改参，或在客户端不传。

---

## 5. 路线 B（可选，原生渠道）：ChannelType=80

只有当你需要「不走 OpenAI 兼容层、单独计费/单独路由」时才值得做。当前 new-api 已占用编号到 `79`（`ChannelTypeCodeAI`），下一个可用是 `80`。

落点清单（已在本仓库核实）：

| 文件 | 改动 |
|---|---|
| `constant/channel.go` | 新增 `ChannelTypeAmor = 80`；`ChannelTypeNames[80] = "Amor AI"` |
| `constant/api_type.go` | 新增 `APITypeAmor`（iota 追加） |
| `relay/relay_adaptor.go` | `GetAdaptor` switch 增 `case constant.ChannelTypeAmor: return amor.Adaptor{}` |
| `relay/channel/amor/adaptor.go` | 新建：`ConvertOpenAIRequest` / `DoRequest` / `DoResponse`，内部复用 `relay/channel/openai` |
| `relay/channel/amor/client.go` | 新建：`setupRequestURL` 指向渠道 BaseURL + `/v1/chat/completions` |
| `service/billing.go` | 若需独立计费，把 80 加入两段计费通道列表 |
| `web/src/features/channels/constants.ts` | 前端渠道类型下拉增加 80 |
| `web/src/features/pricing/components/twoapi-badge.tsx` | 可选：加徽标 |

> 注意：路线 B 的 Go 代码**本会话未实现、未编译**。若要做，建议单开一个 new-api 会话，按上表实现后用 `go build ./...` 与一次真实对话验收。这与路线 A 的能力等价，只是多一层原生封装。

---

## 6. 错误映射

| 网关返回 | 含义 | new-api 表现 |
|---|---|---|
| 200 | 正常 | 计费按 usage |
| 401 `invalid api key` | `--auth-token` 与渠道密钥不一致 | 渠道测试失败，改密钥 |
| 404 `model_not_found` | `amor-N` 超范围（>102） | 检查模型列表 |
| 502 `network: …` | 上游不可达（real 模式） | 检查 `--base-url` / `--proxy` → 服网络 |
| 502 `malformed upstream response` | 上游返回非 OpenAI JSON | 检查 base-url 是否指错服务 |
| 504 `upstream timeout after Ns` | 上游超时 | 调大 `--timeout` |
| 500 `upstream_error` 透传 | 上游自身报错（如 key 失效/欠费） | 网关不吞错，原样上抛 |

网关**不吞错误、不伪造成功**：上游失败一律 5xx，绝不返回空壳 200。

---

## 7. 计费锚点

- 网关按 `len(content)//4` 估算 usage（`prompt_tokens`/`completion_tokens`/`total_tokens`），仅用于 new-api 记账参考，不是上游真实 usage。
- real 模式下真实成本 = 你的 OpenAI 账单（`gpt-4o-mini` 极便宜，`max_tokens=120` 单轮约 100–200 tokens）。
- **不建议**用 `amor-N` 按次数计费：每轮实际 token 差异很大（人格 prompt 约 100 tokens）。

---

## 8. 已知坑

| 现象 | 原因 | 修法 |
|---|---|---|
| 渠道测试 404 | BaseURL 填了 `http://127.0.0.1:8946/v1` | 只填到端口 |
| 401 一直失败 | 设了 `--auth-token`，但渠道密钥写的是 `sk-xxx` 随便串 | 两边保持一致 |
| 流式客户端卡住 | 自己反向代理了网关（nginx）且开了 keep-alive 缓冲 | 网关已发 `Connection: close`；反向代理需 `proxy_buffering off` |
| real 模式全部 502 | 本机代理把上游 404 化成 `{"detail":"Not Found"}` | 网关默认忽略环境代理；要代理就显式 `--proxy` |
| 回复很短 | 上游 `max_tokens=120` 复刻结果 | 想更长就在网关侧改 `DEFAULT_PAYLOAD`，但这会偏离原 App 行为 |
| 角色口吻不对 | 把人格放进了 system | 必须放 `messages[0]` 且 `role=user` |

---

## 9. 验收清单

- [ ] `GET /healthz` 返回 `status: ok`
- [ ] `GET /v1/models` 返回 102 条，`id` 形如 `amor-1`
- [ ] 非流式 `POST /v1/chat/completions` 返回 200 且 `choices[0].message.content` 非空
- [ ] 流式返回 `data: {...}` 分块 + 最后 `data: [DONE]`
- [ ] new-api 渠道测试通过
- [ ] new-api 里发一条消息，能收到角色化回复（口吻与角色名一致）
- [ ] `python bench.py --concurrency 16 --requests 96` 失败率为 0
- [ ] `python verify_demo.py` 8/8 通过

---

## 10. 回滚

网关是独立进程，不侵入 new-api：

```bash
# 停网关
# 1) 找 PID
netstat -ano | findstr :8946
# 2) 结束
taskkill /PID <pid> /F
# 3) new-api 侧：禁用或删除「Amor AI (local)」渠道即可
```

没有任何数据库迁移、没有全局配置改动，回滚成本为零。

---

## 11. 红线

- 不把 Firebase Remote Config 里的 `sk-` 值填进 new-api 渠道。
- 不把网关监听到 `0.0.0.0` 且不设 `--auth-token`（等于开放代理）。
- 不对外宣称"免费无限量 AI"——mock 模式不产生真实 AI 内容。
