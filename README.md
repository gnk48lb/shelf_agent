# shelf-agent

螺丝装箱 → 搬运上架的离线仿真环境与异常自愈 agent 骨架。

> 本文件在 Phase 2 完成（架构图、工具表、故障表、自愈流程图与真实 sweep 结果）。

## 安装

```bash
uv sync --extra dev
uv run pytest -q
```

## 运行

```bash
uv run python -m shelf_agent.eval.cli list-scenarios
uv run python -m shelf_agent.eval.cli demo --scenario happy --show-truth
uv run python -m shelf_agent.eval.cli sweep --config configs/real_screw.yaml --seeds 100 \
  --failure-rates 0,0.05,0.1,0.2,0.3 --policies naive,rule_oracle,rule_noisy --out results/
```
