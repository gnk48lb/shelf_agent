# FIX_TASK 第二轮收尾记录

> 这一轮（FIX_TASK.md 的 A/B/C/D）已全部实现并验证。**最终状态：334 passed / 8.30 s，
> 覆盖率 TOTAL 91%**，DoD 四条全过。
> 本文记录「改了什么、怎么验证的、发现过哪些真问题」——数字全部来自真实运行，
> 产物在 `results/`（已被 .gitignore 忽略）。

## 一、逐条状态

| 条目 | 状态 | 落点 |
|---|---|---|
| **A1** message 只取决于 status | ✅ | `env/sim_backend.py`：`MSG_DONE` / `MSG_TIMEOUT` / `MSG_OK` / `MSG_BOX_NOT_AT_STATION` / `ALLOWED_MESSAGES` |
| **A2** 真值通道注入标注 | ✅ | `SimBackend.debug_truth()["last_event"]`；`eval/report.py` 渲染 `[注入] STRAY` / `[前置违规] robot1=FAULT` |
| **A3** 观测不变性元测试 | ✅ | `tests/test_observation_invariance.py`（逐字比较 + 文案白名单 + 「策略不读 message」重放） |
| **A4** 现有测试引用新常量 | ✅ | `tests/test_no_leak.py`（新增 `test_policy_only_sees_allowed_messages`）、`tests/test_policies.py` |
| **A5** README §3 + A27 | ✅ | `README.md` §3；`docs/assumptions.md` A27（声明取代 A25 的旧做法） |
| **B1** 随机流命运纯度 | ✅ | 故障模型每次无条件抽 3 个、验证器每次无条件抽 5 个；专用流 `verifier:noise`；`utils/rng.py` 写清命名规则、删掉死常量。`tests/test_random_stream_purity.py` |
| **B2** 配置 fail-fast + 策略注册表 | ✅ | `config.build_config` 校验概率 / kind / 权重键 / 限额；`eval/runner.py` 的 `POLICIES`；CLI 先校验策略名。`tests/test_config_validation.py` |
| **B3** `n_unnecessary_resets` 按调用计 | ✅ | `env/sim_backend.py::_reset`；`tests/test_sim_backend.py` 三条断言 |
| **C1** `retry_only` 消融基线 | ✅ | `src/shelf_agent/agents/retry_only.py` + `tests/test_retry_only.py` |
| **C2** `sweep-verifier` | ✅ | `eval/sweep.py::run_verifier_sweep` + CLI 子命令 + `eval/report.py::plot_false_finish_vs_level`；`tests/test_verifier_sweep.py` |
| **C3** 出图 dodge | ✅ | 每策略固定 x 偏移 ±0.004；右图按策略配色、实线 FALSE_FINISH / 虚线 unsafe_release |
| **C4** 跨进程可复现 | ✅ | `tests/test_reproducibility_subprocess.py`（PYTHONHASHSEED=1/2 比 sha256） |
| **D1** README / docs | ✅ | §7 三张表用最终代码重跑生成；新增「组会演示」小节；A27–A30、Q14 |
| **D2** 小清理 | ✅ | 无用 import、`MAX_STEPS` 合并、「连续两次」改用变量、LLM 侧去掉实验室名并改用 YAML 的 `description_en`、`shelf_status` 时 ReAct 暴露 `inspector_run` |
| **D3** [可] 转移表名实相符 | 🟡 **部分** | 表改名 `FALLBACK_TRANSITIONS` + 注释；**没有**把限额守卫搬进表里（理由见 `docs/open_questions.md` Q14） |

## 二、验证（全部来自真实运行）

```bash
uv run pytest -q -o addopts=""      # 334 passed in 8.30s
uv run pytest -q --cov=shelf_agent --cov-report=term   # TOTAL 91%
```

DoD 四条：

| 命令 | 结果 |
|---|---|
| `demo --scenario stray_needs_human --no-color \| grep -c 掉在箱外` | **0** ✅ |
| `demo --scenario stray_needs_human --show-truth --no-color \| grep 注入` | 有输出：`[真值] … \| [注入] STRAY` ✅ |
| `PYTHONHASHSEED=1/2` 各跑一次 sweep，比 sha256 | episodes.jsonl / summary.csv **完全相同** ✅ |
| `sweep --policies rule_noysy` | exit **2** + 「未知策略 ['rule_noysy']，可用：…」✅ |

**红证据**（旧代码 HEAD 上跑新测试）：`results/_checks2.txt` 里有完整的失败输出
——用 `PYTHONPATH=<worktree>/src` 才能让 worktree 的旧代码真正被 import 到
（项目是 editable 安装，否则测的还是主工作区）。

## 三、真实结果

**故障率扫描**（100 seed × 5 故障率 × 4 策略，`configs/real_screw.yaml`）：

| failure_rate | naive | retry_only | rule_oracle | rule_noisy |
|---|---|---|---|---|
| 0 | 100 | 100 | 100 | 100 |
| 0.05 | 86 | 93 | 100 | 100 |
| 0.1 | 78 | 89 | 100 | 100 |
| 0.2 | 68 | 81 | 100 | 100 |
| 0.3 | 52 | 73 | 99 | 99 |

（SUCCESS 数 / 100；`retry_only` 的 FALSE_FINISH 在 rate=0.3 时是 24 次，
`naive` 是 48 次，`rule` 是 0 次。）

**验证器质量扫描**（200 seed × 5 档位，failure_rate=0.3）：`rule_noisy` 的 FALSE_FINISH
**0 → 1 → 2 → 12 → 21**（随档位单调不降），`rule_oracle` **恒为 0**，
`retry_only` 恒为 53（它不验证，档位对它没有影响）。

## 四、这一轮发现并修掉的真问题

1. **观察通道泄漏**（A1）：install / carry 的 message 直接写着「最后一颗掉在箱外」
   「补装未达目标数」——等于把故障告诉 agent，「必须验证才能发现隐藏故障」的前提没了。
2. **随机流纯度不成立**（B1）：`randint` 的底层抽取次数随 `missing` 变化，验证器又按分支
   条件抽取 → 同一条流的后续位置依赖上下文。实测：故障率 0.3 时前 6 次 install 的 kind
   序列有 **10.8%** 的 seed 会分叉。
3. **Wilson 区间在小样本 + 满分时不包含点估计**（C3）：10/10 的上界只有 0.875 →
   `ci_high - rate` 为负 → matplotlib 报 `'yerr' must not contain negative values`。
   Phase 1 的 100-seed sweep 恰好没暴露（n=100 时上界被夹到 1.0）。
4. **配置坏值被静默接受**（B2）：`verifier.kind: orcle`、`rate: 1.5`、权重拼错 `STUK`
   （等于是把注入悄悄关掉）——全部改成加载阶段报错并指出字段路径。
5. **策略名拼错会静默跑别的**（B2）：`--policies rule_noysy` 原来正常跑完、exit 0，
   实际跑的是 oracle 版本却以错误标签入表；现在走注册表，未知名字直接 exit 2。

## 五、还欠着的

- **D3 的「表即唯一来源」重构**没做（会动策略行为，收益不抵风险；见 Q14）。
- **§7 末尾原来那句「换一组权重，相对优势不会消失」**已删（没有运行支撑）；
  要保留需另做敏感性实验（2 组权重 × 100 seed）。
- **接真实 LLM** 没有实际执行过（按 TASK §9 的要求，只给命令示例）。
