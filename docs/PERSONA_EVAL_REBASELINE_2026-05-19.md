# Persona Eval Rebaseline — 2026-05-19

PR #47 合并后对 Guardian 噪音过滤做的第一次 rebaseline 分析。结论是噪音过滤
本身有效，但同时暴露了一个更深的 source-coverage 问题需要后续处理。

> **Update (2026-05-19 后续核实)** — 本文最初推测 "Guardian source coverage 不够"
> 是 persona 区分度差的根因。当晚拿到 GCS 凭据实测后发现真相更具体：GCS bucket
> 数据其实很丰富（每日 65 个 CSV，191 条真实新闻 alert），但 GCS → scored snapshot
> 那一步在迁移中没有落地，所以所有 alert `category_label="Unclassified"` 且
> `score=0`。修复 spec 见 `docs/GUARDIAN_SCORED_SNAPSHOT_HANDOFF.md`，需要
> 与 AI_Treasury_Project 协调。本文以下分析仍然有效，但 "推荐后续" 一节请以
> handoff doc 为准。

## TL;DR

- ✅ PR #47 (ECB severity 0.6 → 0.3，NWS Severe/Extreme 白名单) 工作正常。
- ⚠️ Persona top-N **Jaccard = 1.00**（4 个 persona 选出来的 top-20 完全一致），
  并不是 PR #47 引入的，而是被原本的 ECB 噪音掩盖住了。根因是 Guardian 实际
  在跑的 source 太少 — 只有 4 个 source 在出数据。
- 行动：不阻塞 Phase 3C；source-coverage 留作单独的 ticket。

## 数据点

观察窗口：最近 33 个 snapshot / 1030 条 alert（约 90 天）。

| Source | 数量 | 占比 | PR #47 影响 |
|---|---:|---:|---|
| `ecb` (FX reference rates) | 879 | 85.3% | 全部降权到 0.3 → 有效分数 ~28，跌破 `MIN_GUARDIAN_SCORE=40`，**全部出局** |
| `noaa_nws` (NWS advisories) | 77 | 7.5% | 只保留 `Severe`/`Extreme`，预估 80%+ 出局 |
| `nasa_eonet` | 41 | 4.0% | 保留 |
| `bank_of_canada` | 33 | 3.2% | 保留 |

当前 30 天 baseline (`data/persona_eval/2026-05-19.json`) 中每个 persona 的
top-20 都是 100% 噪音（ECB FX-rate + NWS routine）。

## 关键发现：source coverage 是真正的瓶颈

`generate_actions.py` / `daily_monitor.py` / Guardian POC 的 source list 里写了
~37 个 source（GDELT, USGS, FRED, SEC 等），但 snapshot 历史里只看得到 4 个。
ECB 噪音过滤完成后，剩下的 alert pool（90 天 ~116 条 → ~89 条）会非常稀薄，
4 个 persona 的差异化空间几乎为零。

### 验证

把 90 天窗口跑 `python scripts/persona_eval.py --days 90 --no-llm --dry-run`：

```
Alerts kept: 116 / 1030 raw (11%)
Off-diagonal Jaccard avg: 1.00 (max: 1.00)
6 differentiation flag(s) — every persona pair @ Jaccard=1.00
```

也就是说**即使在加宽到 90 天的窗口下、`MIN_ALERTS_FOR_EVAL=50` 阈值已经满足
的情况下**，4 个 persona 选出的 top-20 仍然完全一致。这不是 PR #47 的问题，是
现在 alert pool 信号不够丰富。

## 推荐后续

按优先级：

1. **Phase 2.8 / Source Coverage Audit (独立 ticket)** — 排查为什么
   GDELT / USGS / FRED / SEC adapter 在 snapshot 历史里没有产出。可能的原因：
   - Adapter 在 `sources.yaml` 里被 disabled
   - API key 缺失（FRED / SEC 需要）
   - Adapter 实现里有错误吞掉了请求
   - 数据源本身就是低频，30-90 天窗口不足以观察
2. **正式 rebaseline** — 等 PR #47 上线 7-14 天，且至少 2-3 个新 source 开始
   出数据，再跑 persona_eval 做正式比较。
3. **Phase 3C 不阻塞** — ResponseAgent 的工作和 persona 差异化是独立的，可以
   并行推进。

## 不需要做的事

- ❌ 现在跑 `persona_eval` 写新 baseline — 数据稀疏会让 Jaccard=1.00 看起来
  像是 PR #47 引入的回归，但实际不是。
- ❌ 立刻调 persona 权重 — 在 source pool 修复前调权重没有意义，会过拟合到
  当前 4 个 source 的特征。

## 参考命令

```bash
# 模拟 PR #47 之后的 alert pool（用现有 snapshot 减去 ECB）
python3 -c "
import json
d = json.load(open('data/persona_eval/2026-05-19.json'))
non_ecb = [a for a in d['window']['alerts'] if a['source'] != 'ecb']
print(f'post-PR pool: {len(non_ecb)} alerts')
"

# 加宽窗口跑差异化检查（不写文件 / 不调 LLM）
python scripts/persona_eval.py --days 90 --no-llm --dry-run

# 查看最近 snapshot 的 source 分布
python3 -c "
import json, glob
from collections import Counter
c = Counter()
for f in sorted(glob.glob('guardian_poc/data/snapshots/alerts_*.json'))[-33:]:
    for a in json.load(open(f)).get('alerts', []):
        c[a.get('source')] += 1
print(c.most_common())
"
```
