# 2026-06-16 TEMU 上下文

## 账号配置方向

TEMU 账号配置已改为稳定字段定位，避免紫鸟展示名变化导致本地配置失效。对象配置形态：

```json
{
  "label": "账号显示名",
  "platform_id": 149,
  "siteId": 391,
  "store_username": "店铺登录手机号或账号"
}
```

注意：

- `label` 用于日志、dry-run 和人工筛选。
- `platform_id + siteId + store_username` 用于从紫鸟 `getBrowserList` 匹配真实浏览器环境。
- `accounts.prod.json` 是本地生产配置，不应提交真实文件；示例模板需要保持对象格式。

## TEMU 登录与授权结论

已修复过的关键点：

- TEMU 登录页切换到手机号登录时，必须使用原生 hover/click，不使用 JS 直接点击 tab，否则浏览器保存密码可能不触发自动填充。
- 登录提交前必须确认密码已填充，并勾选协议框。
- 跨域或同页授权弹窗都要处理；授权框可能出现多个复选框，不能只勾选一个。
- 登录成功判断不能只看 URL，应通过卖家中心 `userInfo` 之类接口确认会话有效。

## TEMU 资金明细导出

相关文件：

- `src/finance_crawler/platforms/temu_fund_details.py`
- `tests/test_temu_login.py`

已处理过的问题：

- 一个账号多个店铺时，后续店铺导出可能出现 task id 相关异常，需要按店铺重新建立 mall 上下文。
- 授权弹窗可能在同一个浏览器窗口内出现，而不是跨域新窗口；处理逻辑需要扫描当前真实业务页，不要依赖 latest tab。
- 对全球/欧区/美国等 Agent Seller 区域，进入授权和下载链路时需要保持正确 mallId。

## TEMU 热销款导出

新增独立脚本：

- `scripts/export_temu_hot_products.py`
- `tests/test_export_temu_hot_products.py`
- 设计与计划：
  - `docs/superpowers/specs/2026-06-09-temu-hot-products-design.md`
  - `docs/superpowers/plans/2026-06-09-temu-hot-products.md`

接口来源：

```text
POST https://agentseller.temu.com/mms/venom/api/supplier/sales/management/listOverall
```

固定筛选：

```json
{"hotTag": true}
```

Excel 仅输出 7 列：

```text
账号、店铺名、商品名称、品类、skc、skc货号、申报价格
```

重要技术链路：

```text
卖家中心 -> link-agent-seller 中转 -> 全球站授权弹窗 -> /stock-entry -> userInfo 鉴权确认 -> 热销款接口
```

不要直接拼 `obtainCode + authentication` 进入全球站；实测证明这会在某些店铺回到卖家中心首页并导致误点“商家中心”。

已验证：

- HAR 离线解析可导出 `MinimalKnit` 3 条热销款。
- 在线实跑 `B23/B25/B26-主账号-YF / MinimalKnit` 成功返回 3 条。
- 聚焦测试和全量测试曾验证通过。

常用命令：

```bat
py -3 scripts\export_temu_hot_products.py --har "C:\Users\ln\Desktop\temu-热销款数据.har" --account "B23/B25/B26-主账号-YF"
py -3 scripts\export_temu_hot_products.py --account "B23/B25/B26-主账号-YF" --shop "MinimalKnit"
py -3 scripts\export_temu_hot_products.py
```

## TEMU 账号池补充记录

曾确认紫鸟实时列表里存在 `B2/B3/B5/B6/B7运营账号2`，本地 `accounts.prod.json` 原先漏配导致脚本在本地账号池阶段报“账号不存在”。已按稳定字段补入本地配置；后续在 2026-06-16 已按用户要求从项目生产账号池剔除。后续迁移时应以目标机器的生产账号池为准。
