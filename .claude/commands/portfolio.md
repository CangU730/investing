---
description: 持仓体检：市值、盈亏、仓位占比、集中度和近期事件
argument-hint: "[可选：持仓 CSV 路径]"
---

1. 运行 `python3 tools/portfolio.py $ARGUMENTS --cny`。如果 `portfolio/holdings.csv` 不存在，告诉用户复制 `portfolio/holdings.example.csv` 后填写（这个文件已被 .gitignore 排除，不会上传到公开仓库）。
2. 检查：单一持仓和同一主题的集中度、现金比例，以及各持仓未来 30 天的催化剂（财报、解禁、重大事件，按 info-gathering 规则查）。
3. 输出：持仓表、风险提示、需要关注的事件日历。不要把持仓细节写进会提交的文件。
