---
description: 投行观点汇总：评级分布、目标价区间、最近的上调和下调、一致预期
argument-hint: "[股票代码]"
---

汇总华尔街对 $ARGUMENTS 的最新观点。没有给代码时先问用户。

1. 按 `.claude/skills/info-gathering/SKILL.md` 用 WebSearch 搜索，至少覆盖：
   - 最近 30–60 天的评级和目标价变化：`<代码> price target raised`、`<代码> price target lowered`、`<代码> upgrade`、`<代码> downgrade`、`<代码> initiates coverage`，带上月份和年份
   - 一致预期和评级统计（MarketBeat、TipRanks、Investing.com 等，至少两家对比）
   - 最看多和最看空的机构及理由
   - 下一季度和全年的营收、EPS 一致预期
2. 用 `python3 tools/quote.py <代码>` 取现价，计算各目标价对应的涨跌空间。
3. 输出：
   - 机构表：机构 | 评级 | 目标价（变化前 → 后） | 日期 | 核心理由 | 备注（是否承销商）
   - 统计：评级分布、平均和中位目标价、最高和最低、相对现价的空间
   - 解读：分歧在哪里，目标价的时效，承销商的利益冲突，目标价过去的准确度（如果查得到）
