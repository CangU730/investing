# investing

个人美股投资研究工作区，配合 [Claude Code](https://claude.com/claude-code) 使用。重点是**信息获取**：从 SEC、行情、新闻、宏观等来源取数，交叉验证，注明出处。

## 里面有什么

| 部分 | 位置 | 说明 |
|---|---|---|
| 数据工具 | `tools/` | 行情、SEC 文件和财务数据、内部人交易、新闻、宏观、持仓估值、网络检查。只用 Python 标准库，不用装依赖 |
| 信息获取规范 | `.claude/skills/info-gathering/` | 来源分级、交叉验证、时效检查、引用格式、网络受限时怎么办 |
| Agent | `.claude/agents/` | info-scout、filings-reader、equity-analyst、risk-reviewer、position-sizer，以及改编自 Anthropic 的 market-researcher、earnings-reviewer |
| Skill | `.claude/skills/` | 来自 Anthropic financial-services 的 16 个：财报分析、财报预览、首次覆盖报告、催化剂日历、投资逻辑跟踪、行业概览、选股、竞争格局、同业估值、DCF 等 |
| 命令 | `.claude/commands/` | 本仓库 8 个，上游 9 个 |
| 研究笔记 | `research/` | 按股票代码存放 |

## 快速开始

在 Claude Code 里打开这个仓库（云端会话或本地都可以），然后：

```
/netcheck            先看哪些数据源能用
/brief SPCX          个股情报简报
/analysts SPCX       投行评级和目标价汇总
/deepdive SPCX       深度研究：多路收集信息 → 分析 → 风险审查
/macro               宏观看板
```

也可以直接用中文提问，比如“查一下 SpaceX 最近的内部人交易”。

## 云端会话的网络设置

Claude Code 云端会话默认限制外网访问，金融数据网站会被拦截（`/netcheck` 会列出来）。

放行方法：会话标题栏的云环境菜单 → **Edit** → **Network access**，把下面的域名加入 **Allowed domains**，并保持勾选 Allow package managers。说明文档：<https://code.claude.com/docs/en/cloud-environments#network-access>

核心（数据工具需要）：

```
www.sec.gov
data.sec.gov
efts.sec.gov
query1.finance.yahoo.com
feeds.finance.yahoo.com
news.google.com
fred.stlouisfed.org
stooq.com
```

推荐（让 Claude 能直接读新闻和数据网站的原文）：

```
finance.yahoo.com
www.reuters.com
www.cnbc.com
www.marketwatch.com
www.nasdaq.com
www.investing.com
www.marketbeat.com
www.tipranks.com
stockanalysis.com
seekingalpha.com
www.fool.com
en.wikipedia.org
```

放行之前，Claude 会改用网页搜索取数，并注明“来自搜索摘要，未读原文”。在自己电脑上用 Claude Code 打开仓库，一般没有这个限制。

## 可选配置

| 环境变量 | 作用 |
|---|---|
| `SEC_USER_AGENT` | SEC 要求请求带上身份和邮箱，例如 `Zhang San zhangsan@example.com`。不设也能试，但可能被 SEC 拒绝 |
| `FMP_API_KEY` | Financial Modeling Prep 的 key，备用行情源 |
| `ALPHAVANTAGE_API_KEY` | Alpha Vantage 的 key，备用行情源 |

云端会话在环境设置里添加环境变量；本地在 shell 里 `export`。

另外，可以在 claude.ai 的「设置 → 连接器」里连接 FMP、Alpha Vantage、Financial Datasets、Bigdata.com 等金融数据连接器。会话里有这些连接器时，Claude 会优先使用。

## 持仓估值

```
cp portfolio/holdings.example.csv portfolio/holdings.csv   # 然后填入真实持仓
python3 tools/portfolio.py --cny
```

`portfolio/holdings.csv` 已加入 `.gitignore`，不会被提交。**这个仓库是公开的**，请不要把个人财务信息写进会提交的文件。

## 测试

```
python3 -m unittest discover -s tests -v
```

测试使用 `tests/fixtures/` 里的虚构样例数据，不需要联网。

## 第三方内容

`.claude/` 下的部分 skill、命令和 agent 来自 [anthropics/financial-services](https://github.com/anthropics/financial-services)，按 Apache License 2.0 使用。导入清单和改动说明见 `third_party/anthropic-financial-services/NOTICE.md`。

## 免责声明

本仓库的内容是信息整理和分析，不构成投资建议。投资有风险，决策前请结合自身情况，必要时咨询持牌专业人士。
