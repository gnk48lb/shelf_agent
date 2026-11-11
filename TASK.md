# TASK：搭建「螺丝装箱 → 搬运上架」仿真环境与异常自愈 Agent 骨架

你是资深 Python 工程师。当前目录**不是空文件夹**：`docs/` 下已有两份前期文档——`docs/assumptions.md`（默认假设 A1–A18：§9 要求的 A1–A9 真机相关口径，加上规格留白的补充假设）与 `docs/open_questions.md`（开放问题 Q1–Q13：需师兄答复的，以及本期明确不做的候选扩展）。**源码、配置、测试仍需从零创建**；但动笔前先通读这两份文档——已有假设按其既定口径实现，不要另起炉灶；本 TASK.md 优先于文档，两者冲突时以 TASK.md 为准，并把改动同步回 `docs/assumptions.md`。下面是完整需求。
**除非被阻塞，不要向我提问**：文中没定义的细节，按"默认假设"实现，并登记到 `docs/assumptions.md`（**从 A19 起续编**，注明"待师兄确认"）。

---

## 0. 背景与目标

场景来自实验室真机流程的抽象：

- **一号机器人**（固定不动）把螺丝装进箱子。技能由 VLA 实现，**阻塞、不可中断**，只返回 `done` 或 `timeout`；可能卡住不动（只能靠超时发现）。
- 一号机器人的**头部相机**能看到箱内，用来判断螺丝是否装满。
- 装好后，**二号机器人**（可移动）把箱子搬到货架；同样阻塞、不可中断，返回 `done` / `timeout`。
- **巡检机器人**用途未知，仅占位。
- 机器人可单独或整体 `reset`（回初始位姿），耗时未知，**不会失败**。

要做的东西：一个纯 Python、离线可跑的**仿真环境**（带可配置、可复现的故障注入），以及能在其中**异常自愈**的 agent：

1. 确定性规则策略（baseline：超时 reset 重试、不满补装、散落求助……）；
2. LLM ReAct agent（用 tool calling 调同一套工具；测试中用脚本化 LLM，不联网）；
3. 评测工具（批量运行、指标、故障率扫描、出图），用于组会汇报；
4. 扎实的 pytest 离线单测。

组会上我要演示：架构、故障注入、几条自愈流程的时间线、成功率随故障率变化的曲线。所以**清晰、可演示、测试扎实**比功能多更重要。

---

## 1. 工作方式（必须遵守）

1. 先通读本文，输出实施计划和任务清单，再动手。
2. 按第 9 节分阶段交付。每阶段结束运行 `pytest -q`，全绿才进入下一阶段；Phase 0 `git init`，每阶段一次 commit（`.gitignore` 覆盖 `.venv/`、`__pycache__/`、`results/`、`.env`）。
3. 如实汇报：没运行过的不要说通过；测试被跳过或依赖没装上要写明原因；**不要通过删除、弱化测试或硬编码预期来让测试通过**，测试失败就修根因。
4. 不要添加本文未列出的工具、故障类型和依赖。认为有必要的扩展，写进 `docs/open_questions.md`，不要擅自实现。
5. 标记含义：**[必]** 必做；**[应]** 应做；**[可]** 前面全绿后再做。

---

## 2. 技术约束

- Python ≥ 3.10；包管理用 `uv`（`pyproject.toml`，src 布局 `src/shelf_agent/`）；uv 不可用时退回 venv + pip，并在 README 说明。
- 第三方依赖仅限：`pyyaml`、`pillow`、`matplotlib`、`pytest`、`pytest-cov`（dev）。其余用标准库（`dataclasses`、`argparse`、`json`、`urllib`、`random`、`hashlib`、`logging`）。**不要**使用 deepagents / langchain / langgraph / pydantic / gym。
- 开发机可能是 Windows：全程 `pathlib`；文件读写显式 `encoding="utf-8"`；CLI 入口对 stdout/stderr 做 UTF-8 重配置；不依赖 shell 特有语法；不使用 fork 多进程。
- **离线且快**：测试不得联网、不需要任何 API key / GPU；库代码中**禁止 `time.sleep`**（仿真时间用虚拟时钟；LLM 客户端的退避睡眠必须可注入）；整套 `pytest` < 30 秒。
- **可复现**：所有随机性来自显式 seed；用 `hashlib.sha256(f"{seed}:{stream}")` 派生**独立随机流**（每个技能的故障流、验证器噪声流各自独立），保证"换策略不会改变某技能第 n 次调用的故障命运"。同 seed 同配置必须得到逐字节相同的轨迹。
- 代码规范：类型标注；单文件 < 400 行；注释与 docstring 用中文、简洁；标识符、日志 key、工具名用英文；必做功能不留 `TODO` / 空实现；不得 `except Exception: pass`。

---

## 3. 领域规格

### 3.1 工具

工具名用下划线（OpenAI 兼容 API 不允许函数名含点）；YAML 里保留 `lab_name` 作与实验室名称的对照。

| 工具名 | 实验室名 | 参数 | status | data | 说明 |
|---|---|---|---|---|---|
| `robot1_install_screws` | robot1.install_screws | 无 | done / timeout | 无 | 阻塞、不可中断；语义为"补装到目标数" |
| `robot1_get_head_image` | robot1.get_head_image | 无 | ok | image_id, path | 只返回图像引用，1 s |
| `verify_box` | （本项目新增） | 无 | ok / error | inside, outside, clear | 拍照 → 验证器；箱子不在工位时 error |
| `robot2_carry_box` | robot2.carry_box | 无 | done / timeout | 无 | 阻塞、不可中断 |
| `reset` | reset | robot ∈ {robot1, robot2, all} | ok | 无 | 永不失败 |
| `inspector_run` | inspector.run | 无 | done | box_on_shelf（仅 shelf_status 模式） | 默认 noop |
| `ask_human` | （本项目新增） | question: str | ok / error | answer | 有延迟与配额 |
| `finish` | （本项目新增） | success: bool, summary: str | ok | 无 | 结束 episode |

约定：`status` 取值为 `done`/`timeout`（实验室技能）、`ok`（其余）、`error`（守卫或前置违规，带 `error_code`）。install/carry 的返回**只有** status 与耗时，不带任何状态信息。

### 3.2 世界状态与技能语义

状态：虚拟时钟 `time_s`；机器人 `robot1/robot2 ∈ {OK, FAULT}`；箱子 `inside`（箱内螺丝数）、`outside`（箱外散落数）、`location ∈ {STATION, SHELF, LOST}`；人类求助计数。**真值对 agent 不可见**，只能通过工具返回观察。世界用 dict 管理箱子和货架槽（按多箱多槽设计），但 `real_screw` 配置里只有 `box_1` 和一个槽，工具无参数。

默认参数（全部写进 `configs/real_screw.yaml`，可配）：

| 项 | 默认值 |
|---|---|
| 目标螺丝数 N | 6 |
| install | 均值 45 s，抖动 ±20%，timeout 120 s |
| carry | 均值 60 s，抖动 ±20%，timeout 180 s |
| get_head_image / verify_box | 1 s / 3 s（`BOX_NOT_AT_STATION` 错误 1 s） |
| reset | robot1 20 s，robot2 30 s，all 取最大 |
| inspector_run | 40 s，模式 noop |
| ask_human | 延迟 120 s，配额 2 |
| failures.rate | 0.15（扫描时覆盖） |
| 故障权重 | install：STUCK 0.4 / INCOMPLETE 0.4 / STRAY 0.2；carry：TIMEOUT 0.5 / SILENT_FAIL 0.5；`p_lost` 0.3 |
| budget | `soft_limit` 40，`hard_extra` 3，`max_invalid_calls` 10 |

语义规则（逐条实现，每条至少一个测试）：

1. **耗时**：成功的调用 = 抽样时长；timeout = `timeout_s`。
2. **`robot1_install_screws`**，令 `missing = N − inside`：
   - 前置：robot1 为 OK 且箱子在 STATION。**前置不满足（含 FAULT）→ 表现为"卡住但不改变状态"**：耗时 `timeout_s`，返回 timeout，robot1 状态不变。
   - 无故障事件：`inside := N`（补装到 N），返回 done。
   - 故障事件（仅当 `missing ≥ 1`，否则按无事件处理）：
     - STUCK：`progress = k ∈ [0, missing−1]`，`inside += k`，robot1 := FAULT，耗时 `timeout_s`，返回 timeout。
     - INCOMPLETE：`progress = k ∈ [0, missing−1]`，`inside += k`，返回 done（正常耗时）。
     - STRAY：`inside := N−1`，`outside += 1`，返回 done（最后一颗掉在箱外）。
3. **`robot2_carry_box`**：
   - 前置：robot2 为 OK 且箱子在 STATION；不满足 → 同上（耗时 `timeout_s`，返回 timeout，状态不变）。
   - 无事件：箱子 `location := SHELF`，返回 done。
   - TIMEOUT：robot2 := FAULT；箱子以概率 `p_lost` 变 LOST，否则仍在 STATION；耗时 `timeout_s`；返回 timeout。
   - SILENT_FAIL：返回 done，但箱子仍在 STATION。
   - robot2 **不检查**箱内是否完整；搬运时若 `inside != N or outside > 0`，backend 记一次 `unsafe_release` 事件（仅供评测）。
4. **`verify_box`**：前置箱子在 STATION，否则 `error`（`BOX_NOT_AT_STATION`）；否则经 ImageLibrary 取图、Verifier 出报告 `{inside, outside, clear}`。
5. **`reset(robot)`**：目标机器人 FAULT → OK；耗时取各目标 reset 时长的最大值；不改变箱子；对无故障机器人也照常耗时（评测记 `unnecessary_reset`）；永不失败。
6. **`inspector_run`**：`noop` 模式仅返回 done；`shelf_status` 模式 `data={"box_on_shelf": bool}`（真值，可配 `false_negative` 概率）。默认 `noop`。
7. **`ask_human`**：见 3.4。
8. **`finish(success, summary)`**：结束 episode，真值由评测判定。

### 3.3 故障注入

- 每个技能一个独立随机流。每次调用先以概率 `rate` 触发故障事件，再按权重选类型。`rate` 是全局旋钮（扫描用），也可按技能单独覆盖。
- **脚本化注入**（测试与演示的基础）：
  `FailureModel.scripted({"robot1_install_screws": ["STUCK", None, ("INCOMPLETE", {"progress": 2})], ...})`
  按调用顺序消费，耗尽后恢复正常；`None` 表示无事件；字符串是默认参数的简写（STUCK 默认 `progress=0`；INCOMPLETE 默认 `progress=missing−1`；TIMEOUT 默认 `lost=False`）。
- 事件只改变世界，不直接告诉 agent。

### 3.4 验证器、图像与人类

- `perception/completion.py` 里只有**一处**完成判据：`is_complete(report, N) = report.clear and report.inside == N and report.outside == 0`。评测用的真值判据 `truth_success` = 箱子在 SHELF 且 `inside == N and outside == 0`。
- `ImageLibrary.capture(truth) -> ImageRef`。`SyntheticImageLibrary` 用 Pillow 画出箱子与箱内/箱外螺丝（图像确实编码计数）；默认不落盘（`save_images=False`），开启后写入 `out/images/`。`ImageRef` 内部带 `meta`（真值，仅供 Oracle），**对 agent 暴露的只有 `image_id`、`path`**。
- `Verifier` 协议：`verify(image_ref, rng) -> Report{inside, outside, clear}`。实现：
  - `OracleVerifier`：读 meta，完全准确（仅仿真用，文档注明）；
  - `NoisyVerifier(p_inside_correct=0.95, boundary_confusion=0.10, p_unclear=0.03, p_outside_miss=0.05)`：先以 `p_inside_correct` 报真值，否则报 `k±1`（等概率，下限 0）；若真值为 N±1，再以 `boundary_confusion` 的概率覆盖为 N（这是"错误放行"的来源）；`outside > 0` 时以 `p_outside_miss` 报 0；`clear=False` 的概率为 `p_unclear`。支持 `from_confusion_table(json)` 加载经验混淆表 `{true_inside: {pred_inside: prob}}`；
  - `ScriptedVerifier`：按脚本逐次返回报告（测试与演示用）。
  - 留好接口以便以后接 VLM 验证器；本期**不要**实现 VLM 验证器。
- `ScriptedHuman`（`env/human.py`）：`ask_human(question)` 耗时 `latency_s`，配额 `max_asks`。人类会处理：把箱外散落螺丝放进箱内（`inside += outside; outside = 0`）；LOST 的箱子放回 STATION；FAULT 的机器人恢复 OK。返回 `data={"answer": "<做了什么>"}`；无事可做则回答"未发现异常"。配额用尽 → `error`（`HUMAN_UNAVAILABLE`，耗时 0）。

### 3.5 观测与防泄漏

agent 只能看到 ToolResult：`status`、`error_code`、`message`、`duration_s`、`data`。`data` 的键必须在 YAML 契约的 `returns_data` 中声明，executor 校验，**多出未声明的键视为 bug（抛 `ContractViolation`）**。真值只能通过 `backend.debug_truth()`（评测/测试专用）取得，agents 层不得 import 它。

### 3.6 策略

`Policy` 协议：`name`；`start(info)`；`next_call(history) -> ToolCall`（history 是此前的 (call, result) 列表）。`info` 含工具规格、目标螺丝数 N、策略限额。

1. **NaiveScriptPolicy**（对照）：install → carry → finish(True)。不验证、不重试。
2. **RuleSelfHealPolicy**（核心）：显式状态机（状态枚举 + 转移表 + docstring 里的流程说明）。默认限额：`max_install_calls=6`、`max_resets_per_robot=3`、`max_unclear_retries=2`、`max_carry_attempts=3`、`max_human_asks=2`。流程：
   - **INSTALL**：调用 install。timeout → RESET1（reset robot1）→ 回 INSTALL；done → VERIFY。
   - **VERIFY**：调用 verify_box。complete → CARRY；`not clear` 或 `inside > N`（报告自相矛盾）→ 重拍（≤ `max_unclear_retries`），仍不行 → HUMAN；`outside > 0` → HUMAN（散落）；`inside < N` → 回 INSTALL（补装）。
   - **CARRY**：调用 carry。done → POST_CHECK；timeout → reset robot2 → 调 verify_box 看箱子是否还在工位：返回 `BOX_NOT_AT_STATION` → HUMAN（箱子丢失）；返回报告 → 回 CARRY 重试（≤ `max_carry_attempts`）。
   - **POST_CHECK**：调用 verify_box。返回报告（箱子仍在工位）→ 判定搬运静默失败，回 CARRY；返回 `BOX_NOT_AT_STATION` → 视为已搬走。若配置为 `shelf_status`，再 `inspector_run` 确认，`box_on_shelf=False` → HUMAN。
   - **HUMAN**：`ask_human(<原因>)`，之后一律回 VERIFY 重新检查；配额用尽或限额超过 → FINISH(False, 原因)。
   - **FINISH**：`finish(success, summary)`；仅在走完 POST_CHECK 且无遗留异常时 `success=True`。
   - 任一限额耗尽：优先 HUMAN（有配额时），否则 FINISH(False)。
   - **任何情况下，验证器未报告 complete 时不得调用 carry**（用测试保证）。
3. **ReActAgent**（见 3.7）。

### 3.7 ReAct agent 与 LLM 客户端

- `LLMClient` 协议：`chat(messages, tools) -> LLMResponse(content, tool_calls)`。每轮**最多执行一个** tool call（多于一个只执行第一个并记警告）。
- `ScriptedLLM`：按脚本（或回调）返回响应，测试用。
- `OpenAICompatClient`：基于标准库 `urllib` 的 `POST {base_url}/chat/completions`（`model`、`messages`、`tools`、`tool_choice="auto"`、`temperature`），解析 `tool_calls[*].function.{name, arguments}`；429/5xx 指数退避重试（`sleep` 与 `transport` 可注入，便于测试）；`base_url`、`model`、API key 来自配置/环境变量；**不得硬编码任何服务地址，不得在日志或轨迹中出现 key**。README 说明如何指向 Gemini 的 OpenAI 兼容端点或本地 vLLM（以各自官方文档为准）。
  - 请求体可另带可选的 `max_tokens`：思考型模型可能整轮只输出 thinking 而没有 tool call，被误判成"无 tool call"。
  - **本机 Magpie 网关可作为 OpenAI 兼容端点**（可选目标之一，非必需）：`base_url` 取 `http://127.0.0.1:3425/v1`——**要带 `/v1`**，因为客户端自己拼 `/chat/completions`（这与 Anthropic 路径不带 `/v1` 相反）。`model` 取 `GET http://127.0.0.1:3425/v1/models` 返回的 id：`workbuddy/*`、`trae-cn/*`、`qoder-cn/*` 原生就是 `/v1/chat/completions`，`minimax-code/*` 原生是 `/v1/messages`（由网关翻译后同样支持 tool calling）。API key 是占位符（`local` 即可，网关不校验）。**具体取值一律走环境变量**，代码与默认配置里不写死任何地址，README 给一份示例。
- `ReActAgent`：system prompt（英文，放 `agents/prompts.py`）由契约自动生成工具说明，并写明规则：
  ① `verify_box` 未报告完整（clear、inside==N、outside==0）不得 carry；
  ② 某机器人 timeout 后，先 reset 该机器人再重试；
  ③ 机器人无法处理的情况（散落、箱子丢失）或重试耗尽才 ask_human；
  ④ 宁可 `finish(success=False)` 并说明原因，也不要猜测。
  无 tool call 的响应：追加一条提醒并重试，连续 2 次仍无 → 该 episode 记 `AGENT_ERROR`。默认只暴露 `verify_box`，不暴露 `robot1_get_head_image`（图像直通 VLM 是后续工作）。
- ReAct 的完整 messages 要进入轨迹 JSONL（OpenAI chat 格式，另存 `tools` schema），以便以后做 SFT。

### 3.8 Executor 与预算

`ToolExecutor` 是 agent 与 backend 之间的唯一通道：校验工具名与参数类型（按契约）→ 检查 episode 状态与预算 → 调 backend → 校验 `data` 键 → 记轨迹。

- 工具名/参数错误：返回 `error`（`UNKNOWN_TOOL` / `INVALID_ARGS`），**不推进仿真时钟**，计入 `invalid_calls`。
- **两级预算（软/硬）**：总调用数超过 `soft_limit` 后，**物理技能**（install / carry / reset / inspector）不再执行，返回 `error`（`BUDGET_EXCEEDED`）；`verify_box`、`ask_human`、`finish` 仍可用；之后再收到 `hard_extra` 次被拒的调用，episode 以 `ABORTED_BUDGET` 终止。`max_invalid_calls` 超限同样硬停。
- 轨迹记录：每步 `{i, t_s, call, result, truth_snapshot}`；`truth_snapshot` 单独存放，**不得进入 agent 的任何输入**。

---

## 4. 架构

### 4.1 目录

```
shelf_agent/
├── pyproject.toml
├── README.md                  # 中文：目标、架构图、工具表、故障表、自愈流程图(mermaid)、运行方式、真实 sweep 结果、局限
├── docs/
│   ├── assumptions.md         # 默认假设清单（A1…），标注待师兄确认
│   └── open_questions.md
├── configs/
│   ├── real_screw.yaml        # 与真机对齐：1 箱 1 槽，工具无参数
│   └── extended.yaml          # [可] 2 箱 2 槽，带 box_id / slot_id 参数
├── src/shelf_agent/
│   ├── config.py                                              # L0
│   ├── utils/        rng.py clock.py jsonl.py stats.py        # L0
│   ├── contracts/    spec.py types.py schema.py               # L1
│   ├── perception/   completion.py imagelib.py verifier.py    # L1
│   ├── env/          world.py failures.py human.py backend.py sim_backend.py   # L2
│   ├── runtime/      executor.py budget.py trajectory.py      # L3
│   ├── agents/       policy.py naive.py rule_selfheal.py llm_client.py react_agent.py prompts.py   # L4
│   └── eval/         runner.py metrics.py scenarios.py sweep.py report.py cli.py                    # L5
└── tests/
```

### 4.2 契约 YAML

`configs/*.yaml` 顶层：`scenario`、`target_screws`、`tools`、`failures`、`verifier`、`human`、`inspector`、`budget`、`rule_policy`。每个工具的契约示例：

```yaml
- name: robot1_install_screws
  lab_name: robot1.install_screws
  robot: robot1
  description: "一号机器人把螺丝补装到箱内，直到达到目标数量。阻塞、不可中断。"
  params: []                        # extended 配置里为 [{name: box_id, type: string}]
  returns_status: [done, timeout]
  returns_data: []
  timeout_s: 120
  duration: {mean_s: 45, jitter: 0.2}
  preconditions: ["robot1 为 OK", "箱子在工位"]   # 仅文档，不执行
  effects: ["箱内螺丝数补到目标数"]             # 仅文档，不执行
```

- 加载时校验：名称唯一且符合 `^[a-zA-Z0-9_-]+$`、参数类型合法（string / int / bool / enum）、必填字段齐全；错误信息要指出具体字段路径。
- `preconditions` / `effects` 只是文档；**不要实现表达式 DSL**，语义全在 `SimBackend` 代码里。
- 提供 `to_openai_tools(spec)`，生成 function-calling schema。

### 4.3 分层与依赖规则

单向依赖，只能向下 import：

`L5 eval → L4 agents → L3 runtime → L2 env → L1 contracts / perception → L0 config / utils`

- `config` 可被任意层 import；`contracts` 与 `perception` 同层互不 import。
- `env`、`contracts`、`perception`、`runtime` **不得** import 任何 LLM / 网络库。
- `agents` 只经 `ToolExecutor` 和契约类型与环境交互，**不得 import `env.*`**。
- 由 `tests/test_architecture.py` 用 AST 扫描**强制**以上规则。

---

## 5. 评测与 CLI

**Outcome 定义**（`eval/metrics.py`）：

| Outcome | 条件 |
|---|---|
| SUCCESS | finish(True) 且 truth_success |
| FALSE_FINISH | finish(True) 但 not truth_success（错误放行/谎报成功） |
| SAFE_ABORT | finish(False) 且 not truth_success（诚实失败） |
| MISSED_SUCCESS | finish(False) 但 truth_success |
| ABORTED_BUDGET | 预算硬停 |
| MAX_STEPS | 达到最大步数仍未 finish |
| AGENT_ERROR | agent 连续无有效 tool call |

每个 episode 的指标：outcome、makespan_s（虚拟时钟终值）、各工具调用数、n_timeouts、n_resets、n_unnecessary_resets、n_wasted_calls（打进 FAULT 或前置不满足的调用）、n_human_asks、n_invalid_calls、unsafe_release（bool）。聚合时：比例给 Wilson 95% 置信区间（`utils/stats.py`，用已知值测试），均值给标准误。**同一组 seed 跑所有策略**（配对比较）。

`scenarios.py`：命名脚本场景，**同时用于测试和演示**，至少包含：

| 场景 | 脚本 | 期望 |
|---|---|---|
| `happy` | 无故障 | SUCCESS |
| `stuck_once` | install:[STUCK] | reset robot1 → 补装 → SUCCESS |
| `incomplete_topup` | install:[INCOMPLETE(progress=3)] | 验证发现不满 → 补装 → SUCCESS |
| `stray_needs_human` | install:[STRAY] | 验证发现箱外散落 → ask_human → SUCCESS |
| `carry_timeout_reset` | carry:[TIMEOUT(lost=False)] | reset robot2 → 确认箱子在工位 → 重试 → SUCCESS |
| `carry_silent_fail_detected` | carry:[SILENT_FAIL] | POST_CHECK 发现箱子仍在工位 → 重搬 → SUCCESS |
| `lost_box_needs_human` | carry:[TIMEOUT(lost=True)] | reset → 发现箱子不在工位 → ask_human → 重新验证并搬运 → SUCCESS |
| `stuck_forever` | install:[STUCK]×10 | 限额耗尽 → SAFE_ABORT，且 `unsafe_release=False` |
| `verifier_false_positive` | ScriptedVerifier 在真值 N−1 时报 complete | FALSE_FINISH 且 `unsafe_release=True`（演示验证器可靠性的重要性） |

**CLI**（`python -m shelf_agent.eval.cli ...`，同时在 `pyproject.toml` 注册 `shelf-agent` 入口）：

- `list-scenarios`
- `demo --scenario NAME [--show-truth] [--no-color]`：打印时间线（虚拟时间、调用、返回、耗时；`--show-truth` 并排显示真值状态）与最终结论。
- `sweep --config configs/real_screw.yaml --seeds 200 --failure-rates 0,0.05,0.1,0.2,0.3 --policies naive,rule_oracle,rule_noisy --out results/`
  其中 `rule_oracle` = RuleSelfHeal + OracleVerifier，`rule_noisy` = RuleSelfHeal + NoisyVerifier（默认参数）。输出 `episodes.jsonl`、`summary.csv`、`summary.md`、`success_vs_failure_rate.png`（双图：成功率；FALSE_FINISH 与 unsafe_release 比例；含置信区间）。
- `run --policy react ...`：连真实 LLM 跑（需网络与 key；**不在测试中执行**），轨迹落盘。LLM 目标由环境变量指定：`SHELF_AGENT_LLM_BASE_URL` / `SHELF_AGENT_LLM_MODEL` / `SHELF_AGENT_LLM_API_KEY`；指向本机 Magpie 时依次为 `http://127.0.0.1:3425/v1`、一个 `GET /v1/models` 里存在的模型 id、占位符 `local`。

轨迹 JSONL 每行一个 episode：`{episode_id, seed, config_hash, policy, outcome, metrics, steps[], messages[]?, truth_trace[]}`；`config_hash` 为规范化配置的 sha256。

---

## 6. 测试要求

`pytest -q` 全绿、无联网、无 key、< 30 s。测策略时用**脚本化故障**，不要 mock 掉被测对象。测试必须断言行为，不能只测 import。

| 文件 | 内容 |
|---|---|
| `test_architecture.py` | 分层规则；env/contracts/perception/runtime 无 LLM/网络 import；agents 不 import env；库代码除 `llm_client.py` 的可注入默认 sleep 外无 `time.sleep`；src 内除 CLI/report 外无 `print(` |
| `test_utils.py` | 派生随机流互相独立；sha256 派生结果稳定（固定期望值）；时钟单调；Wilson 区间（已知值）；jsonl 往返（含中文） |
| `test_contracts.py` | 合法加载；各类非法 YAML 的错误信息指到具体字段；`to_openai_tools` 结构；工具名合规 |
| `test_failures.py` | 脚本化消费顺序与耗尽后恢复；rate=0 无事件、rate=1 必有事件；权重在大样本下近似（固定 seed，容差，不得 flaky）；各技能流互相独立 |
| `test_sim_backend.py` | 3.2 每条语义规则至少一个测试，含前置违规、FAULT 期间调用、reset 不动箱子、`unsafe_release` 记录、LOST、inspector 两种模式、`BOX_NOT_AT_STATION`、时间累计 |
| `test_verifier_imagelib.py` | Oracle 准确；Noisy 同 seed 可复现且统计近似（`boundary_confusion`、`p_unclear`）；混淆表加载；Scripted；Synthetic 图像可生成且内容随计数变化（Pillow 缺失时明确失败，不要静默跳过） |
| `test_human.py` | 三种修复；无事可做；配额用尽；耗时 |
| `test_executor_budget.py` | 未知工具/非法参数不推进时钟；软停止（物理技能被拒，finish/verify/ask 可用）；硬停止；`data` 未声明键 → `ContractViolation`；轨迹含 truth_snapshot 而 ToolResult 不含 |
| `test_no_leak.py` | 对所有脚本场景，断言发给 policy/LLM 的内容里不含任何真值字段（键名与取值双重检查） |
| `test_policies.py` | 每个脚本场景断言**精确的工具调用名序列**和 outcome（这是"自愈流程"的证明）；property 测试：对 200 个 seed × 多个故障率 × Noisy 验证器，policy 只在**上一次验证报告 complete** 时才 carry；限额与 HUMAN 之后回 VERIFY 的行为 |
| `test_react_agent.py` | ScriptedLLM 走通完整 episode；非法 JSON 参数/未知工具/无 tool call 的处理；多 tool call 取第一个；messages 轨迹格式；AGENT_ERROR 分支 |
| `test_llm_client.py` | fake transport：请求体结构、tool_calls 解析、429/503 退避（注入 sleep，断言重试次数与间隔序列）、key 不出现在日志与异常信息里 |
| `test_eval.py` | 7 种 outcome 各一例的分类；指标聚合；配对 seed；小规模 sweep 端到端；同 seed 两次运行 `episodes.jsonl` 逐字节相同；`demo` 退出码 0 且输出含关键步骤；出图文件存在且非空 |
| `test_fuzz_invariants.py` | 随机策略（随机工具、随机参数）跑 N 步：不崩溃；`0 ≤ inside ≤ N`、`outside ≥ 0`；时钟单调不减；箱子恰在一处；FAULT 只能被 reset / ask_human 清除 |
| `test_extended.py` [可] | 2 箱 2 槽配置：两箱均上架；槽位被占时换槽 |

[应] 用 `pytest-cov` 报告覆盖率，目标 `env / runtime / agents / eval` ≥ 90%。

---

## 7. 质量红线

- README 里所有数字必须由实际运行得到，并附命令与 seed 数；不得手写数字。
- 随机性只来自注入的 rng；全局无可变单例。
- 公开函数有类型标注；错误信息可读（指出出错的字段/工具/参数）。
- 日志用 `logging`；库代码里不要 `print`（CLI 与 report 除外）。

---

## 8. 不做

真机接入；任何训练（SFT / RL）；VLM 验证器；多箱并发流水线；GUI / Web；Docker；任何联网的测试。

---

## 9. 分阶段交付与验收

| 阶段 | 内容 | 验收 |
|---|---|---|
| **Phase 0 [必]** | 脚手架：pyproject、目录、`git init`、`.gitignore`、`config.py` 骨架、`test_architecture.py` | `uv run pytest -q` 绿 |
| **Phase 1 [必]** | utils、contracts、perception、env、runtime 及对应测试 | pytest 绿；有测试用工具调用手工走通 happy path |
| **Phase 2 [必]** | naive / rule 策略、scenarios、eval（runner / metrics / sweep / report / cli）；README（中文，含架构图、工具表、故障表、mermaid 自愈流程图）；`docs/assumptions.md` | pytest 绿；`demo --scenario stuck_once --show-truth` 输出清晰时间线；`sweep --seeds 100` 生成全部结果文件；README 附真实结果表 |
| **Phase 3 [应]** | llm_client、react_agent、prompts、`run` CLI | pytest 绿；README 增加"如何接真实 LLM"小节（给命令示例，**不要实际执行**）；另附一个 live 探针脚本——需显式设置环境变量开关才访问真实网关，默认跳过、不进 CI，与离线 pytest 分离 |
| **Phase 4 [可]** | extended 配置 + 测试；`FolderImageLibrary`（读取 `labels.json`，格式 `{"01.jpg": {"inside_count": 0, "outside_count": 0, "is_clear": true, "note": "..."}}`，按真值 (inside, outside) 匹配图片，无匹配回退合成图）；gym 风格 `ToolEnv.reset(seed) / step(call)`（为以后 RL 预留） | pytest 绿 |

`docs/assumptions.md` 至少登记（可补充）：

- A1 install 语义为"补装到 N"，不会过装；
- A2 前置不满足（含 FAULT）表现为 timeout 且不改变状态；
- A3 reset 不改变箱子与螺丝、不会失败、耗时为参数；
- A4 carry 只返回 done/timeout；SILENT_FAIL 与 LOST 的存在与概率；
- A5 箱子在工位时头部相机可拍到，不在工位时验证不可用；
- A6 巡检机器人默认 noop，若能看货架则切 `shelf_status`；
- A7 STRAY（箱外散落）机器人无法自己处理，需要人；
- A8 人类求助延迟 120 s、配额 2；
- A9 目标螺丝数 N 固定为 6，且对 agent 可见。

---

## 10. 最终汇报（用中文）

完成后输出：1）目录树；2）三条运行命令（pytest / demo / sweep）；3）pytest 结果摘要（通过数、用时、跳过项及原因）；4）真实 sweep 结果摘要表；5）已知局限；6）`docs/assumptions.md` 与 `docs/open_questions.md` 摘要。
