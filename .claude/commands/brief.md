---
description: 个股情报简报：价格、最新财报、新闻和 8-K、投行评级、内部人交易与解禁、催化剂
argument-hint: "[股票代码]"
---

为 $ARGUMENTS 做一份最近 30 天的情报简报。没有给代码时先问用户。

1. 交给 info-scout agent，按它的“个股标准清单”收集信息，遵守 `.claude/skills/info-gathering/SKILL.md`。
2. 把简报存到 `research/<代码>/YYYY-MM-DD-brief.md`。
3. 在对话里给出：一句话结论、关键数据表、最值得关注的 3 件事、数据说明（哪些来源被拦截、哪些只来自搜索摘要）。
