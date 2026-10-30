---
description: 每日持仓检查：行情、新闻、事件，对照卖出条件给出持有、减仓或卖出的判断
argument-hint: "[可选：今天关心的问题；可以附持仓截图]"
---

帮用户做每日持仓检查，重点回答“该不该卖、什么时候卖”。$ARGUMENTS

1. **确认时间。** 运行 `date` 和 `TZ=America/New_York date`，说明美股现在是盘前、盘中、盘后还是休市。美股夏令时的交易时间是北京时间 21:30–04:00，冬令时（11 月第一个周日起）是 22:30–05:00。
2. **持仓。** 读 `portfolio/holdings.csv`。文件不存在（新会话）时，请用户发持仓截图，按截图写入；用户发了新截图，就以截图为准更新。这个文件已加入 .gitignore，不会提交。
3. **取数。** 本会话第一次运行时，先跑 `python3 tools/netcheck.py`。然后：
   - `python3 tools/portfolio.py --cny`
   - 每只个股：
     - `python3 tools/quote.py <代码> --history 1mo`
     - `python3 tools/news.py --ticker <代码> --days 2`
     - `python3 tools/sec.py filings <代码> --forms 8-K,4,144 --since <两天前>`
   - 宏观：`python3 tools/macro.py us10y vix`，再用 WebSearch 查当天影响美股的大事件
   - 重要新闻按 `.claude/skills/info-gathering/SKILL.md` 读原文或交叉核实，不能只看标题
4. **对照卖出条件。** 逐只读 `research/<代码>/thesis.md` 里的“卖出或减仓条件”和催化剂日历，判断每一条是否已经触发、离触发还有多远。没有 thesis.md 的持仓，先说明这一点，再给出临时判断。
5. **输出。** 用中文，先给结论：
   - 一句话总结：今天要不要动
   - 表格：代码 | 现价 | 今日涨跌 | 持仓盈亏 | 信号（持有 / 观察 / 减仓 / 卖出）| 理由
   - 触发了卖出或减仓条件的：说明依据、建议卖多少、怎么下单（限价单、分批）
   - 未来 7 天和持仓相关的事件
   - 说明这不是个性化投资建议；涉及和 Anthropic 有业务关系的公司时，说明利益相关
6. **存档。** 出现实质性变化（财报、指引变化、证伪信号触发）时，在对应 `thesis.md` 的“更新记录”里追加一行。仓库是公开的，不写用户的持仓数量、成本和账户信息。
