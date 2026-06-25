# 变更记录

## 2026-06-26

- 修复 TEMU 卖家中心默认扫码登录页识别：`text=手机号登录` 未命中时使用标准化文本 XPath 兜底；点击后必须确认手机号和密码框出现，否则记录 Tab 切换失败并重试，不再误报“等待保存密码”。
- 修复 B27/B28/B29 主账号启动后停在 `agentseller.temu.com/` 的登录超时：启动状态机将普通 agentseller 页面导航回卖家中心资金明细入口；新增账号特有默认落地页回归测试。

## 2026-06-25

- 修复 SHEIN 首次标签页接管红灯：把 `Set changed size during iteration` / `No such target id` 纳入连接竞态，普通 SHEIN 和申合首次 `latest_tab` 读取失败时等待 1 秒并最多重试 3 次。
- 增加 SHEIN 原生标签页恢复试验版：普通 SHEIN/POP/A1B 共享登录和 A1Y-A4Y 申合均以 `existing_only()` 接管紫鸟浏览器，断联时优先 `tab.reconnect(wait=1)`，target 丢失时按业务域名选择替代标签页。
- 申合浏览器请求增加可更新页面引用，列表和报账单导出在替换 tab 后继续复用新页面；单浏览器恢复上限为 3 次，耗尽后仍走原清理和重试。
- 新增普通 SHEIN attach/reconnect/替代 tab、申合 attach/reconnect/浏览器请求替代 tab 回归测试；SHEIN 定向测试 40 项通过。
- 修复部署机 `Set changed size during iteration` / `No such target id`：首版在 `existing_only()` 后仍调用 `new_tab()`，与紫鸟 target 销毁事件竞态；现改为普通 SHEIN 和申合均复用 `latest_tab`，导航失败进入原生重连循环。
- 新增两项“不得创建新 target”回归测试；任务配置校验通过，完整测试 165 项通过。
- 根据全量日志 `run_20260625_002325.log` 修复 TEMU 长批次仍断联：调度层将 `temu_fund_details` 有效 worker 固定为 1，运行计划会显示 `并发=1`。
- TEMU 停止浏览器后新增锁内 3 秒冷却，避免紫鸟异步关闭期间立即启动下一账号。
- 新增 TEMU 调度串行、运行计划有效并发和停止后冷却回归测试；定向测试 `54 passed`，完整测试 `149 passed`。
- 审查后将固定冷却升级为真实会话确认：保存 `debuggingPort` 并轮询端口关闭；关闭超时使任务失败并阻断后续 TEMU 启动。
- 修复混合平台性能回归：TEMU 使用专用作业锁，纯 TEMU 批次仍为单 worker，其他平台任务不再因包含 TEMU 而全局串行。
- 审查修复验证：TEMU 与主调度定向测试 `61 passed`，完整测试 `156 passed`。
- 根据 `run_20260625_005750.log` 修复首次控制绑定随机断联：TEMU 复用紫鸟现有 `latest_tab`，不再立即 `new_tab`；断联时最多 3 次重绑同一 `debuggingPort`。
- 当前未推送修复验证：TEMU 与主调度定向测试 `63 passed`，完整测试 `158 passed`。
- 复核 DrissionPage 4.1.1.4 官方文档和源码后，删除“重新构造同端口 Chromium”的伪重连；改用 `tab.reconnect(wait=1)`，原 target 消失时重新枚举 TEMU Tab，并启用 `existing_only()` 防止接管失败时启动普通 Chrome。
- 官方重连修复验证：TEMU 与主调度定向测试 `64 passed`，完整测试 `159 passed`。
- 补齐 TEMU 初始 target 竞态：首次 `latest_tab` 获取支持有限重试，新增识别 `Set changed size during iteration` / `No such target id`；TEMU 与主调度定向测试 `67 passed`，完整测试 `166 passed`。
- 修复 TEMU 全流程断联恢复：`Chromium(existing_only)` attach 支持 target 竞态重试；接口 fetch、文件下载、店铺导航和区域授权恢复后更新 `ctx.page`；首次恢复再次撞 target 切换时继续使用剩余恢复预算。TEMU 与主调度定向测试 `73 passed`，完整测试 `174 passed`。

## 2026-06-24

- 分析 `run_20260624_223608.log`：8 个 TEMU 账号在 `max_workers=2`、`ziniu_auth_concurrency=1` 下仍有 B23/B20 失败；首次页面断联后出现连续 `127.0.0.1:16851 Read timed out`，最终串行补跑仍无法恢复，确认问题不是单账号权限或店铺数据。
- 修正 TEMU 并发边界：单账号从 `startBrowser`、登录、店铺/区域导出到关闭浏览器全程持有统一鉴权槽，不再在 `userInfo` 验证后释放。
- TEMU `stopBrowser` 改为校验成功状态，失败等待 1 秒并最多重试 2 次；启动阶段异常和正常关闭复用同一清理函数。
- 新增 TEMU 端到端持锁与停止浏览器重试回归测试；TEMU 与主调度定向测试 `51 passed`，完整测试 `146 passed`。
- 修复普通 TK 断联恢复审查问题：`stopBrowser` 只有返回 `statusCode=0` 才视为成功，失败最多尝试 2 次；失败日志不输出 `browserOauth`。
- 共享浏览器启动最终失败的补跑改为按账号批次执行，一次浏览器覆盖该账号失败模块；普通模块失败仍保留单模块补跑。
- 修复普通 TK 总调 `run_20260624_163559.log` 暴露的 C1 断联残留：共享浏览器上下文返回前失败时使用已取得的 `browserOauth` 主动 `stopBrowser`，共享启动按账号级 `retry_count` 重试，最终失败展开为真实模块结果供串行补跑识别。
- 验证：新增启动清理、共享启动重试、真实任务失败结果和最终补跑回归测试 4 项；完整测试 `143 passed`。
- 将 E1E2 Bills 页从固定等待/自写 `readyState` 轮询升级为 DrissionPage 原生 listener + wait：导航前监听 Seller API，等待 URL/document，并直接复用监听包 seller 数据。
- 保留监听超时 fetch 降级和页面刷新短重试；新增监听顺序、超时清理、seller 信息复用回归测试。
- 本机真实运行确认 E1/E2 均命中 Seller API listener；运行日志会明确打印监听命中或 fetch 回退来源。
- 统一 Git remote：`origin` 改为 `https://github.com/amzzigz/getFullFianceData.git`，移除临时 `full` 别名，停止使用旧 `getFianceData` 仓库。
- TEMU 登录接入统一紫鸟鉴权槽，并把锁范围延长到卖家中心会话验证成功；`max_workers=2` 时登录阶段按 `ziniu_auth_concurrency=1` 串行，验证成功后的导出仍可双路并发。
- TEMU 启动/登录阶段发生页面断联或超时时，使用已取得的 `browserOauth` 调用 `stopBrowser` 并关闭页面，避免残留浏览器导致失败补跑继续断联。
- 验证：TEMU 登录测试 26 项通过；调度、紫鸟并发配置和启动参数相关测试 40 项通过。
- A1Y-A4Y 申合报账单接入统一紫鸟鉴权槽，锁覆盖到申合目标页面确认成功；启动阶段断联或超时会在函数返回前停止紫鸟浏览器并关闭页面。
- 验证：申合新增回归测试 2 项通过；申合、TEMU、主调度及紫鸟并发相关测试合计 68 项通过。
- 修正申合并发回归：上一版在目标页面出现后释放槽位，导致 A1Y 导出时 A2Y 启动浏览器并打断连接，失败重试又反向打断 A2Y；现将单个申合账号从启动到关闭浏览器完整串行。
- 新增申合端到端锁范围测试；申合、普通 SHEIN 鉴权、TEMU、主调度和紫鸟并发相关测试合计 74 项通过。
- 分析 `run_20260624_142547.log`：日志仍出现 A1Y/A2Y、A3Y/A4Y 交叠，行为不符合 `1b09b4a` 的端到端串行约束；记录为部署版本待核对，不继续基于旧行为叠加申合修复。
- A21POP/A23POP 资金流水币种 `info: []` 改为业务无数据并跳过重试；其他账号仍按失败处理。

## 2026-06-22

- 标注当前 SHEIN 最稳定导出模式：`dd5b43a` 后按账号共享紫鸟登录、复用账号级 cookie、模块阶段并发导出的方案，经新环境确认效率提升接近一倍且输出文件完整。
- 修复紫鸟异常恢复后的误报：当 `getBrowserList` 成功但返回空浏览器环境列表时，不再继续按账号匹配并报 `account not found`；账号匹配阶段提示浏览器环境列表为空。
- 合并 E1E2 稳定 SHA `7dc6ee8` 的关键行为：健康检查不再因为 `browserList=[]` 主动杀紫鸟重启，同时保留 SHEIN 已验证稳定的运行中紫鸟路径优先策略。
- 验证：`py -3 -m pytest tests\test_ziniu_start_payload.py -q` 通过 7 项。
- 补充 TikTok/E1E2 接手文档：记录普通 TikTok 与 E1E2 的账号源、任务分流、邮箱登录分支、美国 Bills 页导出链路和 30 秒现场验证标准。
- 修复 E1E2 运行不稳定：`start_tiktok_browser()` 接入统一紫鸟鉴权并发槽，避免两个 E1/E2 账号同时打 `127.0.0.1:16851/startBrowser`。
- 修复 E1E2 业务口径：`22008000/暂无数据可导出` 返回 `no_data` 状态，避免被当作失败补跑。
- 根据低配环境失败特征进一步收紧：`tiktok_email.income` / `E1E2` job 强制端到端串行，运行计划显示实际 `并发=1`。
- 验证：低配串行回归和计划输出测试先红后绿；相关 54 个定向测试通过；实跑 `tiktok_email_income` 得到 E1 无数据、E2 成功下载、失败数 0。

## 2026-06-16

- 创建 `project-md/` 项目上下文文档。
- 记录 A21POP/A23POP `pop_balance_records` 币种为空排查结论。
- 从生产 TEMU 账号池删除 `B2/B3/B5/B6/B7运营账号2`。
- 修复 `pop_funds` 输出平台硬编码为 `shein` 导致 POP 提现明细落入 SHEIN 目录的问题，并新增回归测试。
- 修复 SHEIN 新环境登录不稳定：`auth_login` 支持目标页参数，SHEIN/POP 财务模块传入各自 `target_page`，避免只停在主站 home 时就开始请求 GSFS/MWS 接口；新增 `tests/test_shein_target_auth.py`。

## 2026-06-17

- 核验 TEMU 批量导出日志和本地输出：终端“文件数: 3/输出文件=18”是展示截断；但本地 xlsx 与日志宣称完成数对比后确认存在真实缺文件。
- 记录 TEMU 批量导出完整性问题：B27/B28/B29、B30/B31/B32 组当前输出目录未找到对应 xlsx，B1 ANDREILEE 和 B2 FaceTrue 各缺 2 个区域文件。
- 修复 TEMU 资金明细导出完整性防线：主调度汇总读取结构化 `data.outputs`；TEMU 成功结果返回前校验所有区域文件存在；卖家中心下载增加 `2000000/导出任务未完成` 轮询。
- 修复新环境 SHEIN 总调并发 2 下紫鸟启动不稳定：`auth_login` 底层启动浏览器段改为单通道，并新增 `tests/test_shein_auth_serialization.py` 回归测试。
- 增加 `runtime.ziniu_auth_concurrency`：默认 1，高配环境可设 2；启动计划和 run summary 会记录当前紫鸟鉴权并发。
- 优化 SHEIN/POP/A1B 总调性能：账号批处理先共享登录并 warm-up 子系统页面，再复用同一份 cookie 并发跑账号内模块；新增账号内并发配置 `runtime.account_module_concurrency` 和对应测试。

## 2026-06-18

- 修复新环境 SHEIN 共享登录断开后的失败放大：识别 `与页面的连接已断开` 并快速重试；共享鉴权失败时降级为模块级鉴权，避免同账号所有模块直接复用失败结果。
- 修复新环境 SHEIN 共享登录误判：登录页 URL 不再算 warm-up 成功；进入登录页后不再循环刷新目标页，避免保存登录态/登录按钮识别被打断。
- 修复新环境 SHEIN 降级路径重复开关紫鸟：模块级成功登录后写入账号级 cookie 缓存；共享鉴权失败后的模块级降级强制串行，降低 `page disconnected during login` 复发概率。
- 增加总调末尾失败补跑：`runtime.final_failed_rerun_count` 控制补跑轮数，默认示例为 1；补跑串行执行并替换最终汇总结果。

## 2026-06-19

- 增加本地运行日志：`main.py` 将每次运行的 stdout/stderr 同步写入 `logs/runs/run_*.log`，并在启动时打印日志路径；新增 `runtime.save_run_log` 配置。
- 修复紫鸟 V6 后台被手动杀掉后无法自动启动：总调将紫鸟安装目录/API 地址/端口写入环境变量，helper 支持读取这些配置并按配置端口启动客户端；新增 F/E 盘安装目录候选。
