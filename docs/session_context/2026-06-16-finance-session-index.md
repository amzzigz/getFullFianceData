# 2026-06-16 财务项目会话上下文索引

## 记录目的

这组文档用于给后续会话快速接手 `E:\自动化\财务` 项目。内容只记录本会话中对项目有复用价值的实现、验证、命名和排查结论。

## 刻意排除

- 不记录本轮明确要求剔除的账号权限排查过程。
- 不记录任何真实密码、cookie、token、HAR 响应原文或敏感配置。
- 不把本地导出的 Excel、日志、HAR 视为应提交资产。

## 本组文档

- [TEMU 上下文](2026-06-16-temu-context.md)
- [TikTok 与 SHEIN 上下文](2026-06-16-tiktok-shein-context.md)
- [部署与运行上下文](2026-06-16-deploy-runbook-context.md)

## 当前工作区提醒

本项目工作区存在多项未提交修改。后续会话不要默认认为所有 dirty 文件都属于同一个需求；修改前应先 `git status --short` 并按任务范围归因。

## 推荐接手顺序

1. 先读项目根目录 `AGENTS.md`。
2. 再读本索引和对应平台上下文。
3. 涉及 TEMU、TikTok、SHEIN 任一平台时，先跑最小验证而不是只看单测。
4. 涉及紫鸟登录或平台模拟点击时，复用 `tools/ziniu_auth_login_extracted.py` 中已有逻辑，避免重新造一套登录流。

## 常用验证命令

```bat
py -3 scripts\validate_tasks.py --env prod
py -3 -m pytest -q
```

如果只改某个平台，优先跑对应聚焦测试，再按风险决定是否跑全量测试。
