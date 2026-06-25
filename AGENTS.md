# AGENTS.md - 财务采集项目 Agent 工作规则

## 项目定位
这是一个多平台、多账号、多任务的财务数据采集项目。入口是 `main.py`，任务由 `config/tasks.json` 驱动，平台实现位于 `src/finance_crawler/platforms/`。

## 修改原则
- 优先保持现有 CLI、配置结构、输出目录和文件命名兼容。
- 不要在业务代码中硬编码账号、cookie、token、绝对路径、店铺名。
- 不要把真实 HAR、日志、导出文件、密钥提交到 Git。
- 修改 runner 前，先说明该 runner 对应的 task id、平台、账号池、输出类型。
- 新增 runner 时必须更新 runner 注册表/映射、任务配置校验、dry-run 验证。
- 不能通过字段名猜测接口含义；不确定时标记 UNKNOWN，并保留原始响应样本路径或字段证据。

## 推荐工作流
1. 先运行 dry-run 展开任务：
   ```bash
   py -3 main.py --env local --dry-run
   ```
2. 再运行配置校验：
   ```bash
   py -3 scripts/validate_tasks.py
   ```
3. 修改代码后运行最小验证：
   ```bash
   py -3 scripts/smoke_plan.py --env local
   ```
4. 涉及日期逻辑时，必须带 `--today YYYY-MM-DD` 验证月/周边界。

## 输出要求
每次完成修改后，回复必须包含：
- 改了哪些文件
- 改动目的
- 影响哪些 task id / runner
- 验证命令
- 无法验证的原因或风险
