# Amor AI 2API → new-api 对接指南（自包含版）

> 生成时间：2026-10-30 · 对应仓库 `lza6/Amor-AI-APP-2api`，提交 `e827e8a`（含输入校验加固与自检脚本）
> 本文档不依赖任何会话上下文，可整份复制给另一个会话来执行。

---

## 0. 先说清楚一件最容易误会的事

**对接不需要 APK 里那两把 `sk-`。** 那两把是第三方（原 App 开发商）的 OpenAI 凭证，用它们等于刷别人的账单——本仓库和文档里都不会用到、也不应该用到。

对接只有两种上游来源，都跟那两把 key 无关：

| 你要的形态 | 网关怎么起 | 需要什么 key |
|---|---|---|
| 先接通链路 / demo / 压测 | `--mode mock`（默认） | **什么都不需要**，零外部请求 |
| 真实 AI 能力 | `--mode real` | **你自己的** OpenAI key（或任意 OpenAI 兼容中转站地址） |

也就是说：new-api 渠道里填的"密钥"只是本地网关的 `--auth-token`（一个你随便定的字符串），网关自己用 `--api-key` 持有上游凭证。两者是两回事。

---

## 1. 三步对接（路线 A，零 Go 代码）

### 第 1 步：起网关

```bash
cd D:\apk-analysis\amor\2api

# 先跑 mock 把渠道验证通过（零成本、不需要任何 key）
python server.py --port 8946 --auth-token sk-amor-local

# 之后要真实能力时换这行（key 换成你自己的）
# python server.py --port 8946 --auth-token sk-amor-local --mode real --api-key sk-你自己的key
```

自检：

```bash
curl http://127.0.0.1:8946/healthz
curl -H "Authorization: Bearer sk-amor-local" http://127.0.0.1:8946/v1/models
```

### 第 2 步：new-api 后台 → 渠道 → 新建

| 字段 | 值 | 说明 |
|---|---|---|
| 类型 | `OpenAI`（type=1） | 网关是标准 OpenAI 协议 |
| 名称 | `Amor AI (local)` | 任意 |
| 密钥 | `sk-amor-local` | **必须**与你启动网关时 `--auth-token` 的值完全一致；这是本地字符串，不是真 OpenAI key |
| BaseURL / 代理 | `http://127.0.0.1:8946` | **不要带 `/v1`**，new-api 会自己拼 |
| 模型 | 见下方清单 | 也可直接点「获取模型列表」 |
| 模型重定向 | 不需要 | 网关自己把 `amor-N` 映射到角色 |
| 超时 | ≥ 60s | real 模式上游抖动时会更久 |

模型清单（102 个，可直接粘贴）：

```
amor-1,amor-2,amor-3,amor-4,amor-5,amor-6,amor-7,amor-8,amor-9,amor-10,amor-11,amor-12,amor-13,amor-14,amor-15,amor-16,amor-17,amor-18,amor-19,amor-20,amor-21,amor-22,amor-23,amor-24,amor-25,amor-26,amor-27,amor-28,amor-29,amor-30,amor-31,amor-32,amor-33,amor-34,amor-35,amor-36,amor-37,amor-38,amor-39,amor-40,amor-41,amor-42,amor-43,amor-44,amor-45,amor-46,amor-47,amor-48,amor-49,amor-50,amor-51,amor-52,amor-53,amor-54,amor-55,amor-56,amor-57,amor-58,amor-59,amor-60,amor-61,amor-62,amor-63,amor-64,amor-65,amor-66,amor-67,amor-68,amor-69,amor-70,amor-71,amor-72,amor-73,amor-74,amor-75,amor-76,amor-77,amor-78,amor-79,amor-80,amor-81,amor-82,amor-83,amor-84,amor-85,amor-86,amor-87,amor-88,amor-89,amor-90,amor-91,amor-92,amor-93,amor-94,amor-95,amor-96,amor-97,amor-98,amor-99,amor-100,amor-101,amor-102
```

想只开放少量：`amor-1,amor-3,amor-7,amor-12`。也支持角色名别名：`nicole`、`scarlett`（= 角色名 slug，大小写不敏感）。

### 第 3 步：接前自检（必跑）

```bash
cd D:\apk-analysis\amor\2api
python tools/verify_newapi.py --base http://127.0.0.1:8946 --token sk-amor-local
```

期望 **9/9 passed**。它断言的是 new-api 真正会踩的点：

| 断言 | 为什么重要 |
|---|---|
| `GET /v1/models` 列 102 条 | 渠道「获取模型列表」按钮能用 |
| 非流式 chat 且 `usage.total_tokens > 0` | new-api 按 usage 计费，缺 usage 会被记 0 / 报错 |
| 流式 chunk + `[DONE]` + 有内容 | 渠道测试与流式对话 |
| 流式末帧带 `usage` | new-api 的 openai SDK 会带 `stream_options.include_usage`，网关必须容忍并回 usage |
| 裸模型名 `gpt-4o-mini` 可用 | 渠道里手填上游模型名时不会 404 |
| 未知模型 → 404 `model_not_found` | 模型名配错时的明确信号 |
| 错误信封 `{"error":{message,type,code}}` | new-api 靠这个形状判定渠道故障 |
| 缺 Bearer → 401 信封 | 密钥对不上时能明确报错 |

---

## 2. 路由与能力

| 端点 | 作用 |
|---|---|
| `POST /v1/chat/completions` | 主接口，支持流式/非流式；`model` 用 `amor-N` 或角色名 |
| `GET /v1/models` | 102 条，`id=amor-N`，`meta` 里带 name/gender/age/role/is_premium |
| `POST /v1/moderations` | mock 用本地规则；real 走上游 moderations |
| `GET /health` `/healthz` | 健康检查 |
| `GET /stats` · `POST /stats/reset` | 累计请求数 / 错误率 / p50·p95·p99 |
| `GET /` | demo 页（角色列表 / 流式打字机 / 前端并发压测 / token 输入） |

模型名语法：

- `amor-7` → 第 7 号角色
- `nicole` → 角色名 slug
- `gpt-4o-mini` / `default` / `amor` → 落到 1 号角色（保证渠道可用）
- `amor-7@gpt-4.1-mini` → **透传** `@` 后面的模型名给上游（想换模型时用这个，不是客户端能改的）

---

## 3. 错误映射（new-api 侧怎么解读）

| 网关返回 | 含义 | new-api 该怎么做 |
|---|---|---|
| 200 | 正常 | 按 usage 计费 |
| 400 `invalid_request_error` | 请求结构/参数类型不合法（body 非对象、`messages` 非数组、`max_tokens` 非正整数…） | 调用方 bug，**不重试**、不计费 |
| 401 `authentication_error` | 渠道密钥与 `--auth-token` 不一致 | 渠道测试失败，改密钥 |
| 404 `model_not_found` | `amor-N` 超范围或模型名不认识 | 检查模型列表 |
| 413 `invalid_request_error` | 请求体 > 8 MB | 调用方 bug，不重试 |
| 502 `upstream_error` `network: …` | 上游不可达（real 模式） | 检查 `--base-url` / `--proxy` / 服务器出网 |
| 502 `malformed upstream response` | 上游返回非 OpenAI JSON | BaseURL 是否指错服务 |
| 504 `upstream timeout` | 上游超时 | 调大 `--timeout` 与渠道超时 |
| 500 `upstream_error`（透传） | 上游自身报错（key 失效 / 欠费 / 限流） | 网关不吞错，按上游原因处理 |

网关**不吞错、不伪造成功**；客户端输入错误一律 4xx 且不断连（33 条边界用例见 `tools/test_edges.py`）。

---

## 4. 排障速查

| 现象 | 原因 | 修法 |
|---|---|---|
| 渠道测试 404 | BaseURL 填了 `http://127.0.0.1:8946/v1` | 只填到端口 |
| 401 一直失败 | 设了 `--auth-token`，但渠道密钥随手写了别的 | 两边保持完全一致 |
| 流式客户端卡住 | 前面挂了 nginx 且开了缓冲 | 网关已发 `Connection: close`；反代需 `proxy_buffering off` |
| real 模式全部 502 | 本机代理把上游 404 化成 `{"detail":"Not Found"}` | 网关默认忽略环境代理，要代理就显式 `--proxy` |
| 回复很短（1–2 句） | 复刻 App 的 `max_tokens=120` | 想更长：客户端传 `max_tokens`，或用 `amor-N@<model>` 换上游模型，或改 `DEFAULT_PAYLOAD`（会偏离原 App 行为） |
| 角色口吻不对 | 把人格塞进了 `system` | 必须放 `messages[0]` 且 `role=user` |
| 计费为 0 | 上游没返回 usage | 网关自己按 `len//4` 估算，正常应有值；若为 0 请查 `/stats` |

---

## 5. 验收清单

```bash
python tools/verify_newapi.py --token sk-amor-local   # 9/9
python tools/test_edges.py                             # 25/25（错误路径不回 500、不断连）
python bench.py --concurrency 16 --requests 96         # 失败 0
python verify_demo.py                                  # 8/8（Playwright，截图 static/demo.png）
```

人工确认：

- [ ] new-api 渠道「测试」通过
- [ ] new-api 里发一条消息，收到角色化回复且口吻与角色名一致
- [ ] 流式开关打开时前端逐字出字
- [ ] 消耗记录里能看到 token 数（不是 0）

---

## 6. 回滚

网关是独立进程，不侵入 new-api：

```bash
netstat -ano | findstr :8946     # 找 PID
taskkill /PID <pid> /F           # 结束
# new-api 侧：禁用或删除「Amor AI (local)」渠道
```

无数据库迁移、无全局配置改动，回滚成本为零。

---

## 7. 红线

1. **不把 APK 里 Remote Config 的 `sk-` 填进任何渠道**——那是第三方的凭证，使用即盗刷。
2. **不要监听 `0.0.0.0` 还不设 `--auth-token`**——那等于开了一个公开的 OpenAI 代理。
3. **不要对外宣称"免费无限量 AI"**——mock 模式产出的是本地规则回复，不是模型输出。
4. 生产部署建议前置 nginx 或改 ASGI：当前 `ThreadingHTTPServer` 单机约 100 rps。

---

## 8. 可选的路线 B（原生 Go 渠道，非必须）

只有当你需要「不走 OpenAI 兼容层、单独计费/单独路由」时才值得做。当前 new-api 已占用到 `79`，下一个可用 `80`。

落点清单见仓库 `docs/HANDOFF-TO-NEWAPI.md` 第 5 节（`constant/channel.go`、`relay/channel/amor/*`、`service/billing.go`、前端渠道下拉）。**该部分代码未实现、未编译**，与路线 A 能力等价，只是多一层封装。建议单开一个 new-api 会话实现。

---

## 9. 附：协议事实速查（复刻依据）

| 项 | 值 | 证据 |
|---|---|---|
| 上游端点 | `POST https://api.openai.com/v1/chat/completions` | `RetrofitInstance` BASE_URL + `ApiService.@o("chat/completions")` |
| 模型 | `gpt-4o-mini`（硬编码） | `AiRequestBodyDto` 合成构造器 |
| 参数 | temp=1.0 / max_tokens=120 / freq=0.5 / presence=1.0 | 同上 |
| 人格注入 | `messages[0]`，`role:"user"` | `n6/e.java` 的 `PROMPT_CHAT_0109` |
| 历史裁剪 | 最近 6 条 | `i7/x0.java:236` `hj.z.Z(6,…)` |
| 鉴权 | `Authorization: Bearer <key>` | App 里来自 Firebase RC，网关里来自 `--api-key` |
| 无设备证明 | `IS_USE_APP_CHECK=false` | `n6/e.java` |
| 免费额度 | 15 条/天/角色，**纯客户端计数** | `GIRL_FREE_MESSAGE_PER_DAY=15`（服务端不校验，与网关无关） |
| 角色库 | 102 个（2 categories） | `CHARACTERS_JSON` |
