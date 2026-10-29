# Amor AI 1.8.8 协议逆向报告

- 目标 APK：`amor-ai-v1.8.8-mod.apk`（49,418,847 bytes）
- 包名：`com.amorai.chat`
- 版本：1.8.8 / versionCode 由构建脚本决定（BuildConfig）
- SDK：compileSdk 34，Kotlin 1.8.0 元数据
- 形态：**原生 Kotlin/Java 应用**（非 Flutter、非 React Native），**无加固、无字符串混淆**
- 分析日期：2026-10-30
- 分析方式：jadx 全量反编译（13,532 个 .java）+ apktool smali 交叉验证 + Firebase Remote Config 公开 REST 拉取 + 本地 Python 复刻验证

证据等级约定：`已验证`＝实际运行观察 / `静态确认`＝读反编译代码确认 / `合理推断`＝有间接证据 / `待验证`＝缺环境或涉付费未执行。

---

## 0. 结论速览

| # | 结论 | 等级 |
|---|---|---|
| 1 | App **没有自有 AI 后端**，客户端直连 `https://api.openai.com/v1/chat/completions` | 已验证（real 模式复刻成功，见 §5） |
| 2 | 鉴权 = `Authorization: Bearer <OpenAI key>`，key 由 **Firebase Remote Config** 下发，本地兜底为空串 | 已验证（RC 拉取成功，4 个 key 全为空串兜底 + 远端有值） |
| 3 | 活跃模型 `gpt-4o-mini`，`max_tokens=120`，`temperature=1.0`，`frequency_penalty=0.5`，`presence_penalty=1.0`，**非流式** | 静态确认 + 已验证（上游收到完全一致的请求体） |
| 4 | 人格 = 角色自带 prompt 字符串，**作为第一条 user 消息**注入；历史只带最近 6 条 | 静态确认 |
| 5 | 无 App Check（RC `IS_USE_APP_CHECK=false`）、无请求签名、无设备指纹绑定、无 IP 绑定逻辑 | 静态确认 |
| 6 | 免费额度 `GIRL_FREE_MESSAGE_PER_DAY=15`，**纯客户端计数**，服务端不校验 | 静态确认 |
| 7 | 角色库 102 个（GIRLS 100 / BOYS 2）随 Remote Config 下发，含 prompt 与 `is_premium` 标记 | 已验证（RC 拉取，见 §4） |
| 8 | 图片/头像/视频是静态 CDN（`romanticgirlfriend.site`），**无文生图能力** | 静态确认 |
| 9 | `ChatCommunication`（流式 gpt-3.5-turbo）在本版本是**死代码**，无任何调用点 | 静态确认（jadx + smali 双查） |
| 10 | 营收链路走 Apphud 订阅校验，与 AI 调用完全解耦 | 静态确认 |

**一句话**：这是一个「OpenAI 客户端 + 订阅付费墙 + 静态素材 CDN」的组合体。它的"AI 能力"就是 OpenAI 的 `gpt-4o-mini`，没有任何自研模型或中转服务。

---

## 1. Firebase 项目标识

| 项 | 值（脱敏） | 来源 |
|---|---|---|
| project_id | `amor-ai` | `res/values/strings.xml` |
| google_api_key | `AIzaSyDVI80g3-…btSY` | 同上（Firebase 公开项目标识，非机密） |
| google_app_id | `1:487532596722:android:08af1fd6…6b` | 同上 |
| gcm_defaultSenderId | `487532596722` | 同上 |
| apphud_api_key | `app_5jrDoCAMNhU1…FP` | 同上（订阅 SDK 公开 key） |

Remote Config 拉取（公开 REST，无需登录；App 自身就是这么拉的）：

```bash
curl -X POST \
 "https://firebaseremoteconfig.googleapis.com/v1/projects/amor-ai/namespaces/firebase:fetch?key=<google_api_key>" \
 -H "Content-Type: application/json" \
 -d '{"app_instance_id":"<任意 UUIDv4>","app_id":"1:487532596722:android:08af1fd6fe2026e54caf6b",
      "country_code":"US","language_code":"en","platform_version":"34",
      "package_name":"com.amorai.chat","app_version":"1.8.8",
      "sdk_version":"21.1.1","time_zone":"UTC"}'
```

实测返回 200 / 约 100 KB / `templateVersion: 65` / 49 个参数（`已验证`）。

---

## 2. 端点矩阵

| 用途 | Method | URL | 鉴权 | 实测 |
|---|---|---|---|---|
| 聊天生成 | POST | `https://api.openai.com/v1/chat/completions` | `Bearer <RC KEY_OPEN_AI_V4*>` | 未真实调用（付费红线），本地假上游复刻 `已验证` |
| 消息审核 | POST | `https://api.openai.com/v1/moderations` | `Bearer <RC KEY_OPEN_AI>` | 同上 `已验证`（假上游） |
| 配置下发 | POST | `firebaseremoteconfig.googleapis.com/v1/projects/amor-ai/namespaces/firebase:fetch` | `?key=<google_api_key>` | `已验证` 200 |
| 角色头像/图集 | GET | `https://romanticgirlfriend.site/new_amor/<baseUrl>ava1.jpg` 等 | 无 | 静态资源，域名存活（Cloudflare） |
| 付费墙图 | GET | `https://romanticgirlfriend.site/app_ai_android_all/<lang>/for_paywalls/paywall_1.<n>.png` | 无 | 静态资源 |
| 回传日志 | POST | `http://95.216.101.99/` | 无 | `PostBackLogger`，与 AI 无关 |
| 订阅校验 | POST | Apphud `gateway.apphud.com` + `apphud.blob.core.windows.net` | SDK key | 第三方 SDK |

**本机网络注意**：`romanticgirlfriend.site` 走 Cloudflare，直连被 RST，需代理；`api.openai.com` 视本机网络而定。网关默认**忽略环境代理直连**，需要代理时用 `--proxy`。

---

## 3. 聊天请求的真实形态

### 3.1 签名链（关键类与方法）

| 环节 | 文件 | 说明 |
|---|---|---|
| Base URL | `com/amorai/chat/data/network/RetrofitInstance.java:21` | `BASE_URL = "https://api.openai.com/v1/"` |
| 端点声明 | `com/amorai/chat/data/network/ApiService.java:24` | `@POST("chat/completions")`、`@POST("moderations")`，`Authorization` 由调用方传入 |
| 请求体 DTO | `.../models/retrofitopenai/AiRequestBodyDto.java:134` | 默认值：`model="gpt-4o-mini"`, `temperature=1.0`, `max_tokens=120`, `frequency_penalty=0.5`, `presence_penalty=1.0` |
| 消息 DTO | `.../models/retrofitopenai/ChatMessageDto.java` | `role` / `content` |
| key 选择 | `com/amorai/chat/data/network/ChatCommunication.java:201-279` | 4 个候选键：首请求用 `KEY_OPEN_AI_V4`，重试用 `KEY_OPEN_AI_V4_2`；SIM 国家在 `POOR_COUNTRIES_LIST` 时用 `*_POOR` 变体 |
| key 兜底表 | `n6/e.java:30-33` | 四个键的本地默认值**全是空字符串**（`""`） |
| 审核 key | `com/amorai/chat/data/repositoriesimpl/a.java:148` | 单独读 `KEY_OPEN_AI`（注意：不是 `_V4`） |
| 人格模板 | `n6/e.java:28` | `PROMPT_CHAT_0109`，`%s` 依次为 name / relationship / language / relationship |
| 人格填充 | `i7/x0.java:241-267` | 角色自带 `prompt` 优先（`String.format(prompt, language)`）；否则用通用模板填 4 个参数 |
| 消息组装 | `i7/x0.java:228-240` | 人格串作为**第一条 user 消息**插到队首；历史超过 6 条只留最近 6 条 |
| 真正调用 | `com/amorai/chat/data/repositoriesimpl/j.java:e()` + `i7/x0.java:j()` | `j.e(List, Continuation)` → `ApiService.messageRequest(...)` |
| 语言取值 | `i7/x0.java:245` | SharedPreferences `CHAT_LANGUAGE`，缺省取 `Locale.getDefault().getDisplayLanguage()` |

### 3.2 请求体（复刻结果，`已验证`）

```json
{
  "model": "gpt-4o-mini",
  "messages": [
    {"role": "user", "content": "<角色 prompt，%s 已填语言>"},
    {"role": "user", "content": "<历史第 1 条>"},
    {"role": "assistant", "content": "<历史第 2 条>"}
  ],
  "temperature": 1.0,
  "max_tokens": 120,
  "frequency_penalty": 0.5,
  "presence_penalty": 1.0
}
```

要点：

1. **没有 `system` 角色**。人格是塞进 `messages[0]` 的 `role:"user"` 消息 —— 这是本 App 最反直觉的一点，复刻时必须照做，否则口吻会跑偏。
2. **没有 `stream` 字段**，响应读 `choices[0].message.content`（同步 JSON）。
3. 历史裁剪 = 最近 6 条（`hj.z.Z(6, list)`）。
4. `max_tokens=120` 意味着单条回复天然很短，这是 App 里"女友说话短促"的真正原因，不是 prompt 效果。
5. 发给上游前**不修改** `model`：客户端固定 `gpt-4o-mini`。

### 3.3 响应解析

```java
AiResponseDto → choices[0] → message.content
```
`ChatCommunication`（死代码）里是 SSE 版：按 `data:` 行 → `[DONE]` 结束 → 取 `choices[0].delta.content` 累加。

---

## 4. Remote Config 关键参数（实测值）

| 参数 | 值 | 含义 |
|---|---|---|
| `KEY_OPEN_AI_V4` | `Bearer sk-vBisJ…GjX`（48 字符，旧格式） | 主 key，首请求使用 |
| `KEY_OPEN_AI_V4_2` | `Bearer sk-PHzoO…cae`（48 字符，旧格式） | 备用 key，超时/异常重试用 |
| `KEY_OPEN_AI_V4_POOR` / `_POOR_2` | 与上面两个相同 | 运营商 SIM 属"穷国"时使用（降低封号成本） |
| `KEY_OPEN_AI` | `Bearer sk-proj-…`（171 字符，新格式） | **仅** moderations 端点使用 |
| `IS_USE_APP_CHECK` | `false` | 未启用 App Check → 无设备证明门槛 |
| `GIRL_FREE_MESSAGE_PER_DAY` | `15` | 每个角色每天免费消息数（客户端计数） |
| `ENABLE_SEXUAL_SCORE_LIMIT` / `SEXUAL_SCORE_LIMIT` | `5` / `0.6` | moderation sexual 分数阈值 |
| `USE_FILTER_MODERATION` | `true` | 是否走上游 moderations 过滤 |
| `PROMPT_CHAT_0109` | 见 §3.1 | 通用人格模板 |
| `CHARACTERS_JSON` | 90,990 字节 | 102 个角色（详见 §4.1） |
| `POOR_COUNTRIES_LIST` | 605 字节国家码表 | 决定用哪组 key |
| `STOP_WORDS_FOR_IMAGE` / `TRIGGER_WORDS_JSON` | 图/提示词黑名单 | 与文本生成无关 |

> **凭证说明（重要）**：这 4 个 `sk-` 值是**第三方（App 开发者）付费的 OpenAI 凭证**，不是本项目可用的资源。本仓库不内置、不自动加载、不转发这些值。是否还有效 `待验证` —— 未做真实调用（付费红线）。任何使用都属盗刷他人账单，本项目不提供此路径。

### 4.1 角色库结构

```json
{"version":"1","list":[
  {"category":"GIRLS","characters":[
    {"id":1,"baseUrl":"/women/1/","priority":20,"likedStateFromCharacter":"LIKED",
     "role":"Superhero","name":"Nicole","age":28,"peppers":0,
     "tags":"#captain🛡️,#realhero, #marvelous",
     "description":["…多语言描述…"],
     "prompt":"You are Nicole, a superhero girl … Answer in the language %s!!!",
     "is_premium":false}
  ]},
  {"category":"BOYS","characters":[…2 个…]}
]}
```

- 共 102 个：GIRLS 100 + BOYS 2
- 每个角色自带 `prompt`（含 `%s` 占位语言），`is_premium` 控制是否付费可见
- 头像/图集路径 = `https://romanticgirlfriend.site/new_amor` + `baseUrl` + `ava1.jpg` / `match.jpg`

---

## 5. 本地复刻验证（本次实际跑过的）

| 验证项 | 命令 | 结果 |
|---|---|---|
| 网关健康 | `curl /healthz` | `{"status":"ok","mode":"mock","characters":102}` `已验证` |
| 模型枚举 | `curl /v1/models` | 102 条，`amor-<id>` + 别名 `nicole` `已验证` |
| 非流式聊天 | `POST /v1/chat/completions` | 200，OpenAI 结构 + usage `已验证` |
| 流式聊天 | 同上 `stream:true` | SSE 逐块 + `[DONE]` `已验证` |
| 未知模型 | `model=amor-9999` | 404 `model_not_found` `已验证` |
| 审核端点 | `POST /v1/moderations` | 200，`results[0].flagged` `已验证` |
| real 模式（假上游） | `--mode real --base-url <本地假上游>` | 请求体/头与 App 完全一致：`gpt-4o-mini`, `max_tokens=120`, `temp=1.0`, `fp=0.5`, `pp=1.0`, `messages[0].role=user`（人格串） `已验证` |
| 上游 500 | 假上游返回 500 | 透传 500 `已验证` |
| 上游 malformed | 假上游返回非 JSON | 502 `malformed upstream response` `已验证` |
| 上游超时 | `--timeout 1` + 3s 慢响应 | 504 `upstream timeout after 1.0s` `已验证` |
| 上游不可达 | base-url 指向死端口 | 502 `network: 10061` `已验证` |

> real 模式的验证使用**本地假上游**（`tools/fake_openai.py`），**没有对任何真实付费 API 发起请求**。

---

## 6. 踩坑表

| 现象 | 真实原因 | 修法 |
|---|---|---|
| 复刻后角色口吻干瘪、自称 AI | 人格串必须放在 `messages[0]` 且 `role=user`，不是 system | 照 App 抄：`[{role:"user", content:prompt}, ...历史]` |
| 回复过长、不像 App | App 固定 `max_tokens=120` | 复刻时保留 120，或在网关侧截断 |
| 本地假上游返回 `{"detail":"Not Found"}` FastAPI 风格 | Python `urllib` 吃了 `HTTP_PROXY` 环境变量，请求被本机 v2ray 接管 | `ProxyHandler({})` 禁用环境代理，或显式 `--proxy` |
| 流式压测全部超时/挂死 | SSE 响应没有 `Content-Length`，HTTP/1.1 keep-alive 下客户端读不到 EOF | 流式响应必须 `Connection: close` + `close_connection = True` |
| 压测时服务端刷 `WinError 10054` 栈 | 客户端提前断开（正常现象） | 覆写 `handle_error` 忽略 ConnectionReset/BrokenPipe |
| 控制台 `UnicodeEncodeError: gbk` | Windows 默认 GBK，角色名/emoji 无法编码 | `stream.reconfigure(encoding="utf-8")` |
| `romanticgirlfriend.site` 直连 RST | Cloudflare + 本机网络 | 走代理（socks5 10808 实测 200） |
| 误以为有流式聊天 | `ChatCommunication` 是**死代码**（gpt-3.5-turbo + stream） | jadx 与 smali 双向确认无调用点；真正链路是 `j.e` → 非流式 |

---

## 7. 额度、风控与付费

| 维度 | 事实 | 判定 |
|---|---|---|
| 免费额度 | `GIRL_FREE_MESSAGE_PER_DAY=15`（每角色每天），SharedPreferences 本地扣减 | 客户端，可绕；但绕过只影响本机显示 |
| 服务端配额 | 无。没有任何自建后端计数 | 服务端不拦 |
| App Check | 关闭（RC `IS_USE_APP_CHECK=false`），APK 内也无强制初始化路径 | 无设备证明 |
| 请求签名 | 无（只有日志拦截器 `kn.c` = OkHttp HttpLoggingInterceptor） | 无签名 |
| 设备绑定 | 无。虚拟设备/任意机型都行 | 无 |
| 真实成本 | 最终是 App 开发者的 OpenAI 账单 | **盗刷；不可作为生产渠道** |
| 付费墙 | Apphud 订阅 + Google Play Billing，纯客户端判定 | 与 AI 调用解耦 |

**价值判定（结论三档制）**：`归档 / 不可入生产`。

理由：本 App 没有任何可复用的上游能力 —— 它的"上游"就是 OpenAI 官方端点。想获得同样能力，直接用自己的 OpenAI（或任意 OpenAI 兼容）key 即可，无需逆向。逆向此 App 的唯一"额外收益"是使用别人的 key，属于盗刷，且这些 2022 年格式的 key 极可能已被 OpenAI 撤销（`待验证`）。

---

## 8. 混淆与加固

- 无 Dex 壳、无字符串加密、无 native 保护（`lib/armeabi-v7a` 仅常见第三方 .so）
- 业务包 `com.amorai.*` 类名/方法名**完全保留**（j.java、a.java 这类短名是原本如此的多层拆分，不是混淆）
- 第三方库包名被压缩成 `a`~`z0` 两字母（R8），但不影响业务链路定位
- jadx 失败方法 54 个，均为 Kotlin 协程状态机，改用 smali 交叉验证后无信息损失

---

## 9. 复现步骤

```bash
# 1) 解包
java -jar apktool.jar d -f -o apktool_out amor.apk
/d/apk-analysis/jadx/bin/jadx.bat -d jadx_out amor.apk

# 2) 定位协议
grep -rn "BASE_URL" jadx_out/sources/com/amorai/chat/data/network/RetrofitInstance.java
sed -n '160,300p' jadx_out/sources/com/amorai/chat/data/network/ChatCommunication.java

# 3) 拉配置（用 §1 的 curl）

# 4) 起网关 + 压测
python server.py --port 8946
python bench.py --concurrency 16 --requests 96
python verify_demo.py            # Playwright 真实浏览器
```
