---
description: 个股深度研究：先多路收集信息，再做完整分析，最后由风险审查员挑毛病
argument-hint: "[股票代码]"
---

对 $ARGUMENTS 做深度研究。没有给代码时先问用户。

1. **收集信息（并行）。** 先运行 `python3 tools/netcheck.py`，再同时派出 3–4 个 info-scout，分别负责：
   - 价格走势、财务数据和最新财报（`quote.py`、`sec.py facts`、8-K）
   - 近 60 天的新闻、重大事件和即将到来的催化剂
   - 投行评级、目标价和一致预期
   - SEC 文件要点：股本结构、锁定期、内部人交易、风险因素（可以交给 filings-reader）
   把网络检查的结果告诉每个 agent。
2. **分析。** 把收集到的结果交给 equity-analyst，写出完整研究笔记，存到 `research/<代码>/YYYY-MM-DD-analysis.md`。
3. **挑毛病。** 把研究笔记交给 risk-reviewer 审查，把审查意见补进笔记的“风险”部分。
4. **在对话里汇报**：一句话结论、关键数据、估值区间、主要分歧、最大的三个风险、接下来的关键时间点。如果用户提供了资金和风险偏好，再用 position-sizer 给出仓位和分批建议。
5. 如果 `research/<代码>/thesis.md` 不存在，按 thesis-tracker skill 的格式建一个，状态写“观察中”。
