---
description: SEC 文件速读：最近的文件清单和要点，或回答某个具体条款问题
argument-hint: "[股票代码] [问题，可选]"
---

用 filings-reader agent 处理：$ARGUMENTS

- 只给了代码：列出最近 90 天的 SEC 文件（`python3 tools/sec.py filings <代码> --since <90 天前>`），挑出重要的（10-K、10-Q、8-K、S-1/424B、DEF 14A、Form 144、13D/G）逐份总结要点，并用 `python3 tools/sec.py insiders <代码>` 汇总内部人交易。
- 给了具体问题（比如“锁定期怎么规定的”）：找到对应文件和段落，引用原文回答。

每个要点都注明文件类型、日期和链接。
