# TEMU 热销款数据导出设计

## 目标

新增独立脚本，从 TEMU 全球站“销售管理 -> 热销款”接口抓取全部店铺记录，并导出 Excel。

## 数据范围

- 接口：`POST https://agentseller.temu.com/mms/venom/api/supplier/sales/management/listOverall`
- 固定筛选：`hotTag=true`
- 按接口 `total` 自动分页，直到取得全部记录
- 每个 SKC 导出一行，不按尺码 SKU 拆行
- 申报价格取该 SKC 下 SKU 的 `supplierPrice`，由分转换为元；若同一 SKC 存在多个不同价格，使用去重后以 `/` 连接的价格文本

## Excel 字段

严格只导出以下字段：

1. 账号
2. 店铺名
3. 商品名称
4. 品类
5. skc
6. skc货号
7. 申报价格

## 运行方式

- 默认读取 `config/accounts.prod.json` 中全部 TEMU 账号并遍历全部店铺
- `--account` 支持按账号标签筛选
- `--shop` 支持按店铺名、店铺 ID 或 B 编号筛选
- `--har` 为离线验证模式，只解析 HAR 中最后一次热销款响应，不启动紫鸟
- 输出为一个工作簿，工作表名为“热销款”

## 验证标准

- HAR 离线解析得到 `MinimalKnit` 的 3 条热销 SKC
- Excel 表头严格等于指定 7 列
- 在线测试可只运行 `B23/B25/B26-主账号-YF` 的 `MinimalKnit`
