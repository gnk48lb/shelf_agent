# 默认假设清单

本文件登记 TASK.md 未定义、按默认假设实现的细节。每条注明影响面与确认状态。

- **[必]** / **[应]** / **[可]** 沿用 TASK.md §1 的标记含义。
- 「状态」列：`待师兄确认` = 依赖真机事实或师兄口径；`项目内自决` = 纯实现选择，师兄点头即可，不影响实验结论。
- 实现时若某条假设被推翻，改这里、改代码、改测试三处同步；不要只改代码。

---

## 一、真机相关（TASK.md §9 指定必须登记的 A1–A9）

| 编号 | 假设 | 理由与影响 | 状态 |
|---|---|---|---|
| A1 | `robot1_install_screws` 语义为「补装到目标数 N」，不会过装 | 决定 `missing = N − inside` 的算法与 STRAY/INCOMPLETE 的进度取值。真机若一次装一颗，需改成累加语义。 | 待师兄确认 |
| A2 | 前置不满足（含机器人处于 FAULT）表现为「卡住但不改变状态」：耗时 `timeout_s`、返回 `timeout`、状态不变 | 决定「必须先 reset 才能继续」这条恢复动态是否成立。若真机前置不满足会立刻报错，FAULT 语义与自愈流程都要重写。 | 待师兄确认 |
| A3 | `reset` 不改变箱子与螺丝、不会失败、耗时取配置参数 | 决定「reset 后要重新验证」的必要性，以及 `unnecessary_reset` 的计数口径。 | 待师兄确认 |
| A4 | `carry` 只返回 `done` / `timeout`；`SILENT_FAIL`（返回 done 但箱子没动）与 `LOST`（箱子丢失）存在，概率由 `p_lost` 控制 | 这两类是「无观察通道的失败」，是 POST_CHECK 与 HUMAN 分支存在的理由。若真机搬运从不静默失败，`carry_silent_fail_detected` 场景需降级为 [可]。 | 待师兄确认 |
| A5 | 箱子在工位时头部相机可拍到；箱子不在工位时验证不可用（`BOX_NOT_AT_STATION`） | 决定 `verify_box` 的前置检查与 `lost_box_needs_human` 场景的判定路径。 | 待师兄确认 |
| A6 | 巡检机器人默认 `noop`；若师兄确认它能看货架，则切 `shelf_status` 模式 | 只影响 `inspector_run` 是否有实际作用，默认不影响主流程。 | 待师兄确认 |
| A7 | `STRAY`（螺丝散落箱外）机器人自己无法处理，需要人 | 决定 `ask_human` 的核心用途之一，以及「散落必须求助」这条规则。 | 待师兄确认 |
| A8 | 人类求助延迟 120 s、配额 2（`max_asks`） | 决定预算与耗时的量级。延迟若更长，`budget.soft_limit` 需相应放宽。 | 待师兄确认 |
| A9 | 目标螺丝数 N 固定为 6，且对 agent 可见 | 决定 `is_complete` 的判据与配置项形态。真机若 N 不固定，需把 N 改为 agent 可见参数（`extended.yaml` 里随 `box_id` 一起给）。 | 待师兄确认 |

---

## 二、TASK.md 规格留白处（本项目补充假设）

### 影响实验结论，需师兄确认

| 编号 | 假设 | 理由与影响 | 状态 |
|---|---|---|---|
| A10 | §3.7「默认只暴露 `verify_box`，不暴露 `robot1_get_head_image`」读作：**默认暴露集合里排除 `robot1_get_head_image`**，而非「只给 `verify_box` 一个工具」 | 若按后者理解，ReAct agent 无法 install / carry / reset / ask_human / finish，episode 无法进行。默认暴露集定为：`robot1_install_screws`、`verify_box`、`robot2_carry_box`、`reset`、`ask_human`、`finish`；`robot1_get_head_image` 留给后续 VLM 验证器。 | 待师兄确认 |
| A11 | `MAX_STEPS` 的具体上限暂取 `budget.soft_limit + budget.hard_extra`（即 40 + 3 = 43 步） | §5 的 outcome 表列了 `MAX_STEPS`，但全文未定义步数上限。若上限另有来源，以真实配置为准。 | 待师兄确认 |
| A12 | `budget.soft_limit` 的「总调用数」包含**所有**进入 executor 的调用（含被拒的 invalid 调用、verify、ask_human、finish） | 若不计入被拒调用，预算就只是个软提示，硬停失去意义。直接决定 `ABORTED_BUDGET` 的出现时机。 | 待师兄确认 |
| A13 | `config_hash` = 规范化配置的 sha256，范围包含：配置文件全文 + 所有运行时覆盖项（`failure_rates`、`policy`、扫描参数）+ 契约版本号；**不含** `seed` 与 `episode_id` | 决定「哪些结果可以声称同一配置」。seed 单列在轨迹里，便于按 seed 配对比较而不串哈希。 | 项目内自决 |
| A14 | `test_no_leak.py` 的「真值字段」清单显式列出，禁止出现的键至少包含：`inside`、`outside`、`location`、`robot_state`、`fault`、`truth`、`meta`、`truth_snapshot`、`box_location`、`missing`；同时做取值检查（真值整数、`location ∈ {STATION, SHELF, LOST}`） | §6 要求「键名与取值双重检查」，但不给出清单。只查键名会漏掉「键名合法但值是真值」的泄漏。 | 项目内自决 |

### 影响实现细节，知悉即可

| 编号 | 假设 | 理由与影响 | 状态 |
|---|---|---|---|
| A15 | 脚本化注入（`FailureModel.scripted`）优先于 `failures.rate`：设了脚本时按脚本逐次消费，**耗尽后该技能恢复正常**（不再注入任何事件）；需要「脚本用完后回到 rate 随机注入」时，显式传入 `fallback` 模型即可 | §3.3 只说「耗尽后恢复正常」。本条按「恢复**正常**」的字面口径实现（脚本耗尽 → 无事件），这样命名场景的结果完全确定、可复现；`fallback` 参数保留了另一种口径的入口。 | 项目内自决 |
| A16 | `carry` 的 `SILENT_FAIL` 不把 robot2 置为 FAULT（只有 `TIMEOUT` 才置 FAULT） | §3.2 只在 TIMEOUT 分支写了置 FAULT，隐含 SILENT_FAIL 时机器人状态不变——机器人「以为搬成功了」。 | 项目内自决 |
| A17 | 真实 LLM 目标全部走环境变量（`SHELF_AGENT_LLM_BASE_URL` / `SHELF_AGENT_LLM_MODEL` / `SHELF_AGENT_LLM_API_KEY`），**默认留空**；未设置时 `run --policy react` 直接报错并提示该设哪几个变量，不尝试任何默认地址 | §3.7 要求不得硬编码服务地址、不得在日志或轨迹中出现 key。留空报错比写死一个本地地址更符合这条约束，也避免有人误以为项目自带后端。 | 项目内自决 |
| A18 | `NoisyVerifier` 允许报出 `inside > N`（真值为 N 时按 `k±1` 取到 N+1），不额外裁剪；`is_complete` 要求 `inside == N` 精确相等，故 N+1 不算完成 | §3.6 的 VERIFY 分支专门处理「报告自相矛盾（inside > N）→ 重拍」，说明这个情形是预期内的。 | 项目内自决 |

---

## 三、本期实现新增的假设（A19 起，待师兄确认）

以下几条是写代码时遇到、但 TASK.md 与前 18 条都没定口径的地方。按 §1 的要求登记，
**均为「按默认假设实现，待师兄确认」**。

| 编号 | 假设 | 理由与影响 | 状态 |
|---|---|---|---|
| A19 | `unsafe_release` 只在**箱子真正放上货架**时记录；搬运静默失败（箱子没动）不记 | §3.2 说「搬运时记一次」，但「搬运」既可指尝试也可指放行。取「放行」口径：它对应的是「不完整的箱子被放上了货架」这个物理事实，静默失败时箱子根本没离开工位。若改成尝试口径，`unsafe_release` 会与 `FALSE_FINISH` 高度重合，指标失去区分度。 | 待师兄确认 |
| A20 | 搬运超时后的复检（`CARRY_CHECK`）与搬运后的复检（`POST_CHECK`）若拿到**不完整**的报告，不直接回 `CARRY`，而是回 `VERIFY` 重新判断 | §3.6 说「返回报告 → 回 CARRY 重试」，但同章又规定「验证器未报告 complete 时不得调用 carry」。两条在噪声验证器下会打架。取后者（更严的那条）：只有报告 complete 才允许搬运，否则回 `VERIFY` 走补装 / 求助分支。 | 待师兄确认 |
| A21 | 巡检机器人（`shelf_status` 模式）确认「箱子在架上」才算完工；它报 `box_on_shelf=False` 时策略会求助人类，人类若也帮不上（箱子确实在架上，只是巡检漏报），策略诚实 `finish(False)` | 此时真值其实是成功的，于是 outcome 记为 **MISSED_SUCCESS** 而不是 SAFE_ABORT——这正好体现「宁可失败也不谎报」的代价。若师兄确认巡检可靠（`false_negative=0`），这条路径不会出现。 | 待师兄确认 |
| A22 | 货架没有空槽时，搬运按失败处理（`timeout`、`robot2` 进入 FAULT、箱子留在工位） | §3.2 只说「按多箱多槽设计」，没说槽位占满怎么办。`real_screw` 是 1 箱 1 槽，触发不到；`extended.yaml`（2 箱 2 槽）会用到。 | 待师兄确认 |
| A23 | 未知的故障类型按「无事件」处理（正常完成，不改变语义） | 防止配置写错时仿真崩掉；真实故障类型只有 STUCK / INCOMPLETE / STRAY / TIMEOUT / SILENT_FAIL 五种。 | 项目内自决 |
| A24 | `Status` / `BoxLocation` / `RobotState` 三个枚举放在 `utils/enums.py`（L0），而不是 `contracts/types.py`（L1） | §4.3 规定 contracts 与 perception 同层互不 import，但 `perception/completion.py` 的真值判据需要位置词汇。放到 L0 是唯一能同时满足两条约束的位置。 | 项目内自决 |
| A25 | 验证**报告**里的 `inside` / `outside` 是感知输出，不是真值；防泄漏测试按「契约是否声明了该键」区分二者 | A14 列的禁止键里有 `inside` / `outside`，但它们同时是 `verify_box` 契约声明的合法输出。测试规则定为：`data` 的键必须在该工具的 `returns_data` 里（否则算泄漏），`data` 之外的任何位置出现真值键才算泄漏；取值侧检查 `STATION` / `SHELF` / `LOST` / `FAULT` 四个真值 token。 | 项目内自决 |
| A26 | `reset(robot=all)` 的耗时取配置 `reset_durations.all`；该键缺失时取各目标机器人 reset 时长的最大值 | §3.2 说「耗时取各目标 reset 时长的最大值」，但 YAML 里单独给了 `all` 键。两者在当前配置下同为 30 s。 | 项目内自决 |

---

## 四、待办

- [ ] 把 A1–A9 整理成选择题发给师兄（沿用旧项目「给选择题而不是填空题」的做法）。
- [ ] A10–A12 优先确认：它们直接决定自愈流程和预算口径，改动成本最高。
- [ ] 每条假设被确认或推翻后，同步更新本文件、`docs/open_questions.md`、对应测试与 `configs/*.yaml` 的注释。
