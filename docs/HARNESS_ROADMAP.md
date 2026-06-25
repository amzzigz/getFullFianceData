# 财务采集项目 Harness Engineering 路线图

## 目标
把项目从“能跑的爬虫集合”升级为“可验证、可维护、可交给 Agent 安全修改的采集框架”。

## 阶段 1：建立项目护栏
- 增加 `AGENTS.md`，约束 Agent 不乱改配置、路径、输出格式。
- 增加 `scripts/validate_tasks.py`，校验 `config/tasks.json` 中的 runner、周期、账号池、必要字段。
- 增加 `scripts/smoke_plan.py`，用 dry-run 验证任务展开、账号选择、日期周期。

## 阶段 2：拆分 main.py 职责
建议拆出：
- `task_loader.py`：任务读取、筛选、周期展开。
- `runner_registry.py`：runner 字符串到函数的映射。
- `executor.py`：并发、重试、结果汇总。
- `cli.py`：命令行参数。

这样 Agent 修改某个 runner 时，不会误伤主流程。

## 阶段 3：抽象平台通用能力
目前多个 SHEIN runner 都有类似逻辑：`post_json`、下载中心轮询、文件名、供应商上下文。
建议抽出：
- `http_client.py`：统一 timeout/retry/logging。
- `download_center_client.py`：统一下载中心查询、匹配、下载。
- `shein_context.py`：统一 supplier/account/session。
- `artifact_naming.py`：统一输出路径和文件名。

## 阶段 4：为 Agent 准备 fixtures
不要让 Agent 依赖真实平台实时接口来判断对错。准备脱敏样本：
- `fixtures/tasks.minimal.json`
- `fixtures/accounts.minimal.json`
- `fixtures/shein_download_center_response.json`
- `fixtures/temu_user_info_response.json`
- `fixtures/run_summary.example.json`

## 阶段 5：再写 skills
当某类任务重复 5 次以上，再封装 skill：
- `finance-crawler-runner`：新增/修改 runner。
- `har-to-task-config`：从 HAR 提炼 task 配置。
- `download-center-debugger`：定位下载中心匹配失败。

## 学习重点
Harness engineering 不是先写 skill，而是先让项目具备：
- 明确边界
- 可重复运行的验证命令
- 可审计的输出
- 小样本 fixtures
- 明确失败信息
