# Amor AI 1.8.8 → OpenAI 兼容 2API

把 `com.amorai.chat`（Amor AI 1.8.8）的角色人格与请求参数复刻成一个标准 OpenAI 兼容网关，
支持 **mock（零成本）** 与 **real（BYOK，自己的 key）** 双模，附带 demo 页、压测脚本与浏览器端到端验证。

> 逆向报告：[`docs/PROTOCOL.md`](docs/PROTOCOL.md) ·
> new-api 对接：[`docs/HANDOFF-TO-NEWAPI.md`](docs/HANDOFF-TO-NEWAPI.md)

---

## 30 秒跑起来

```bash
cd D:\apk-analysis\amor\2api

# 0) 拉完整角色库（仓库只带 3 个示例；本步生成 data/characters.full.json，共 102 个）
python tools/fetch_characters.py --proxy http://127.0.0.1:10808   # 不走代理可省略 --proxy

# mock 模式：不发任何外部请求，零成本
python server.py --port 8946
# → http://127.0.0.1:8946/          demo 页
# → http://127.0.0.1:8946/v1/models 102 个角色模型
```

真实能力（你自己的 OpenAI key）：

```bash
python server.py --port 8946 --mode real --api-key sk-你的key
# 需要代理时：--proxy http://127.0.0.1:10808
# 需要本地鉴权：--auth-token sk-amor-local（此后 /v1/* 需 Bearer）
```

零依赖：只用 Python 标准库（`urllib` + `http.server`）。Python ≥ 3.10。

---

## 它是怎么工作的（30 秒版)

Amor AI 没有自有 AI 后端。它把「角色 prompt + 聊天历史」直接发到
`https://api.openai.com/v1/chat/completions`，模型固定 `gpt-4o-mini`，
`max_tokens=120`，key 由 Firebase Remote Config 下发。

本网关复刻的就是这一层：

```
客户端 → 本网关 (OpenAI 兼容) → [mock: 本地生成 | real: 你的 OpenAI 端点]
                  ↑
        102 个角色的人格 prompt（来自 App 的 CHARACTERS_JSON）
```

关键复刻点（偏离就会"变味"）：

| 细节 | 值 |
|---|---|
| 人格位置 | `messages[0]`，`role` 必须是 **user**（不是 system） |
| 历史长度 | 只带最近 **6** 条 |
| 模型 / 参数 | `gpt-4o-mini` / `temp=1.0` / `max_tokens=120` / `fp=0.5` / `pp=1.0` |
| 语言 | 人格模板里的 `%s` 填语言，默认 `English` |

---

## 接口

| Method | Path | 说明 |
|---|---|---|
| GET | `/healthz` | 健康检查（含 mode / 角色数） |
| GET | `/v1/models` | 102 个角色（`amor-<id>` + 角色名别名） |
| POST | `/v1/chat/completions` | 支持 `stream:true/false` |
| POST | `/v1/moderations` | 复刻 App 的审核调用 |
| GET | `/stats` | 累计请求数 / 错误率 / p50·p95·p99 |
| POST | `/stats/reset` | 清空统计 |
| GET | `/` | demo 页 |

模型命名：

- `amor-1` … `amor-102`（主键）
- `nicole` / `scarlett` …（角色名 slug，大小写不敏感）
- `amor-1@<upstream-model>`：把上游模型名替换成指定值（默认 `gpt-4o-mini`）

---

## 能力实测表

| 能力 | 状态 | 证据 |
|---|---|---|
| 角色对话（102 个角色） | ✅ | `/v1/models` 102 条；浏览器 e2e 选角色→回复 |
| 流式（SSE） | ✅ | `data:` 分块 + `[DONE]`，压测 64/64 通过 |
| 非流式 | ✅ | OpenAI 标准 `chat.completion` + usage |
| 人格复刻（prompt 注入） | ✅ | real 模式对假上游抓包：`messages[0].role=user` 且内容=角色 prompt |
| 参数复刻 | ✅ | 抓包核对：`gpt-4o-mini / 120 / 1.0 / 0.5 / 1.0` |
| 审核端点 | ✅ | mock 关键词表 + real 转发 `/v1/moderations` |
| 角色库 | ✅ | 102 个（GIRLS 100 / BOYS 2），含 `is_premium` |
| **用 App 内的 key 白嫖** | ❌ 不支持 | 第三方付费凭证，不内置、不加载 |
| 文生图 / 语音 / 视频 | ❌ 不存在 | App 的图片是静态 CDN 素材，无生成能力 |

---

## 压测数据

环境：Windows 10 + Python 3.14 + 本机回环，**mock 模式**（上游延迟模拟 120–200ms）。
命令：`python bench.py --concurrency N --requests M`。
> mock 数字只反映**网关自身开销**，不含任何真实模型推理耗时；接 real 后延迟以你的上游为准。

| 场景 | 并发×请求 | 成功 | RPS | p50 | p95 | p99 |
|---|---|---|---|---|---|---|
| 非流式 | 8 × 64 | 64/64 | 40.7 | 162 ms | 300 ms | 398 ms |
| 非流式 | 32 × 160 | 160/160 | 93.7 | 185 ms | 800 ms | 852 ms |
| 非流式（16 并发，第 1 轮） | 16 × 96 | 96/96 | 65.7 | 168 ms | 649 ms | 706 ms |
| 非流式（16 并发，第 2 轮） | 16 × 96 | 96/96 | 90.8 | 158 ms | 198 ms | 203 ms |
| 流式 | 8 × 48 | 48/48 | 36.7 | 173 ms | 412 ms | — |
| 流式 | 16 × 64 | 64/64 | 58.9 | 166 ms | 678 ms | 690 ms |

**错误率全程 0%。** 高并发下的 p95 抬升来自 Python 线程 + GIL 排队，不是协议问题。

复测（修完输入校验层后重跑，同一台机器）：8×64 → 38.7 RPS / p50 158 ms；32×160 → 113.0 RPS / p50 169 ms；
流式 16×64 → 57.8 RPS / p50 170 ms，**失败 0**。校验层对吞吐无可测量影响。

上游异常路径（对本地假上游实测，`tools/test_edges.py --upstream`）：

| 场景 | 网关返回 |
|---|---|
| 上游 500 | 透传 500 `upstream_error` |
| 上游返回非 JSON | 502 `malformed upstream response` |
| 上游超时（`--timeout 1` + 3s 慢响应） | 504 `upstream timeout after 1.0s` |
| 上游端口不通 | 502 `network: [WinError 10061]` |

---

## 客户端错误契约（输入校验）

所有客户端错误都返回 OpenAI 风格信封 `{"error":{message,type,code}}`，**不会出现 500，也不会断连**：

| 输入 | 状态码 | type |
|---|---|---|
| body 不是 JSON 对象（数组 / 字符串 / 坏 JSON） | 400 | `invalid_request_error` |
| `messages` 不是数组 / 元素不是对象 / `content` 非字符串 | 400 | `invalid_request_error` |
| `model` 非字符串 | 400 | `invalid_request_error` |
| `max_tokens` 非正整数、`temperature` 等非数字 | 400 | `invalid_request_error` |
| `model` 不认识 | 404 | `model_not_found` |
| 未知路由 | 404 | `not_found` |
| 启用 `--auth-token` 而 Bearer 缺失/错误 | 401 | `authentication_error` |
| 请求体 > 8 MB | 413 | `invalid_request_error` |

回归脚本（mock 25 条 + real/上游 8 条 = 33 条，全部基于真实 HTTP 响应）：

```bash
python tools/test_edges.py                                        # 25/25
python tools/test_edges.py --base http://127.0.0.1:8947 --upstream http://127.0.0.1:8950   # 33/33
python tools/test_edges.py --base http://127.0.0.1:8948 --token s3cret                     # 28/28
```

---

## 浏览器端到端验证

```bash
python verify_demo.py                       # 无鉴权网关
python verify_demo.py --token sk-amor-local # 启用了 --auth-token
```

覆盖：页面加载 → 角色列表 102 条 → 选角色 → 流式发消息 → 非流式发消息 → 前端 32 并发压测。
实测 **8/8 PASS**，截图 `static/demo.png`。

---

## 文件清单

| 文件 | 作用 |
|---|---|
| `server.py` | OpenAI 兼容网关（含可选 Bearer 鉴权、统计、SSE 兼容层） |
| `amor_client.py` | 协议层：人格组装、消息裁剪、上游调用、mock 引擎 |
| `demo.html` | 单页 demo（角色列表 / 流式打字机 / 前端并发压测 / token 输入） |
| `bench.py` | 压测脚本（sync / stream，多轮，输出 p50/p95/p99） |
| `verify_demo.py` | Playwright 真实浏览器端到端验证 |
| `tools/test_edges.py` | 边界/错误路径回归（输入校验、413、401、上游 500/畸形/慢响应） |
| `tools/verify_newapi.py` | new-api 渠道接前自检（models/流式/usage 计费锚点/错误信封） |
| `tools/fake_openai.py` | 本地假上游，用于验证 real 模式（零外部请求） |
| `tools/fetch_characters.py` | 从 Firebase Remote Config 拉完整角色库（102 个）与非凭证参数快照 |
| `data/characters.json` | **示例角色 3 个**（仓库版；完整库由上面的脚本生成到 `characters.full.json`，已被 gitignore） |
| `data/remote_config.json` | 非敏感 RC 参数快照（已剔除所有 key） |
| `docs/PROTOCOL.md` | 逆向报告：证据链、端点矩阵、踩坑表 |
| `docs/HANDOFF-TO-NEWAPI.md` | new-api 对接契约（自包含） |
| `static/demo.png` | demo 页实测截图 |

---

## 风险与限制

| 项 | 说明 |
|---|---|
| 凭证 | 仓库内**不含任何** OpenAI key。APK 的 Remote Config 里有 4 个第三方 `sk-`，属他人付费凭证，是否仍有效 `待验证`，**不可使用**。 |
| 不是"免费 AI" | 本网关的 AI 能力 100% 来自你自己配置的上游。mock 模式产生的是本地规则回复，不是模型输出。 |
| real 模式未对真实 OpenAI 发起调用 | 按付费 API 红线，本次只用本地假上游验证链路。首接真实 key 时请先用 1 个请求验证。 |
| App 行为差异 | App 有客户端侧 15 条/天/角色的免费限制与 Apphud 付费墙；本网关不含这些，属"能力剥离"后的版本。 |
| 上游模型 | 固定 `gpt-4o-mini`（复刻 App）。想换模型用 `amor-1@<model>` 显式指定。 |
| 线程模型 | `ThreadingHTTPServer`，单机压测上限约 100 RPS；生产请前置 nginx/gunicorn 或换 ASGI。 |
| 法律边界 | 仅做协议研究与互操作封装；不提供绕过他人付费、盗用他人凭证的路径。 |

---

## 一键自检

```bash
python -c "import ast,sys;[ast.parse(open(f,encoding='utf-8').read()) for f in ['server.py','amor_client.py','bench.py','verify_demo.py','tools/test_edges.py']];print('syntax ok')"
python server.py --port 8946 &
python tools/test_edges.py            # 25/25
python tools/verify_newapi.py         # 8/8（接 new-api 前必跑）
python bench.py --concurrency 16 --requests 96
python verify_demo.py
curl http://127.0.0.1:8946/stats
```
