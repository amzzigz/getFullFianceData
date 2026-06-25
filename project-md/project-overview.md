# 财务项目概览

## 项目目标

自动化导出财务相关平台数据，覆盖 TikTok、TEMU、SHEIN/POP、速卖通等账号池。

## 核心结构

- `main.py`：任务调度入口。
- `config/tasks.json`：平台任务定义。
- `config/accounts.prod.json`：本机生产账号池。
- `src/finance_crawler/`：业务实现。
- `tools/ziniu_auth_login_extracted.py`：紫鸟鉴权和平台登录复用入口。
- `output/`：本地导出文件、capture 和 run summary。

## 接手原则

- 先读 `E:\自动化\AGENTS.md` 和项目上下文 Markdown。
- 涉及紫鸟、SHEIN、TEMU、TikTok 登录时，优先复用现有登录 helper。
- 工作区可能已有未提交改动，修改前先看 `git status --short`，不要回滚非当前任务改动。
