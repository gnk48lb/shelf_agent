# 开放问题与候选扩展

本文件登记两类条目，**都不在 TASK.md 本期范围内，不擅自实现**：

1. **需要外部信息才能定**——依赖师兄答复或真机实测，猜不出来。
2. **候选扩展**——我认为对项目（尤其是论文）有价值，但 TASK.md §8 明确「不做」，写在这里备案，等批准再动。

编号 `Q1…`，与 `assumptions.md` 的 `A1…` 配套：`A` 是「已经按默认假设实现了」，`Q` 是「还没法决定」。每条若被拍板，同步更新 `assumptions.md`、对应测试与 `configs/*.yaml`。

---

## 一、需要外部信息才能定

### Q1 真机各技能的实测成功率与常见失败方式 → 校准故障注入参数

- **背景**：`configs/real_screw.yaml` 里 `failures.rate = 0.15`、install 故障权重 `STUCK 0.4 / INCOMPLETE 0.4 / STRAY 0.2`、carry `TIMEOUT 0.5 / SILENT_FAIL 0.5`、`p_lost = 0.3`——**全部是拍的**。真机那边目前没有统计。
- **为什么重要**：评审一定会问「仿真真实吗」。参数没有来源就只能靠 sweep 的敏感性分析来辩护。更要命的是：如果 STUCK 实际占比远高于 0.4，本文的自愈流程重心就偏了。
- **要向师兄要什么**：每个技能的历史成功率 + 常见失败方式的大致比例（凭印象也行，先有个量级）。
- **若拿到**：改 YAML 里的 `failures` 段即可，代码不用动；README 的结果表要注明参数来源是「真机统计」而非「默认值」。
- **状态**：待师兄答复（可与 A1–A9 的选择题一起发）。

### Q2 「装好了」的判定口径

- **背景**：TASK.md 用的是精确判据 `inside == N and outside == 0`（§3.4），这比「满/没满」清楚。但已有的 20 张图标签里口径是另一套：`status ∈ {empty, partial, full}`，而 `15.jpg` 备注写「半满」标签却是 `full`，`11.jpg` 场景名写「达标正例」真值却是 5（N=6，按精确判据它是欠装）。
- **为什么重要**：`full` 这个标签既可以指「装满了容量」也可以指「达到目标数 N」，两种口径在 N−1 这个最难的地方结论相反——而 N−1 恰好是假放行（FALSE_FINISH）的主要来源。
- **要向师兄要什么**：拿 3–5 张截图给他做选择题（「这几张算装好了吗」），他的判断就是口径。
- **若口径与本项目一致**：在 `docs/assumptions.md` 补一条说明，并在 README 写清 `full` 与 `inside == N` 的关系。
- **状态**：待师兄答复。

### Q3 巡检机器人能否看货架

- **背景**：师兄的原话是「不需要管，只要调用即可」，所以 §3.2 规则 6 把 `inspector_run` 默认设为 `noop`，`shelf_status` 模式只是预留。
- **为什么重要**：`carry` 的 `SILENT_FAIL`（返回 done 但箱子没到货架）目前**没有观察通道**——只能靠「再拍一次头部相机，看箱子还在不在工位」这种间接办法。若巡检机器人真能看货架，`inspector_run` 就是唯一直接通道，POST_CHECK 的判定逻辑会不一样，论文里也能报一条更干净的证据。
- **要向师兄要什么**：一个是/否问题——「能不能让它去看货架？返回什么？」
- **若答案是能**：把 `inspector_run` 从占位升级为正式路径，`shelf_status` 模式的 `false_negative` 概率可配置；新增一个场景 `carry_silent_fail_detected_by_inspector`。
- **状态**：待师兄答复。**但路径已备好**：`inspector.mode: shelf_status` 已实现（`configs/*.yaml` 的 `inspector.mode` + `false_negative`），规则策略在 POST_CHECK 之后会调 `inspector_run` 确认；它报 `box_on_shelf=False` 时策略会求助人类，人类也无能为力则诚实 `finish(False)`——此时真值其实成功，outcome 记为 **MISSED_SUCCESS**（口径见 `assumptions.md` A21）。师兄只要回一个「能 / 不能」，把 `inspector.mode` 一改即可，无需改代码。

### Q4 真机照片的采集与标注规范

- **背景**：Phase 4 的 `FolderImageLibrary` 约定读 `labels.json`，格式 `{"01.jpg": {"inside_count": 0, "outside_count": 0, "is_clear": true, "note": "..."}}`。而现在手头那 20 张 Gemini 生成的图，标签是 `{"actual_count": 5, "status": "partial", "note": "六颗随机散落"}`——**字段名和语义都不一样**（`actual_count` vs `inside_count`；`status` 三值 vs `is_clear` 布尔）。
- **为什么重要**：这是本期实现里唯一一处「上游产物格式与本项目约定不一致」的地方。如果按 `labels.json` 的约定写实现，等真机照片到位时还要再转一次；而且 `is_clear`（能否看清）和 `status`（装了多少）是**两个正交维度**，合成一个字段会丢掉「看不清」这个信号——而「看不清时宁可重拍」是拒识机制的核心。
- **若要动**：建议真机照片按 `{"inside_count", "outside_count", "is_clear", "note"}` 标注；历史那 20 张 Gemini 图另存一份、不混进同一个图库（它们不是真实光照）。
- **状态**：**Phase 4 已按本文件的 `labels.json` 约定实现** `FolderImageLibrary`（`perception/imagelib.py`）：读 `labels.json`，按真值 `(inside, outside)` 匹配图片，匹配不上回退合成图。仍未解决的是「历史那 20 张 Gemini 图的标签格式（`actual_count` / `status` 三值）与本约定不一致」——接入真机图库前需一次性转换，且按上面的建议另存一库、不与真机图混在一起。真机照片到位前，本期仍只用合成图。

### Q5 师兄的机器上用什么 LLM 端点

- **背景**：§3.7 写的目标「Gemini 的 OpenAI 兼容端点或本地 vLLM」是**可移植**的；而本机 Magpie（`http://127.0.0.1:3425`）是**只有你这台机器有**的个人网关，实验室其他人没有，Linux 机器上也未必可达（WSL 里要靠 mirrored networking 才通）。
- **为什么重要**：`run --policy react` 如果只能在你机器上跑，师兄就复现不了。
- **建议做法**：README 把 Magpie 写成「本机便利选项」，把 Gemini / vLLM 写成默认示例；环境变量默认留空（见 `assumptions.md` A17），任何人填上自己的端点即可。
- **状态**：项目内已按此处理；等 README 阶段定稿。

---

## 二、发现的规格不一致（需要有人拍板）

### Q6 `NoisyVerifier` 的默认参数目前没有实测来源

- **背景**：§3.4 给了 `p_inside_correct=0.95 / boundary_confusion=0.10 / p_unclear=0.03 / p_outside_miss=0.05`，说是用来模拟真 VLM 验证器的噪声通道。但现有实测是：Gemini 3.5 Flash Lite 直接计数 39/45（86.7%），3.6 是 43/44（97.7%）——**量级对不上 0.95**，而且那批图是 Gemini 生成的，真值本身有已知错误（13.jpg 图里真有 11 颗，标签写 7）。
- **为什么重要**：这个验证器是论文 E2（验证错误→端到端后果）的核心噪声源。参数拍脑袋 vs 参数有出处，直接影响结论的可辩护性。
- **可选做法**：(a) 保持默认，README 注明「未经真实数据校准」；(b) 用真机照片重测混淆统计后回填；(c) 在 sweep 里把验证器质量本身当自变量扫一遍（`success_vs_failure_rate.png` 旁边加一张「成功率 vs 误放行率 vs 验证器质量」）。
- **注意**：§8 明确本期不做训练，但**测混淆统计不需要训练**，只需要推理，成本很低。
- **状态**：待定，见 README 阶段的取舍。

### Q7 「满」在两处口径不一致

- **背景**：见 Q2。代码侧统一用 `inside == N and outside == 0`（`perception/completion.py` 里唯一一处判据），数据侧的 `status: full` 是另一个口径。
- **影响面**：只影响将来接真图库时的标签转换，不影响本期实现。本期所有验证器都基于真值数字，不读 `status`。
- **状态**：本期不做，仅登记。

---

## 三、候选扩展（有价值，但 TASK.md 本期明确不做）

按 §1.4 的要求登记在此，**不擅自实现**。每条标注「若要做，最小改动是什么」。

### Q8 验证器的拒识（rejection）接口 —— 论文主线的关键

- **动机**：实测已证明两点——模型自报置信度没用（对的 1.0，错的 0.975–0.988），而证据模糊时 VLM 系统性偏向判「成功」。这正是「宁可重拍，不可误放行」的依据。而 FailBench 在 14 个公开来源的 2197 次操作尝试上测了 13 个检测器，最强平衡准确率也只有 0.77；**误放行的代价远高于多拍一张**。
- **最小改动**：`perception/completion.py` 的 `Report` 从 `{inside, outside, clear}` 扩成带 `can_tell: bool`（或 `confidence: float`）；`NoisyVerifier` 增加 `p_unclear` 之外的拒识通道；`RuleSelfHealPolicy` 的 VERIFY 分支已有「not clear → 重拍」逻辑，扩个字段就够。
- **对论文的价值**：拒识 + 风险-覆盖曲线，是比「我搭了个 agent 框架」站得住得多的贡献点。
- **本期状态**：不做。§3.4 只要求留好接口。

### Q9 LLM 侧 logprobs（yes-token 概率）

- **动机**：本地部署的一个技术性理由——直接取「yes」token 的概率当完成度信号，而不是解析模型自报的置信度（已证明没用）。
- **最小改动**：`agents/llm_client.py` 的 `LLMResponse` 增加可选 `logprobs` 字段；`OpenAICompatClient` 透传 `logprobs` / `top_logprobs` 参数；验证器侧新增一个 `TokenProbVerifier`。
- **注意**：需要后端支持（vLLM 返回 logprobs 的方式以对应版本官方文档为准；Magpie 当前未验证是否透传）。
- **本期状态**：不做，接口留好。

### Q10 真机 `RobotBackend`

- **动机**：`SkillBackend` 接口的设计目的就是「仿真和真机实现同一套接口，换后端时 agent 一行不改」。
- **最小改动**：新建 `env/robot_backend.py`，实现与 `SimBackend` 相同的调用签名，把 `robot1.install_screws` 等映射到真机 HTTP / MCP 调用。
- **前置**：Q1 的真机接口规格（师兄那边「技能列表、参数、返回值、失败类型」目前仍未给出）。
- **本期状态**：§8「真机接入」明确不做。

### Q11 真实的 `ask_human` 通道

- **动机**：仿真里用 `ScriptedHuman` 顶替；真机演示时希望有人真的能收到求助。
- **最小改动**：`env/human.py` 增加一个 `RemoteHuman`（沿用旧项目 HITL 的挂起/恢复思路），把 `ask_human` 推到 QQ / 邮件 / IM。
- **注意**：延迟与配额会与 §3.4 的 120 s / 2 次假设冲突，真实通道的人反应时间不可控，评测时要单独说明。
- **本期状态**：§3.4 只要求 `ScriptedHuman`，不做。

### Q12 急停 / 操作员侧 reset

- **动机**：真机技能阻塞且不可中断，客户端超时只是不再等，机械臂可能还在动。**安全相关的动作不能依赖模型决定**——操作员需要一个不经过 LLM 的 `/reset` 入口。
- **最小改动**：`ToolExecutor` 旁挂一个进程外的控制通道（信号 / 文件哨兵 / 单独的 HTTP 端点），只允许 `reset` 与 `finish`。
- **对仿真**：可作为 Phase 4 的 `ToolEnv` 扩展，或纯真机侧的事，本期不必实现。
- **本期状态**：§3.1 的 `reset` 是 agent 可调的工具；急停不在规格内。

### Q13 多箱调度

- **动机**：曾想用多箱流水线制造复杂度。**但这条建议被明确否掉了**——多箱调度是经典规划与调度的强项，加进来更可能让规则脚本或规划器赢，审稿人会问「为什么不用规划器」。
- **现状**：TASK.md 的 `configs/extended.yaml`（2 箱 2 槽）是 `[可]` 级别，与这条不冲突——它测的是「槽位被占时换槽」，不是流水线调度。
- **本期状态**：不做；若要加深复杂度，应该加**依赖感知与不确定性**的情形（技能返回与视觉互相矛盾、规则里没有的新失败），而不是加箱数。

---

## 四、提醒：本期不做但论文会需要的

- **SFT / RL**：§8 明确不做（训练）。但 §3.7 已经要求 ReAct 的完整 messages 以 OpenAI chat 格式进轨迹 JSONL——**轨迹就是将来的 SFT 数据**，所以 `truth_snapshot` 必须严格剥离（§3.8 有此约束，`test_no_leak.py` 要守住）。
- **VLM 验证器**：§3.4 明确本期不实现，但它是论文主线（见 Q8）。本期只需保证 `Verifier` 协议留好接口、`ScriptedVerifier` 与 `NoisyVerifier` 行为一致，将来换实现不影响 agent。
- **真机照片**：Q4 的采集规范最好从第一天就定，否则后面补拍、补标注的成本很高。

---

## 待办

- [ ] 把 Q1、Q2、Q3 连同 `assumptions.md` 的 A1–A9 一起整理成选择题发给师兄（一次问完，别分批）。
- [ ] Q6 在 README 阶段决定取舍：保持默认 / 重测回填 / 把验证器质量当自变量扫。
- [ ] Q8、Q9 若确定要做，先在本文件里写清楚最小改动范围，再动手。
