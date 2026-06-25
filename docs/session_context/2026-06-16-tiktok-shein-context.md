# 2026-06-16 TikTok 与 SHEIN 上下文

## TikTok E1/E2 邮箱登录

相关背景：

- TikTok E1/E2 需要走邮箱登录分支。
- 成功标准不是单测通过，而是页面在约定时间内离开登录页。

关键原则：

- 不允许在还没切换到邮箱登录方式时提交表单。
- 必须看到邮箱输入框、密码输入框，并确认密码非空后才点击登录。
- 账号池中 E1/E2 的邮箱登录逻辑应保持窄范围，不要扩大到其他 TikTok 账号。

相关实现位置：

- `tools/ziniu_auth_login_extracted.py`
- TikTok 平台脚本位于 `src/finance_crawler/platforms/` 下相关模块。

## TikTok 邮箱登录实测经验

实测中出现过：

- 已切换到邮箱登录但未点击登录按钮。
- 跳转首页较慢，需要给足等待窗口。
- 若页面仍在 `account/login`，不能声称登录成功。

后续验证时建议使用真实页面短跑，不要只跑 mock 测试。

## SHEIN 总调结果归类

已调整过的业务期望：

- SHEIN 汇总里“无数据可导出”不应一律写失败。
- 下载中心里某些模块可能返回导出失败文案，但业务含义是当前无数据或未生成可下载文件。
- 面向业务人员的总结应区分：
  - 成功导出
  - 无数据
  - 真失败

典型例子：

```text
下载中心轮询后仍未拿到文件链接，但 last_file_response 中包含 MILS-导出文件失败
```

这类情况需要结合模块业务语义判断，不要简单归类为失败。

## SHEIN 页面捕获工具

本会话中出现过用于当前页捕获的辅助脚本与测试：

- `scripts/capture_shein_current_page.py`
- `tests/test_capture_shein_current_page.py`

后续若继续处理 SHEIN 当前页面诊断，应先确认这些文件是否属于当前任务范围，再决定是否复用。

## SHEIN 新环境模拟登录不稳定

2026-06-16 根据单模块日志定位：

- `shein_platform_fees` 批量单模块中，A3/A4/A20/A21 报 `code=20302 msg=子系统登录重定向`。
- 这类错误不是平台费用接口本身业务失败，而是主站 `#/home/` 已登录后，GSFS/MWS 子系统 SSO 还没有就绪，代码过早请求 `getSupplierOperateInfo`。
- 修复方向：SHEIN/POP 财务模块把各自 `target_page` 传给 `auth_login`，鉴权层对 SHEIN 目标页直接打开目标子系统页并按目标页区分缓存。
- 已新增回归测试：`tests/test_shein_target_auth.py`。
