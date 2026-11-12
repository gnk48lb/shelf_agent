# 故障率扫描结果

- 配置：`configs\real_screw.yaml`（scenario=real_screw，N=6）
- seeds：10（每个故障率 × 每个策略共用同一批 seed，配对比较）
- 故障率：0.0, 0.3
- 策略：naive, rule_noisy
- 目标螺丝数 N = 6；失败率参数见 `configs/real_screw.yaml`

| failure_rate | policy | episodes | SUCCESS | FALSE_FINISH | SAFE_ABORT | 其他 | SUCCESS 95% CI | unsafe_release | makespan_s (mean±se) | 求助次数 |
|---|---|---|---|---|---|---|---|---|---|---|
| 0.0 | naive | 10 | 10 | 0 | 0 | 0 | [0.722, 1.000] | 0.0 | 108.8±1.4 | 0.00 |
| 0.0 | rule_noisy | 10 | 10 | 0 | 0 | 0 | [0.722, 1.000] | 0.0 | 112.8±1.4 | 0.00 |
| 0.3 | naive | 10 | 4 | 6 | 0 | 0 | [0.168, 0.687] | 0.2 | 146.3±16.5 | 0.00 |
| 0.3 | rule_noisy | 10 | 10 | 0 | 0 | 0 | [0.722, 1.000] | 0.0 | 237.1±41.8 | 0.10 |

## 配对比较（同一批 seed）

- naive vs rule_noisy：配对 20 个 seed，胜 0 / 负 6 / 平 14；成功率 0.700 vs 1.000
