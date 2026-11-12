# FIX_TASK.md —— shelf_agent 第二轮：审查问题修复与加固

## 0. 背景与总原则

仓库 `shelf_agent` 已有 4 个 commit（Phase 0–4），`pytest` 247 passed。我（审查者）**实际跑过**：全量 pytest、9 个命名场景 demo、200 seed 的 sweep、两次不同 `PYTHONHASHSEED` 的 sweep 逐字节比对、行覆盖率估算、以及若干对抗性实验。结论：分层、确定性、预算、脚本化场景都做得扎实，README 的 15 行数字我用 100 seed 重跑**全部一致**；但有 1 个必须修的泄漏、1 段错误的因果解释、若干会让后续实验失真或静默出错的隐患。

**本轮是修复与加固，不是重写。** 总原则：

1. **先红后绿**：每个 [必]/[应] 条目先写会失败的测试，**把失败输出贴进最终汇报**，再改代码让它变绿。
2. 不删除、不弱化现有测试；若规格变化迫使你改某个现有测试，逐个说明原因。
3. 不扩范围：只做本文列出的条目；发现别的问题写进 `docs/open_questions.md`，不要擅自动手。
4. 每个 Phase 一次 commit（中文 message），`pytest -q` 全绿才进入下一个 Phase。
5. **README / 文档里的每个数字都必须来自实际运行**，写明命令；没有跑过的因果解释一律删掉或标成「推测」。
6. 依赖不变（pyyaml / pillow / matplotlib / pytest 系）；Windows 安全（pathlib、utf-8）；库代码无 `print` / `time.sleep`；单个 `.py` 文件 ≤ 400 行；注释用中文。
7. 不改 `TASK.md`；不接真实 LLM；不做训练。
8. 时间不够时的优先级：**A → B1 → D1 → 其余**。没做完的如实写出来，别声称完成。

标记：[必] 必须做；[应] 应该做；[可] 有余力再做。

---

## Phase A —— 观察通道泄漏 [必]

### 问题与证据

```
$ python -m shelf_agent.eval.cli demo --scenario stray_needs_human --no-color
[ 39.7s] → robot1_install_screws()
          ← done (39.7s)  # 最后一颗掉在箱外        ← agent 本不该知道
```

ReAct agent 实际看到的 tool 结果 JSON 是 `{"status":"done","message":"最后一颗掉在箱外",...}`。
规格（TASK §3.1）与 README §3 都写明「install / carry 的返回**只有** status 与耗时，不带任何状态信息」，但 `src/shelf_agent/env/sim_backend.py` 违反了（行号为大致位置）：

| 位置 | 现文案 | 问题 |
|---|---|---|
| ~L186 INCOMPLETE | `补装未达目标数` | 直接说「没装满」 |
| ~L192 STRAY | `最后一颗掉在箱外` | 直接说散落 |
| ~L218 carry 无空槽 | `货架没有空槽，箱子仍在工位` | 泄漏箱子位置 |
| ~L166 / L204 前置违规超时 | `…未就绪或箱子不在工位，技能卡住` | 与真卡住（~L181 `robot1 卡住`、~L232 `robot2 搬运超时`）文案不同，agent 可据此区分「真卡住」与「前置不满足」 |
| ~L301 inspector noop | `巡检完成（noop）` | 泄漏实现细节 |

为什么测试没拦住：`tests/test_no_leak.py` 只检查键名集合和 4 个英文 token（STATION/SHELF/LOST/FAULT），不检查中文文案；`tests/test_policies.py::test_policy_never_sees_truth` 只断言 payload 的键集合。
后果：规则策略不读 message，所以 sweep 数字不受影响；但 **ReAct/LLM 路径下「必须验证才能发现隐藏故障」这个实验前提被破坏**，demo 时间线也会把答案写在脸上。

### 要求

**A1** 物理技能（install / carry）的 `message` 只能取决于 `status`：`done → "done"`，`timeout → "timeout"`。常量集中定义在一处，所有分支（正常 / INCOMPLETE / STRAY / SILENT_FAIL / STUCK / TIMEOUT / 无空槽 / 前置违规）必须引用同一个常量。`reset → "ok"`；`inspector_run → "done"`（noop 与 shelf_status 同文案）；`verify_box` 成功 → `"ok"`；`finish → "ok"`。
保留不变：`BOX_NOT_AT_STATION` 的文案（相机能观察到的事实，A5 规定的观测通道）、`ask_human` 的回答文本（人说的话）。

**A2** 给演示留一个**只在真值通道**的注入标注：`SimBackend` 在每次 `invoke` 开头清空、结束时记录「本次调用的注入事件（kind + params）或前置违规原因」，放进 `debug_truth()["last_event"]`——永远不进 `ToolResult`。`render_timeline(show_truth=True)` 在该步的真值行里多打 `[注入] STRAY` / `[前置违规] robot1=FAULT`；`show_truth=False` 时时间线只显示 agent 可见信息（message 与 status 相同时不重复打印）。

**A3** 新增 `tests/test_observation_invariance.py`（元测试：**同一 status 下，不同隐藏状态产生逐字相同的 (status, error_code, message, data)**）。骨架（API 取自现有 `tests/test_sim_backend.py` 的写法）：

```python
def _obs(result):                      # done 的耗时带抖动，比较时去掉
    d = result.to_dict(); d.pop("duration_s"); return d

def _run(tool, script=None, pre=None):
    ep = make_episode(CONFIG, seed=0, failure_script=script, failure_rate=0.0, verifier_kind="oracle")
    if pre: pre(ep)
    return ep.backend.invoke(tool, {})

def test_install_done_is_indistinguishable():
    base = _obs(_run("robot1_install_screws"))
    for script in ({"robot1_install_screws": [("INCOMPLETE", {"progress": 3})]},
                   {"robot1_install_screws": ["STRAY"]}):
        assert _obs(_run("robot1_install_screws", script)) == base
```

覆盖矩阵（每行内所有结果 `_obs` 必须相等，timeout 行还要断言 `duration_s == timeout_s`）：
- install `done`：无事件 / INCOMPLETE(progress=3) / STRAY
- install `timeout`：STUCK / robot1=FAULT / 箱子不在工位
- carry `done`：正常 / SILENT_FAIL
- carry `timeout`：TIMEOUT(lost=True) / TIMEOUT(lost=False) / 货架无空槽（用 extended 配置或手动占槽）/ robot2=FAULT / 箱子不在工位

另加两条全局测试：
- **文案白名单**：跑 9 个命名场景 + 200 seed × 故障率 {0.1, 0.3, 0.6} × 策略 {naive, rule_noisy}，按 `(tool, status, error_code)` 收集实际出现的 message 集合，断言每组 ≤ 1 个元素（排除 `ask_human`，以及 executor 层错误码 `UNKNOWN_TOOL / INVALID_ARGS / BUDGET_EXCEEDED / MAX_STEPS / EPISODE_FINISHED`）。以后谁再加一条新文案，这个测试会变红。
- **策略不读 message**：对 rule_oracle 跑 9 个场景，把每个 `ToolResult.message` 置空后重放，调用序列必须与原来相同（防隐蔽通道）。

**A4** `test_no_leak.py` 与 `test_policies.py::test_policy_never_sees_truth` 引用新常量；**不要放宽**现有断言。
**A5** README §3 的约定段落与实现一致；`docs/assumptions.md` 新增 A27「message 文案策略」，并说明它取代 A25 里「只查 4 个英文 token」的做法。

**验收**：A3 的测试先红（贴输出）后绿；`demo --scenario stray_needs_human --no-color` 输出里 `grep -c 掉在箱外` 为 0；加 `--show-truth` 后能看到 `[注入] STRAY`。

---

## Phase B —— 随机流纯度、配置/名字校验、指标口径

### B1 [必] 随机流「命运纯度」

**问题**：README §4 与 `utils/rng.py` 声称「换策略不会改变某技能第 n 次调用的故障命运」，实测**不成立**。原因：
- `env/failures.py` ~L131 `rng.randint(0, max(0, missing-1))`，底层 `getrandbits` 的抽取次数随 `missing` 变化（拒绝采样：`randint(0,0)` 平均抽约 2 次，`randint(0,2)` 约 1.33 次）；~L128 的 `lost` 抽取又只在 TIMEOUT 时发生。同一技能流的后续位置因此依赖历史上下文。
- `perception/verifier.py` NoisyVerifier 的抽取是**条件式**的（~L104 仅当 |inside−N|==1 才抽，~L111 仅当 outside>0 才抽）；且验证器噪声用的是 `duration:verify_box` 流（`sim_backend._rng(TOOL_VERIFY)`），与耗时流同名。
- `utils/rng.py` 里的 `STREAM_*` 常量全是死代码，实际流名是 `failure:<skill>` / `duration:<tool>`。

我的复现（贴给你对照）：同一 seed、仅 `missing` 上下文不同（`[6]*n` vs `[6,3,2,5,4,1][:n]`），2000 个 seed 里 kind 序列不同的比例：

| failure_rate | 前 3 次 install | 前 6 次 install |
|---|---|---|
| 0.1 | 0.0% | 1.8% |
| 0.3 | 0.0% | 10.8% |

对当前三个默认策略影响很小，但这条不变量被写进了 README，而且之后拿 ReAct 与规则策略做配对比较时会实打实地受影响。

**要求**：
- 每次 `next_event` **无条件**从该技能流抽固定个数（建议 3 个）均匀随机数 `u_event / u_kind / u_param`：触发 = `u_event < rate`；kind 由 `u_kind` 与权重选；`progress = min(missing-1, int(u_param * missing))`（`missing<=1` 时为 0）；`lost = u_param < p_lost`。**不得再调用 `randint`**。技能没有配置时也照抽 3 个。
- `NoisyVerifier.verify` 每次调用**无条件**抽固定个数（建议 5 个）均匀随机数，分支只用它们做判定，不再按分支决定是否抽。
- 验证器用**专用流** `verifier:noise`；耗时用 `duration:<tool>`；故障用 `failure:<skill>`。`utils/rng.py` 顶部写清流命名规则，`STREAM_*` 常量要么删除，要么真的被使用。
- 新增测试：
  (a) `test_failure_fate_is_independent_of_context`：200 seed、两套不同 `missing` 上下文序列，前 50 次调用的 kind 序列逐 seed 完全相同；
  (b) `test_failure_stream_draws_fixed_count`：用计数 spy 包装 rng（`random()` 计数；`randint/uniform/choice` 直接 raise），断言每次 `next_event` 恰抽 3 个、每次 `verify` 恰抽 5 个，与真值 / kind / 上下文无关；
  (c) `test_paired_seeds_share_first_events`：同 seed 下 naive 与 rule_oracle 的第一次 install 事件 kind、第一次 carry 事件 kind 相同（仅比较两边都发生了该调用的 seed；用 A2 的 `debug_truth()["last_event"]` 取）。
- 随机流映射变了，sweep 的具体数字会略有变化——这是预期的。README 数字在 Phase D 统一重跑，**不要手改**。

### B2 [应] 配置与名字校验（fail-fast）

**问题**（全部实测过，全被静默接受）：`verifier.kind: orcle`（load 时不报，运行时才 `ValueError`）、`failures.rate: 1.5`、`failures.rate: -0.2`、`verifier.p_inside_correct: 3`、`budget.soft_limit: 0`、权重里拼错的 `STUK`（运行时按「无事件」处理，A23 → 注入被悄悄关掉，整张表会是「零故障」）。
另：`eval/runner.py` ~L61 `make_policy` 用 `name.startswith("rule")`，实测 `sweep --policies rule_noysy`（`rule_noisy` 拼错）**正常跑完、exit 0**，实际跑的是 **oracle 验证器**版本，却以错误标签入表。

**要求**：
- `config.build_config` 校验并抛带**字段路径**的 `ConfigError`：所有概率 ∈ [0,1]（`failures.rate`、`p_lost`、技能级 `rate`、`verifier.p_*`、`boundary_confusion`、`inspector.false_negative`）；`verifier.kind ∈ {oracle, noisy, scripted}`；权重 key 必须属于该技能已知故障集合（install：STUCK / INCOMPLETE / STRAY；carry：TIMEOUT / SILENT_FAIL）且（配置了 weights 的技能）权重和 > 0；`soft_limit ≥ 1`、`hard_extra ≥ 0`、`max_invalid_calls ≥ 1`、各 `rule_policy.max_* ≥ 0`。校验只放在 `build_config`（加载阶段）：运行时对未知 kind 仍按 A23 宽容，`_choose_kind` 的行为与现有测试 `test_choose_kind_with_zero_weights_yields_no_event` 保持不变。
- 策略名改用**注册表**：`POLICIES = {"naive":…, "rule_oracle":…, "rule_noisy":…, "retry_only":…}`（各自带 verifier kind），`make_policy` / `verifier_kind_for` 改查表；未知名抛 `ValueError("未知策略 'x'，可用：…")`；CLI `sweep --policies` 在开跑前校验。
- 测试：参数化坏配置表（每行断言异常信息含字段路径）；`test_unknown_policy_name_is_rejected`（含 `rule_noysy`）；CLI 对未知策略名 exit code 非 0。

### B3 [应] 指标口径：`n_unnecessary_resets`

`env/sim_backend.py` ~L280–282 对 `targets` 逐个机器人计数，导致 `reset(all)` 时只有 robot1 故障也会记 1 次「不必要」。改为**按调用**计：仅当所有目标机器人此前都是 OK 才记 1 次。测试：`reset(all)` 仅 robot1 FAULT → 0；两台都 OK → 1；`reset(robot1)` 且 OK → 1。更新 `assumptions.md` A3 的口径描述。

---

## Phase C —— 评测内容与回归测试

### C1 [应] `retry_only` 消融基线

现在唯一对照 `naive` 什么都不做，「自愈有效」把「超时重试」与「验证」两种机制混在一起了。新增策略 `retry_only`（**只看 status，不验证、不求助**）：
- install `timeout` → `reset(robot1)` → 重试；install `done` → carry；
- carry `timeout` → `reset(robot2)` → 重试 carry；carry `done` → `finish(True)`；
- 次数上限用 `rule_policy` 里已有的限额（install 调用 ≤ `max_install_calls`，每台 reset ≤ `max_resets_per_robot`，carry ≤ `max_carry_attempts`），任一耗尽 → `finish(False, 原因)`；
- **永不调用** `verify_box` / `ask_human` / `inspector_run`。

注册进 B2 的注册表；sweep 默认策略改为 `naive,retry_only,rule_oracle,rule_noisy`。测试：场景序列（`stuck_once` → install, reset, install, carry, finish；`incomplete_topup` 下 FALSE_FINISH 且 `unsafe_release=True`；`lost_box` 下耗尽后 SAFE_ABORT）；property：retry_only 的轨迹里永不出现 `verify_box`。
**预期方向**（先排查再汇报，别硬凑）：retry_only 在 STUCK / carry TIMEOUT 上优于 naive，但 INCOMPLETE / STRAY / SILENT_FAIL 仍全部变成 FALSE_FINISH，曲线应介于 naive 与 rule 之间。

### C2 [应] 验证器质量扫描 `sweep-verifier`

**动机（实测）**：README 称 rule_noisy 与 rule_oracle「逐 seed 完全一致」，这只是小样本假象。当前代码、failure_rate=0.3、默认噪声、**5000 seed**：rule_noisy `FALSE_FINISH = 17`（0.34%）、`unsafe_release = 0.0034`，rule_oracle 为 0（100 seed 里期望只有 0.34 次，所以 0 次并不意外）。把噪声调高（300 seed、failure_rate=0.3，`boundary_confusion = p_outside_miss = level`）：level 0.5 → rule_noisy 9 次 FALSE_FINISH，level 1.0 → 27 次，rule_oracle 始终 0。B1 之后具体数字会变，**方向必须一致**（rule_noisy 的 FALSE_FINISH 随 level 单调不降，rule_oracle 恒为 0）。

**要求**：新 CLI 子命令
```
sweep-verifier --config configs/real_screw.yaml --failure-rate 0.3 \
    --levels 0,0.1,0.25,0.5,1.0 --seeds 200 \
    --policies rule_oracle,rule_noisy,retry_only --out results/verifier/
```
每个 level 把 `verifier.boundary_confusion` 与 `verifier.p_outside_miss` 同时设为 level（`p_inside_correct`、`p_unclear` 保持配置值），产出 `verifier_sweep.csv / .md / .png`（FALSE_FINISH 率与 `unsafe_release` 率 vs level，带 Wilson 区间；retry_only 画成参考线）。复用 `run_sweep`，不要复制逻辑；`config_hash` 要体现 level。
注意：**level=0 不等于无噪声**——`p_inside_correct=0.95` 仍会产生 ±1 的计数噪声（truth=N−1 时有约 2.5% 概率被报成 N），所以 level=0 时 rule_noisy 仍可能有极个别 FALSE_FINISH；README 里要把 level=0 描述成「只剩 ±1 计数噪声」，不要说成无噪声。
测试：20 seed 的 smoke 测试；level=1.0 时 rule_noisy 的 FALSE_FINISH 数 ≥ level=0 时（固定 seed 集合，200 seed 足够稳定）；rule_oracle 在所有 level 下 FALSE_FINISH 恒为 0。

### C3 [应] 作图

`success_vs_failure_rate.png` 里 rule_noisy 被 rule_oracle 完全盖住（图例 3 项只看到 2 条线）。给每个策略的 x 加固定小偏移（dodge，±0.004 量级），误差棒不重叠；颜色表加 `retry_only`；右图用 policy 颜色区分策略、线型区分指标（FALSE_FINISH 实线 / unsafe_release 虚线）。`sweep-verifier` 的图同样处理。

### C4 [应] 跨进程可复现回归测试

我手动验证过：`PYTHONHASHSEED=1` 与 `12345` 下 sweep 的 `episodes.jsonl / summary.csv / summary.md` sha256 逐字节一致（很好）；但仓库里只有同进程的 `test_same_seed_produces_identical_episodes_jsonl`。新增 `tests/test_reproducibility_subprocess.py`：用 `subprocess`（`sys.executable -m shelf_agent.eval.cli …`，`PYTHONPATH` 指向当前导入的 `shelf_agent` 所在 src 目录）在 `PYTHONHASHSEED=1` 与 `2` 下各跑一次小 sweep（seeds=10、rates=0,0.2、policies=naive,rule_noisy），比较 `episodes.jsonl` 与 `summary.csv` 的 sha256；整个测试 < 10 s。

---

## Phase D —— 文档重写与清理

### D1 [必] README / docs（所有数字用 Phase A–C 之后的**真实运行**重新生成）

- **§7 表格与配对比较**：用最终代码重跑 `--seeds 100`，4 个策略（naive / retry_only / rule_oracle / rule_noisy），写明命令、seed 数、git commit。
- **删除并重写 §7「怎么读这张表」第 3 条**。现文「验证器噪声几乎没有影响……原因：策略每次搬运前都重新验证，单次误报被下一次验证挡住」是**错的**：策略在搬运前**只验证一次**；一次「误报完成」会直接放行，搬运后箱子离开工位，没有任何环节再看箱内（POST_CHECK 只会拿到 `BOX_NOT_AT_STATION`）。默认噪声下 rule_noisy ≈ rule_oracle，只是因为默认参数很少产生「把不完整的箱子报成完整」（约 0.3% / episode，见 C2 的 5000 seed 数字）。改成：「默认噪声下 100 seed 分不出两者；验证器质量的影响见 §7.2」，并在 §7.2 贴 `sweep-verifier` 的真实结果与图（若 C2 没做完，就贴 5000 seed 单点对比的真实命令与数字，并注明「完整扫描未做」）。
- §7 末尾 ⚠️ 的「换一组权重……相对优势不会消失」**没有任何运行支撑**：要么补一组敏感性运行（至少换 2 组故障权重，各 100 seed）并贴数，要么删掉这句。
- §3「只有 status 与耗时」与 §4「命运纯度」两处声明：与 Phase A、B1 的实现一致，并指向对应测试名。
- §5 流程图与说明与实际代码一致（见 D3）。
- 新增「组会演示」小节：3 条命令（`stuck_once` / `carry_silent_fail_detected` / `verifier_false_positive`，均带 `--show-truth`）+ 2 张图（sweep 与 sweep-verifier），各一句话说明「这张图想说明什么」。
- docs：新增 A27（message 策略）、A28（随机流抽取规则）、A29（`n_unnecessary_resets` 口径）、A30（`retry_only` 定义）；`open_questions.md` 里「你这台机器」改成中性措辞。

### D2 [应] 小清理（单独一个 commit，不改行为）

- 未使用的 import：`agents/naive.py`、`agents/rule_selfheal.py` 的 `Policy`；`agents/react_agent.py`、`env/world.py` 的 `Mapping`；`runtime/executor.py` 的 `Any`（用 pyflakes 或 ruff；装不了就手查）。
- `eval/runner.py` ~L199–201 两个相同的 `return "MAX_STEPS"`，合并。
- `agents/react_agent.py` ~L79 写死「连续两次」，改用 `self.max_no_tool_call`；`agents/prompts.py` ~L24 注释「四条硬规则」实为 5 条。
- **LLM 看到的文本**：去掉 tool 描述里自动追加的「（实验室名：…）」（实验室名只用于人读的映射）；YAML 每个工具增加 `description_en`（英文，用于系统提示与 tools schema，缺省回退中文 `description`），`real_screw.yaml` / `extended.yaml` 全部补齐。现状是英文系统提示里混着中文工具描述。测试：系统提示与 tools schema 里不含中文字符。
- `inspector.mode == shelf_status` 时，ReAct 暴露 `inspector_run`（现在 `DEFAULT_EXPOSED_TOOLS` 不含它，规则策略能用、ReAct 用不了），系统提示补一句用法；测试覆盖。

### D3 [可] `TRANSITIONS` 表名实相符

`agents/rule_selfheal.py` 的 `TRANSITIONS` 共 36 个条目；我用探针（9 个场景 + 450 个随机 episode）统计，只有 9 个被查表命中，其余 27 个要么被 `_handle`（~L171–224）里的特例先处理，要么当前配置下不可达（`reset_error`、`human_error` 等）。README §5 的流程图读起来像表驱动，实际是「特例 + 表」。二选一：(a) 删除被遮蔽的条目，表改名 `FALLBACK_TRANSITIONS` 并写清注释；(b) 把限额守卫上移成表里的 guard，让表真正成为唯一来源。
**行为必须不变**：9 个场景序列、以及固定 seed 的 sweep 输出 sha256 前后一致（**重构前先记录**）。做不到就放弃，并把原因写进 `open_questions.md`，不要硬改。**注意顺序**：D3 如果做，要在 B1 之后、README 重跑数字之前做完。

---

## 交付与汇报

**Definition of Done（逐条跑并把输出贴进汇报）**：
```
python -m pytest -q
python -m shelf_agent.eval.cli demo --scenario stray_needs_human --no-color | grep -c 掉在箱外      # 必须是 0
python -m shelf_agent.eval.cli demo --scenario stray_needs_human --show-truth --no-color | grep 注入 # 必须有输出
PYTHONHASHSEED=1 与 =2 各跑一次 sweep（seeds=20），sha256sum 对比 episodes.jsonl / summary.csv   # 必须相同
python -m shelf_agent.eval.cli sweep --policies rule_noysy ...                                      # 必须报错退出
```

**最终汇报格式**：逐条（A1…D3）写「完成 / 未完成 / 偏离」；新增测试名；A3 与 B1 的「红」输出片段；`pytest -q` 结果；`sweep` 与 `sweep-verifier` 的真实命令与关键输出；README 里每个数字对应哪条命令；任何没做完或没把握的地方原样写出来，不要美化。
