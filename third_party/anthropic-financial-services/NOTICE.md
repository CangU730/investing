# 第三方内容说明：anthropics/financial-services

本仓库 `.claude/` 下的部分 skill、命令和 agent 来自 Anthropic 的开源仓库
[anthropics/financial-services](https://github.com/anthropics/financial-services)，
按 Apache License 2.0 使用，许可证全文见同目录 `LICENSE`。

- 上游提交：`60565064557ca77b12f62aa7877f365d111c129d`
- 导入日期：2026-10-29

## 原样导入（未修改）

| 本仓库路径 | 上游路径 |
|---|---|
| `.claude/skills/catalyst-calendar` | `plugins/vertical-plugins/equity-research/skills/catalyst-calendar` |
| `.claude/skills/earnings-analysis` | `plugins/vertical-plugins/equity-research/skills/earnings-analysis` |
| `.claude/skills/earnings-preview` | `plugins/vertical-plugins/equity-research/skills/earnings-preview` |
| `.claude/skills/idea-generation` | `plugins/vertical-plugins/equity-research/skills/idea-generation` |
| `.claude/skills/initiating-coverage` | `plugins/vertical-plugins/equity-research/skills/initiating-coverage` |
| `.claude/skills/model-update` | `plugins/vertical-plugins/equity-research/skills/model-update` |
| `.claude/skills/morning-note` | `plugins/vertical-plugins/equity-research/skills/morning-note` |
| `.claude/skills/sector-overview` | `plugins/vertical-plugins/equity-research/skills/sector-overview` |
| `.claude/skills/thesis-tracker` | `plugins/vertical-plugins/equity-research/skills/thesis-tracker` |
| `.claude/commands/*.md`（catalysts、earnings、earnings-preview、initiate、model-update、morning-note、screen、sector、thesis） | `plugins/vertical-plugins/equity-research/commands/` |
| `.claude/skills/competitive-analysis` | `plugins/agent-plugins/market-researcher/skills/competitive-analysis` |
| `.claude/skills/comps-analysis` | `plugins/agent-plugins/market-researcher/skills/comps-analysis` |
| `.claude/skills/pptx-author` | `plugins/agent-plugins/market-researcher/skills/pptx-author` |
| `.claude/skills/audit-xls` | `plugins/agent-plugins/earnings-reviewer/skills/audit-xls` |
| `.claude/skills/xlsx-author` | `plugins/agent-plugins/earnings-reviewer/skills/xlsx-author` |
| `.claude/skills/dcf-model` | `plugins/vertical-plugins/financial-analysis/skills/dcf-model` |

上游 market-researcher 和 earnings-reviewer 插件里的 `idea-generation`、`sector-overview`、
`earnings-analysis`、`earnings-preview`、`model-update`、`morning-note` 与 equity-research
中的同名 skill 完全相同，只导入一份。

## 改编（已修改）

| 本仓库路径 | 上游路径 | 改动 |
|---|---|---|
| `.claude/agents/market-researcher.md` | `plugins/agent-plugins/market-researcher/agents/market-researcher.md` | 数据源从 CapIQ/FactSet MCP 换成 `tools/` 脚本和网页搜索；改为中文；面向个人投资者 |
| `.claude/agents/earnings-reviewer.md` | `plugins/agent-plugins/earnings-reviewer/agents/earnings-reviewer.md` | 数据源从 FactSet/Daloopa MCP 换成 `tools/` 脚本和网页搜索；改为中文；模型更新改为可选 |

## 使用时要注意

这些 skill 原本是为机构卖方研究员写的，默认输出 Word/Excel 报告，并假设有 Bloomberg、
FactSet 等付费数据。在本仓库里的用法见根目录 `CLAUDE.md`：默认输出中文 Markdown，
数据来自 `tools/` 脚本和网页搜索，只有用户要求时才生成 Word/Excel/PPT 文件。
