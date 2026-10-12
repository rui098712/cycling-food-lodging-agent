# 骑行食宿规划对话式 Agent

输入骑行起终点、人均午饭餐标、人均晚饭餐标和人均住宿标准，程序调用高德 Web 服务获得骑行路线，在实际骑行道路里程的 50%～66.7% 区间连续搜索午饭，并返回该区间经过的市、县区和乡镇/街道，优先推荐镇区或市区附近的餐厅；每骑行 15～20 公里优先安排加油站、其次安排便利店或超市作为休息点；到达终点后优先推荐住宿，如能找到实际骑行道路距离不超过 3 公里的晚饭点，则附加到住宿方案中。

用户可以用一句话输入完整需求，也可以在多轮对话中分步补充或修改行程。项目支持本机 Ollama、DeepSeek API 和 OpenAI 三种可替换后端。对话模型通过 Function Calling 选择“更新行程状态”或“提交骑行规划”工具，地图路线和距离约束仍由高德与确定性代码执行。

如果用户已有准备实际跟随的 GPX 轨迹，可以将它作为唯一主路线导入。此时 Agent 不会再调用高德重新算路，而是严格按 GPX 轨迹的累计里程搜索推荐，最后导出包含推荐途经点的新 GPX。

项目同时提供适配手机的 Web 版：用户可以在浏览器中输入起终点或上传 GPX，查看文字版休息点、午饭、住宿和晚饭推荐，并下载加入推荐点的 GPX。原有命令行版本仍然保留。

## 系统架构

当前版本是一个“多轮 LLM + Function Calling + 确定性工具”的混合 Agent：

1. 程序保存完整会话历史和当前结构化行程状态，因此能补问缺失字段、理解后续修改。
2. LLM 通过 Function Calling 自主选择 `update_trip_request` 或 `submit_trip_plan`，而不是只返回一段文本。
3. Pydantic 验证所有模型生成的工具参数；工具白名单、最大步数和幂等提交防止越权、死循环和重复任务。
4. 后台 Agent 以 `Plan → Act → Observe` 循环调用规划、约束校验和 GPX 导出工具。
5. 地图工具执行地理编码、骑行路线和周边 POI 搜索；普通代码复核道路里程、午饭区间、休息点偏离和晚饭 3 公里约束。
6. 网页显示可审计的决策/工具/观察轨迹和校验报告，但不暴露模型隐藏思维过程。

这种分工非常重要：LLM 擅长理解语言，但不负责猜测地图数据。地图距离和人均消费约束仍由普通代码执行，更准确、更便宜也更容易测试。

### Agent 数据流

```text
用户多轮消息
    ↓
LLM 选择 Function Call
    ├─ update_trip_request → 校验并更新会话状态 → 补问缺失信息
    └─ submit_trip_plan  → 提交幂等后台任务
                              ↓
                  Plan → Act → Observe 循环
                  规划 → 约束复核 → GPX 导出
                              ↓
                  文字结果 + 执行轨迹 + GPX
```

## 实际道路里程如何计算

高德模式中，高德返回整条骑行路线的 `distance`，以及每个道路分段的 `step_distance`，单位都是米。项目使用 `step_distance` 做累计求和，因此路线进度不是起终点经纬度的直线距离比例。

GPX 模式中，主路线里程是 GPX 相邻轨迹点之间的累计距离，不调用高德重新算路。因此它反映用户传入的固定轨迹；如果轨迹来自实时 GPS 记录，里程也可能受 GPS 飘点影响。

两种模式都根据路线累计里程动态设置搜索点，保证午饭区间相邻搜索点的路线间隔不超过 4 公里。

## 项目结构

```text
.
├── main.py                         # 传统表单式命令行入口
├── llm_main.py                     # 自然语言 LLM 入口
├── cycling_agent/
│   ├── agent.py                    # Agent 编排与工具调用流程
│   ├── agent_runtime.py            # Plan-Act-Observe 循环、工具注册和约束校验
│   ├── amap_client.py              # 高德地图工具
│   ├── conversation_agent.py       # 多轮会话、Function Calling 和会话状态
│   ├── coordinate_systems.py       # WGS-84 与 GCJ-02 坐标转换
│   ├── deepseek_parser.py          # DeepSeek API 结构化参数提取
│   ├── gpx_io.py                   # GPX 固定路线导入和推荐点导出
│   ├── llm_parser.py               # Structured Outputs 参数提取
│   ├── ollama_parser.py            # Ollama 本地模型参数提取
│   ├── parser_factory.py           # Web/CLI 共用的模型后端选择
│   ├── llm_agent.py                # LLM 与地图规划器的组合层
│   ├── models.py                   # 输入、路线、POI 等数据模型
│   ├── ranking.py                  # 可解释推荐排序
│   ├── rest_stop_planner.py        # 周期性休息点规划
│   ├── route_progress.py           # 实际道路里程定位
│   └── web_service.py              # 后台规划任务和 JSON 结果转换
├── web_main.py                     # FastAPI 手机网页入口
├── web/static/                     # 响应式 HTML/CSS/JavaScript
├── evals/                          # 真实模型多轮评测数据与脚本
└── tests/                           # 不消耗高德额度的离线测试
```

## 运行手机 Web 版

先在 VS Code 终端进入项目并启动虚拟环境：

```bash
cd "/Users/rui/Documents/ChatGPT/食宿规划agent"
source .venv/bin/activate
python -m pip install -r requirements.txt
python web_main.py
```

电脑浏览器打开：

```text
http://127.0.0.1:8000
```

如果手机和 Mac 连在同一个 Wi-Fi，可在 Mac 的另一个终端查看局域网 IP：

```bash
ipconfig getifaddr en0
```

假设输出是 `192.168.1.23`，手机浏览器打开：

```text
http://192.168.1.23:8000
```

Mac 首次可能询问是否允许 Python 接受网络连接，要选择允许。关闭启动服务的终端或按 `Control+C`，手机网页就会停止访问。这种方式只适合本人测试；让任意用户从互联网访问，下一阶段需将 FastAPI 服务部署到云端并配置 HTTPS。

Web 后端提供：

- `POST /api/chat/sessions`：创建内存中的多轮 Agent 会话；
- `POST /api/chat/sessions/{session_id}/messages`：发送消息并执行 Function Calling 循环；
- `POST /api/plans`：提交起终点或 GPX 和三项人均标准；
- `GET /api/plans/{job_id}`：查询后台规划进度和文字结果；
- `GET /api/plans/{job_id}/gpx`：下载加入推荐点的 GPX。

路线规划会执行较多地图请求，因此 Web 版使用后台任务，手机页面轮询进度，不会让一个 HTTP 请求持续卡住。生成文件保存在本地 `.runtime/` 中，该目录已被 Git 忽略。

对话会话目前保存在 FastAPI 进程内存中，服务重启后会失效。这适合 Demo；正式部署可将会话状态替换为 Redis 或数据库存储。

## 运行 LLM 版本（Ollama / DeepSeek / OpenAI）

首次使用先安装 Ollama，然后下载模型：

```bash
ollama pull qwen3:4b
```

确保 Ollama 应用正在运行。如果没有启动，可以在一个单独终端中运行：

```bash
ollama serve
```

在 `.env` 中保留高德 Key，并添加本地模型配置：

```text
AMAP_API_KEY=你的高德Web服务Key
LLM_PROVIDER=ollama
OLLAMA_MODEL=qwen3:4b
OLLAMA_HOST=http://127.0.0.1:11434
```

`OLLAMA_MODEL` 可以在以后换成其他已下载的模型。不要把 `.env` 上传到 GitHub；仓库中的 `.env.example` 只包含占位符。

如果使用 DeepSeek API，改为：

```text
LLM_PROVIDER=deepseek
DEEPSEEK_API_KEY=你的DeepSeek_API_Key
DEEPSEEK_MODEL=deepseek-flash
```

DeepSeek 只在用户填写“自然语言描述”时调用；用户直接填表或仅上传 GPX 时不消耗模型额度。解析器使用结构化输出，关闭思考模式并限制最大输出，以降低延迟和费用。

```bash
source .venv/bin/activate
python llm_main.py
```

输入示例：

```text
我想从武威市天祝县达隆宾馆骑到张掖市七彩丹霞景区，人均午饭50，人均晚饭80，人均住宿250。
```

## GPX 固定路线导入和导出

导入已有 GPX 轨迹：

```bash
python llm_main.py --gpx "/完整路径/我的路线.gpx"
```

GPX 模式中，如果自然语言里没写起终点，程序会直接使用轨迹首尾，因此可以只输入：

```text
人均午饭50元，人均晚饭80元，人均住宿250元。
```

规划完成后，程序默认在原文件旁生成 `我的路线_planned.gpx`，其中包含：

- 原始 GPX 轨迹；
- 午饭候选点；
- 周期休息点；
- 住宿候选点；
- 经道路距离验证的晚饭点。

也可以自定义输出路径：

```bash
python llm_main.py \
  --gpx "/完整路径/我的路线.gpx" \
  --output-gpx "/完整路径/最终骑行路线.gpx"
```

不导入 GPX 时，也可将高德规划的路线和推荐点导出：

```bash
python llm_main.py --output-gpx "骑行规划.gpx"
```

GPX 坐标遵循 WGS-84，高德 POI 使用 GCJ-02。项目在导入时将轨迹转成 GCJ-02 用于高德搜索，导出时再将高德推荐点转回 WGS-84；原始 GPX 轨迹本身保持不变。程序会拒绝用输出文件覆盖导入的原始 GPX。

如果不想启动任何 LLM，仍可以运行原来的表单式版本：

```bash
source .venv/bin/activate
python main.py
```

如果以后获得 OpenAI API Key，只需把 `.env` 改为：

```text
LLM_PROVIDER=openai
OPENAI_API_KEY=你的OpenAI_API_Key
OPENAI_MODEL=gpt-6-astra
```

运行离线测试：

```bash
python -m unittest discover -s tests -v
```

## Agent 评测

48 项离线测试覆盖路线、POI、行政区识别、镇区午饭优先级、GPX、工具白名单、多轮记忆、幂等提交、最大步数、Ollama 适配和 Web API，不消耗 LLM 或高德额度。

使用真实 DeepSeek 评测集检查字段准确率和任务完成率：

```bash
python -m evals.run_agent_evals --provider deepseek
```

该评测不调用高德，但会产生少量模型 API 用量。当前内置 3 个用例，覆盖一次输入、多轮补齐和后续修改。

DeepSeek 的 Chat Completions 是无状态的，因此本项目由服务端保存并重传会话历史；Function Calling 按官方的 assistant `tool_calls` → 本地执行 → `tool` 结果 协议实现：[Multi-round Conversation](https://api-docs.deepseek.com/guides/multi_round_chat/)、[Tool Calls](https://api-docs.deepseek.com/guides/tool_calls/)。

`.env` 和 `*.gpx` 已被 `.gitignore` 忽略，密钥和可能包含个人行程的轨迹都不应上传到 GitHub。

## 排序规则与数据限制

- 推荐分由高德评分、参考消费和距搜索点距离组成；午饭候选位于镇区或市区附近时会获得额外优先级，并在推荐理由中明确说明。
- 午饭区间的市、县区和乡镇/街道名称来自高德逆地理编码；路线每隔最多 4 公里采样，因此结果用于沿线选点，不等同于完整的行政区边界测绘。
- 高德缺失评分或价格时，程序明确显示“暂无”，不会虚构数据。
- 酒店 POI 的 `cost` 是高德提供的参考消费，不保证等于某一天的实时房价。接入酒店预订平台前，结果只能作为初筛。
- GPX 模式可以保证搜索和导出基于同一条轨迹，但不能阻止骑行者主动偏航或导航软件因封路重新规划。
- 周边距离表示 POI 到路线采样点的距离，不等于用户实际绕行距离。后续可以为候选 POI 再调用一次骑行规划，计算真实绕行成本。
- Agent 优先选择终点附近的住宿，再围绕每个住宿搜索晚饭；随后用坐标直线距离做低成本预筛选，并调用高德骑行规划验证实际道路里程。找不到 3 公里内晚饭时，住宿仍会保留。
- 名称中含有“暂停营业”“已关闭”“停业”等状态的住宿或餐厅不会进入推荐结果。
- 休息点从上一个休息点重新累计道路里程，优先搜索之后 15～20 公里的加油站，其次搜索便利店或超市；候选应距原路线不超过 100 米，没有结果时放宽到 300 米，再没有则把搜索窗口延长到 20～30 公里。
- 如果 15～30 公里内仍未找到休息点，下一轮会紧接从 30 公里处继续搜索，不再跳过 30～45 公里路段；后续找到时，输出的间隔仍按上一个真实休息点计算。
- 休息点查询每隔最多 5 公里在路线创建搜索中心，使用 3 公里查询半径召回候选，再把候选投影回原路线，以路线累计里程和距路线距离做最终过滤。
- 同一组独立 POI 搜索最多使用 4 个并发工作线程，同时在客户端统一限制请求启动频率；高德客户端会对临时网络错误、非法 JSON 和 QPS 限流自动退避重试，并缓存同次运行中的相同请求。非关键 POI 请求失败时，Agent 保留已获得的结果并输出警告，不会让整份规划直接丢失。
