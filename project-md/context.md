# 长期上下文

## GitHub 正式仓库

- 唯一正式远端为 `https://github.com/amzzigz/getFullFianceData.git`，本地统一使用 remote 名称 `origin`。
- 旧仓库 `getFianceData` 已废弃，不再 fetch、push 或作为代码基线；后续会话提交前必须核对 `origin/main`。

## SHEIN 当前最稳定导出模式

- 2026-06-22 新环境实测确认：当前最稳定模式为 `dd5b43a` 之后的 SHEIN/POP/A1B 账号批处理方案。
- 核心模式：按账号批处理；同账号先串行完成紫鸟登录和 SHEIN/POP/A1B 子系统 warm-up，提取一份账号级 cookie；随后按 `runtime.account_module_concurrency` 执行账号内模块导出。
- 紫鸟连接策略是稳定关键：优先复用当前正在运行并已登录的紫鸟实例；`software.ziniu_install_dir` / `ZINIAO_INSTALL_DIR` 只作为紫鸟端口不可用、需要自动启动时的兜底，不应覆盖运行中的紫鸟进程。
- 新环境反馈：该模式比逐模块反复开关浏览器效率提升接近一倍，且输出文件完整性正常。
- 后续 SHEIN 稳定性调整应优先保护这套模式，不要轻易恢复为每个模块独立打开/关闭浏览器，也不要让配置目录强行接管运行中的紫鸟实例。
- 2026-06-25 试验版在不改变上述批处理模式的前提下接入 DrissionPage 原生标签页恢复：普通 SHEIN/POP/A1B 共享登录和 A1Y-A4Y 申合链路均使用 `existing_only()` 接管紫鸟浏览器；断联时先 `tab.reconnect(wait=1)`，原 target 消失时按业务域名重新选择标签页，单浏览器最多恢复 3 次。
- 标签页恢复仅处理当前紫鸟浏览器内的瞬时 DrissionPage 断联；恢复失败后仍沿用现有 stopBrowser、账号级重试和失败补跑，不改变鉴权并发槽、账号级 cookie 或模块并发。
- 2026-06-25 部署验证发现 `existing_only()` 后继续调用 `browser.new_tab()` 会与紫鸟销毁/替换初始 target 产生竞态，触发 DrissionPage `_onTargetDestroyed` 的 `Set changed size during iteration`，随后报 `No such target id`。当前 SHEIN 两条链路已改为只复用 `browser.latest_tab`，目标页导航也放入可重连循环，不再主动创建新 target。

## SHEIN/POP 资金类导出

- `pop_balance_records` 使用 `src/finance_crawler/platforms/balance_records.py`。
- 币种来自接口 `/mws/mwms/sso/metadata/query/supplier/currency`。
- 列表接口为 `/mws/mwms/sso/balance/queryBalanceRecord`。
- A21POP/A23POP 的资金流水模块当前未启用；这两个账号币种接口返回 `info: []` 时按业务无数据处理，不进入任务重试或最终失败补跑。其他账号币种为空仍保留为失败，避免掩盖异常。
- `pop_funds` 复用 `src/finance_crawler/platforms/shein_funds.py`，输出目录和 capture 必须尊重任务配置里的 `platform=pop`。

## SHEIN A1Y-A4Y 申合报账单

- `shein_a1y_a4y_report_bill` 使用独立浏览器流程 `src/finance_crawler/platforms/shenhe_report_bill.py`，不属于普通 SHEIN/POP/A1B 共享 cookie 批处理。
- 2026-06-24 部署机实测确认：申合流程不能在 `shenhe888.com` 页面出现后就释放鉴权槽；后续账号调用紫鸟 `startBrowser` 会打断前一账号仍在进行的接口查询/下载，形成 A1Y/A2Y 互相断联和立即重试。
- A1Y-A4Y 每个账号必须从紫鸟启动、页面连接、接口查询、文件下载直到 `stopBrowser` 完整占用统一鉴权槽，申合账号之间端到端串行；普通 SHEIN/POP 接口任务不因此改为全局串行。
- 如果申合浏览器在 `start_logged_in_page()` 返回前断联或登录超时，必须在函数内部使用已取得的 `browserOauth` 停止浏览器并关闭页面，避免失败补跑接管断开的残留环境。
- `run_20260624_142547.log` 中 A1Y/A2Y、A3Y/A4Y 仍交叠运行，证明该次执行没有使用端到端串行提交 `1b09b4a` 的代码路径；不能据此判定 `1b09b4a` 仍有同样并发缺陷，部署复测前需先确认 `git rev-parse HEAD`。

## TEMU 账号池

- `config/accounts.prod.json` 的 `temu` 列表使用对象格式：`label`、`platform_id`、`siteId`、`store_username`。
- `label` 用于日志和人工筛选，实际匹配应优先依赖稳定字段。

## 业务口径

- 对业务总结不要把所有未下载文件都归类为失败。
- 需要区分：成功导出、无数据、账号/权限/页面状态导致的真失败。

## TikTok / E1E2

- 普通 TikTok C 系列总调按账号共享一个浏览器。`run_20260624_163559.log` 中 C1 在共享浏览器初始化阶段发生 DrissionPage `与页面的连接已断开`，尚未进入任何业务模块。
- 当前根因有两层：`start_tiktok_browser()` 已取得 `browserOauth` 但尚未返回上下文时抛出异常，外层 `ctx=None` 导致无法执行 `stopBrowser`；同时失败被记录为虚拟任务 `tiktok_account_batch`，不在任务配置中，最终失败补跑无法识别。
- 普通 TikTok 共享浏览器启动函数返回前失败时，必须使用已取得的 `browserOauth` 主动停止浏览器并关闭页面；共享浏览器初始化应按账号级 `retry_count` 重试，最终失败应展开为真实模块结果供串行补跑。
- 普通 TikTok 停止浏览器必须检查紫鸟 `stopBrowser` 的 `statusCode=0`，失败时最多尝试 2 次并记录不含 `browserOauth` 的警告。共享启动失败结果保留真实模块 ID 和分组标记，最终补跑按账号重新运行一次共享浏览器批次；其他模块失败仍走单模块补跑。
- 普通 TikTok 账号池来自 `tools/tk账号池.txt`，当前包含 C 系列账号；E1/E2 邮箱账号池来自 `tools/E1-E2.txt`，通过 `config.py` 载入为独立 `tiktok_email` 账号源。
- `config/tasks.json` 中普通 TikTok 平台有提现明细、销售数据、费用中心、资金账户 4 个模块；`tiktok_email_income` 单独使用 `platform=E1E2`、`account_source=tiktok_email`、`runner=tiktok_email.income`，不并入普通 TikTok 共享浏览器批次。
- E1/E2 走美国商家后台 `seller.us.tiktokshopglobalselling.com/finance/bills`，默认时区为 `America/Anchorage`，按上个自然月创建 income xlsx 导出。
- TikTok 紫鸟登录入口复用 `tools/ziniu_auth_login_extracted.py`。其中 E1/E2 邮箱登录分支只应对账号名包含独立 `E1` / `E2` 时触发。
- E1/E2 登录成功不能只凭代码或单元测试判断；历史现场验证要求是退出登录后 30 秒内进入非 login 页面，否则视为失败。
- E1E2 的稳定性受机器配置影响很大。低配环境下不能只串行 `startBrowser`，两个账号的完整任务也应端到端串行，避免一个账号导出时另一个账号启动紫鸟浏览器导致页面刷新、连接断开或本地 `16851` 超时。
- 2026-06-22 复核 `7dc6ee8`：该 SHA 能按对象取到，但当前远端 `main` 是 `dd5b43a`；两者在 E1E2 导出模块无差异，稳定性差异集中在紫鸟 helper。合并策略是保留 SHEIN 已验证稳定的“运行中紫鸟路径优先”，同时健康检查不再因 `browserList=[]` 主动杀紫鸟重启，避免低配/占线环境扩大波动。
- 2026-06-24 部署机与本机对比确认：E1E2 已端到端串行，但部署机仍会在 Bills 页面自动跳转/刷新期间调用 `run_js(fetch)`，触发 DrissionPage `页面被刷新`；本机页面在固定 3-4 秒内完成，因此不易复现。
- E1E2 Bills 页当前采用 DrissionPage 原生业务就绪门槛：导航前监听 `/api/v3/seller/common/get`，使用 `wait.url_change()` 和 `wait.doc_loaded()` 约束导航阶段，以 seller API 数据包作为业务页面可用信号并直接复用其中的 `seller_id`。监听不可用或超时才回退浏览器 fetch；页面刷新短重试仅作为兜底。

## TEMU 资金明细批量导出口径

- TEMU 卖家中心登录页默认停在“扫码登录”，此时页面没有手机号和密码输入框。模拟登录必须先确认“手机号登录”Tab 已切换成功；全字段均为 `False` 表示登录表单尚未出现，不是保存的账号密码未自动填充。
- `temu_fund_details` 每个店铺理论输出 4 个区域文件：卖家中心、全球、欧区、美国。
- 当前 `TaskResult.output_path` 只保留前三个输出路径并追加 `...`，所以终端汇总里的 `输出文件` 不是实际文件总数。
- 判断完整性应以输出目录实际 xlsx、capture/run summary 的 `data.outputs` 或 `mall_results.regionResults` 为准，并把“无账单提示/仅表头空明细”和“文件缺失”分开说明。
- 如果紫鸟打开 TEMU 后默认停在无权限/异常店铺，前端店铺上下文可能污染后续页面控制；资金明细导出应在获取并筛选 `mallList` 后，先把卖家中心上下文固定到第一个目标店铺，再进入资金明细页和开始接口导出。
- 单独跑一个 TEMU 账号的一个资金明细模块时，`runtime.account_module_concurrency` 基本不提速；该参数只影响同账号多模块并发，TEMU 模块内部店铺和区域当前仍是串行。
- `run_20260624_223608.log` 证明仅把登录阶段纳入 `ziniu_auth_concurrency=1` 仍不够：前一账号导出时启动下一账号，仍会出现 DrissionPage 断联，随后紫鸟本地 `127.0.0.1:16851` 连续读超时，串行补跑也无法恢复。
- TEMU 资金明细现在按账号端到端占用统一鉴权槽，从 `startBrowser`、登录、店铺/区域导出直到确认 `stopBrowser` 后才释放；因此 `max_workers=2` 时其他任务仍可排队，但同一时刻只运行一个 TEMU 浏览器账号。
- TEMU `stopBrowser` 必须以 `statusCode=0` 才算成功；失败时短暂等待并最多尝试 2 次，避免浏览器尚未停止就立即进入补跑。
- `run_20260625_002325.log` 进一步证明：只有尝试级鉴权锁时，线程池中的其他账号会在失败账号两次尝试之间插队，形成长批次高频交错的停止/启动；紫鸟停止成功响应是异步确认，不能立即启动下一个环境。
- 纯 `temu_fund_details` 运行在任务调度层使用单 worker，保证同一账号的内部重试连续执行；混合平台运行只串行 TEMU 作业，不降低其他平台任务并发。
- 紫鸟 `getBrowserList` 是账号环境池，不能证明某次运行会话已退出。TEMU 保存 `startBrowser` 返回的 `debuggingPort`，关闭后最多等待 10 秒确认该端口停止监听；超时则当前任务失败并阻断本进程后续 TEMU 启动。
- `run_20260625_005750.log` 显示纯串行后仍有 B30/B23/B2 在 `startBrowser` 后、登录点击前随机断联，说明主要故障还包含 DrissionPage 初次控制绑定不稳；浏览器环境本身未必失效。
- TEMU 启动后使用 `ChromiumOptions.existing_only()` 接管紫鸟已经打开的浏览器，并优先使用现有 `latest_tab`，不再立即创建新标签页。
- DrissionPage 页面连接断开时最多恢复 3 次：先调用当前 Tab 的官方 `reconnect(wait=1)`；原 target 已销毁时按 `kuajingmaihuo.com` / `temu.com` 重新枚举现有标签页。浏览器级连接失败仍进入原有 stop/start 重试。
- 紫鸟初始化期间首次读取 `latest_tab` 也可能与 target 销毁/替换竞态；TEMU 对 `Set changed size during iteration` 和 `No such target id` 进行最多 3 次首次接管重试，再进入正常登录循环。
- TEMU 标签页恢复不再只覆盖登录阶段：店铺上下文切换、接口 `run_js(fetch)`、区域授权导航和浏览器文件下载统一通过账号级 `ctx.page` 执行；断联恢复后立即更新 `ctx.page`，后续步骤继续使用替代 Tab。业务阶段单浏览器累计最多恢复 3 次。
