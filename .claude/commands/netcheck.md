---
description: 检查各个数据源能不能访问，列出需要放行的域名
---

运行 `python3 tools/netcheck.py`，然后用中文说明：

- 哪些数据源可以用，哪些被拦截；被拦截时对哪些功能有影响
- 需要放行的域名清单（核心的放前面），以及放行方法：云端会话在会话标题栏的云环境菜单 → Edit → Network access 里加到 Allowed domains；也可以在自己电脑上用 Claude Code 打开仓库
- 放行之前的替代办法：用 WebSearch 取数，结果注明“来自搜索摘要，未读原文”
