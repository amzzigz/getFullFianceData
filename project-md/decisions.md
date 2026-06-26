# 决策记录

## 2026-06-26

- 面板执行用户拖入的 bat 时不改写 bat 内容；改为按 bat 内 `cd /d "%~dp0..."` 的回退层级保存副本，使 bat 自己计算出的项目根保持正确。普通 TEMU/TK/SHEIN bat 的 `%~dp0..` 保存到 `output`，E1E2/POP 这类 `%~dp0..\..` 保存到 `output/panel`。
- 财务控制面板首版不重写爬虫调度和平台导出逻辑，只作为本地网页壳调用现有 `main.py` 参数入口；这样账号、模块、周期、诊断模式均沿用已验证路径。
- 控制面板首版使用 Python 标准库 HTTP 服务和原生 HTML/JS，不引入 FastAPI、前端构建或数据库依赖；运行记录先保存到 `output/panel/runs`，后续确认需求后再迁移 TaskLauncher 的 APScheduler/SQLite 计划执行层。
- 面板业务日志不直接暴露 DrissionPage、webdriver 等专业细节；常见技术错误转换为“浏览器连接中断”“账号登录未完成”“平台仍在生成文件”等业务人员可理解文本。
- 控制面板不再把定时功能塞进左侧筛选栏；左侧只承担“本次运行范围”，右侧主区域用 Tab 切换 `运行日志` 和 `定时计划`，避免账号/模块列表较长时侧栏不可用。
- 控制面板定时执行不复用左侧筛选状态。业务人员在定时页拖入本地 bat/cmd，面板保存一份副本到 `output/panel`，计划触发时执行该副本，避免为了总调 bat 继续扩张左侧栏。
- bat 上传按原始二进制保存，不用文本解码，避免中文路径或非 UTF-8 编码 bat 被浏览器转码破坏。
- bat 副本必须放在 `output/panel` 而不是更深目录，以兼容现有 bat 中常见的 `cd /d "%~dp0..\.."` 项目根定位写法；执行时使用 `stdin=DEVNULL` 防止 `pause` 卡住后台任务。
- 已保存到旧 `output/panel/bat_jobs` 的计划保留兼容：执行前自动复制到 `output/panel` 后再运行，避免用户需要逐个删除重建计划。
- 控制面板定时执行首版继续保持轻量：计划存到 `output/panel/schedules.json`，由面板进程每 20 秒检查每日/每周/每月触发点；同一时间已有任务运行时不再并发启动新任务。
- 从隔离 worktree 启动控制面板时必须传 `--project-root E:\自动化\财务`，否则只能读取 worktree 内的公开/空配置，账号池会显示为空或不完整。
- TEMU 登录修复遵循最小改动：不引入通用页面发现框架，只补充“手机号登录”定位兜底和切换结果验证；找不到或未切换成功时立即返回外层重试。
- TEMU 资金明细启动成功标准只认卖家中心 `userInfo`，不把普通 `agentseller.temu.com` 首页视为成功；普通 agentseller 落地页直接返回资金明细入口，authentication/login 页面继续沿用现有登录处理。

## 2026-06-25

- 浏览器稳定性修复遵循最小化原则：仅把跨 SHEIN 链路完全相同的“首次 `latest_tab` 有限重试”放入 `auth.py` 小助手，不抽象整套跨平台浏览器管理框架；TEMU 保留其平台专用恢复和清理语义。
- `existing_only()` 模式下禁止 SHEIN 再调用 `browser.new_tab()`。紫鸟启动时已提供现有 target，额外创建 tab 会与 target 销毁事件竞争；普通 SHEIN 和申合均复用 `latest_tab`，并在同一 tab 上导航。
- SHEIN 断联恢复沿用 TEMU 已核对的 DrissionPage 4.1.1.4 官方语义，但保持平台隔离：普通 SHEIN/POP/A1B 在 `auth.py` 恢复 `geiwohuo.com` 标签页，A1Y-A4Y 申合在自身模块恢复 `shenhe888.com` 标签页。
- 恢复顺序固定为当前 tab `reconnect(wait=1)`、按目标域名枚举现有 tab、符合目标域名或空白页的 `latest_tab`；最多 3 次，禁止通过重新构造普通 Chrome 掩盖紫鸟会话失效。
- 原有 SHEIN 账号批处理、账号级 cookie 复用、`ziniu_auth_slot()` 锁范围、stopBrowser 清理和最终失败补跑保持不变。
- TEMU 稳定性不能只依赖函数内部的 `ziniu_auth_slot`；该锁保护单次尝试，失败返回后线程池可让其他账号插入，再回到原账号重试。`temu_fund_details` 必须在调度层固定单 worker，使账号及其重试成为连续执行单元。
- 紫鸟 `stopBrowser statusCode=0` 只代表停止请求被接受，不代表浏览器环境已完全退出；固定 3 秒冷却改为轮询本次 `debuggingPort` 关闭，最长 10 秒。
- `getBrowserList` 返回环境池而非运行会话，不用于判断关闭完成。调试端口超时未关闭时设置进程级启动阻断，宁可停止后续 TEMU 账号，也不能在残留浏览器上继续启动。
- 纯 TEMU 批次有效 worker 为 1；混合平台任务使用 TEMU 专用作业锁，只保证 TEMU 账号互斥，其他平台仍按原 `max_workers` 执行。
- DrissionPage 的“与页面的连接已断开”不等同于紫鸟浏览器会话已失效。TEMU 首次绑定使用 `existing_only()` 和现有 `latest_tab`；连接瞬断优先调用官方 `tab.reconnect(wait=1)`，target 消失才重新枚举 TEMU Tab，浏览器级连接失败才停止并重启环境。
- `Set changed size during iteration` 和 `No such target id` 在紫鸟启动阶段按 target 切换竞态处理；首次 `latest_tab` 最多重试 3 次，不因一次 target 销毁立即停止整个浏览器。
- TEMU 业务阶段禁止把裸 `page` 长期传递给接口和下载链路；统一传账号浏览器上下文，由页面操作入口在断联时替换 `ctx.page`。恢复次数按单浏览器会话累计，避免每个接口各自重试 3 次形成无界恢复。

## 2026-06-24

- 普通 TK `stopBrowser` 清理不能只以“调用未抛异常”视为成功，必须检查 `statusCode=0` 并做有限重试；最终失败补跑仅对带共享启动失败标记的结果按账号批次执行，其他模块失败继续使用原单模块补跑。
- 普通 TK 共享浏览器若在 `start_tiktok_browser()` 返回前断联，必须复用 TEMU/申合的启动阶段清理模式，使用已取得的 `browserOauth` 停止浏览器；共享浏览器初始化按账号级 `retry_count` 重试，最终失败必须转成真实模块任务结果，不能继续使用补跑器无法识别的 `tiktok_account_batch` 作为唯一结果。
- E1E2 部署机稳定性不能继续依赖固定 `sleep(3/4)` 或自写 `readyState` 计数；Bills 页以 `/api/v3/seller/common/get` 数据包作为业务就绪信号。DrissionPage `wait.url_change()` / `wait.doc_loaded()` 只负责导航门槛，监听包负责确认 SPA 业务数据已加载。
- Seller API 监听包中的 JSON 直接用于提取 `seller_id`，避免页面刚稳定后立刻再发一次相同 fetch。监听器必须在导航前启动并在 `finally` 中停止。
- DrissionPage `页面被刷新` 属于页面导航竞态，只在浏览器 JS 请求边界做最多 3 次短重试；其他业务错误、登录错误和接口错误不扩大重试。
- GitHub 正式仓库统一为 `amzzigz/getFullFianceData`，本地 remote 名称统一为 `origin`；旧 `amzzigz/getFianceData` 已废弃，不再推送。
- TEMU 原“会话确认后释放鉴权槽、资金明细继续按 `max_workers` 并发”的决策废弃。`run_20260624_223608.log` 显示浏览器导出阶段与下一账号 `startBrowser` 重叠仍会触发页面断联，并把紫鸟本地 `16851` 拖入持续读超时；TEMU 资金明细必须从启动到 `stopBrowser` 端到端占用 `ziniu_auth_slot`。
- TEMU 正常关闭和启动失败清理都必须验证 `stopBrowser statusCode=0`；失败时等待 1 秒并最多尝试 2 次，日志不得输出 `browserOauth`。
- TEMU 在 `start_temu_browser()` 返回前失败时必须主动停止已启动的紫鸟浏览器并关闭页面，不能依赖外层 `ctx` 清理，否则失败补跑会复用断开的环境。
- A1Y-A4Y 的申合报账单不能照搬 TEMU 的“登录后释放槽位”策略。部署机证实下一账号启动紫鸟会打断前一申合账号仍在运行的页面接口，因此申合任务必须从启动到关闭浏览器端到端占用 `ziniu_auth_slot`；上一版“页面确认后释放并并发导出”的决策废弃。
- A21POP/A23POP 的 `pop_balance_records` 当前未启用，币种接口 `info: []` 对这两个账号按 `no_data` 处理；该规则按账号精确限定，不扩展到其他 POP 账号。

## 2026-06-22

- 紫鸟 `getBrowserList` 返回 `statusCode=0` 但 `browserList=[]` 在财务总调中不是正常“账号不存在”，而是客户端/成员态/环境权限异常；账号匹配阶段应给出客户端异常提示，避免把剩余账号全部误归类为未找到该账号。
- 健康检查阶段不再因为 `browserList=[]` 主动杀紫鸟重启。`7dc6ee8` 的 E1E2 稳定性差异集中在这个行为；低配/占线环境里自动 kill/restart 容易放大断联。真正端口无响应时仍允许按配置/进程路径自动启动紫鸟。
- 紫鸟安装目录配置只应用作自动启动兜底，不应覆盖当前正在运行的紫鸟实例；helper 检测启动目录时优先使用运行进程路径，再使用 `ZINIAO_INSTALL_DIR`，以贴近稳定版 `b7262e1c` 的运行态。
- `dd5b43a` 之后的 SHEIN/POP/A1B 总调模式为当前最稳定版：按账号共享紫鸟登录和账号级 cookie，模块导出阶段再并发；新环境验证效率接近翻倍且输出文件完整。后续不要轻易改回逐模块独立鉴权，也不要让配置安装目录覆盖运行中的紫鸟实例。
- 接手 TikTok/E1E2 时以当前代码和 `project-md` 为准，记忆只作为旧现场背景；发现 `project-md` 缺少 TikTok/E1E2 细节，因此补入长期文档。
- E1/E2 继续保持独立邮箱登录分支：账号源为 `tiktok_email`，任务平台为 `E1E2`，不扩散到普通 TikTok C 系列账号。
- 对 E1/E2 登录问题的成功标准保持严格：退出登录后 30 秒内进入非 login 页面才算现场成功；单元测试只证明分支逻辑和选择器顺序。
- TikTok `start_tiktok_browser()` 与 SHEIN 共享登录一样会触发紫鸟本地 `startBrowser`，必须受 `runtime.ziniu_auth_concurrency` 约束；任务层可以并发排队，但浏览器启动/登录段默认串行。
- E1E2 `code=22008000` / `暂无数据可导出` 是业务无数据，不是技术失败；应计入 `no_data`，并跳过最终失败补跑。
- 因失败日志来自低配环境，E1E2 不能依赖高配本机实跑结论；该任务默认端到端串行，优先稳定和可解释日志，不追求两个账号并发。

## 2026-06-16

- `pop_balance_records` 排查先做只读接口诊断，不触发导出下载。
- A21POP/A23POP 的币种为空先按账号业务状态解释，不做代码兜底导出，因为前端同样依赖币种列表，强制 `CNY` 查询也没有返回明细 `info`。
- 按用户要求从 `config/accounts.prod.json` 移除 `B2/B3/B5/B6/B7运营账号2`，保留同组 `账号1`。
- `pop_funds` 继续复用 SHEIN 提现接口实现，但实现内部不得硬编码输出平台为 `shein`，应使用任务配置的 `platform`。
- SHEIN 总调模拟登录不能只以 `#/home/` 作为 GSFS/MWS 模块成功标准；模块应把自己的 `target_page` 传给鉴权层，让紫鸟浏览器实际进入目标子系统页后再提取 cookie。鉴权缓存按目标页区分，避免复用只初始化了主站 home 的 cookie。
- 紫鸟 `startBrowser` 参数由 `tools/ziniu_auth_login_extracted.py` 统一生成；当 `getBrowserList` 同时返回 `browserId` 和 `browserOauth` 时两者都传，并过滤空字段，兼容新环境返回 `may be missing arguments` 的严格校验。
- 新环境总调可以保持任务级并发，但紫鸟本地 webdriver/startBrowser 入口需要串行化；并发打 `127.0.0.1:16851` 会放大 Read timeout、`startBrowser failed after retry: None` 和浏览器路径异常。
- 紫鸟鉴权并发由 `runtime.ziniu_auth_concurrency` 控制，默认 1；不要自动按机器性能猜测，高配稳定环境可人工设为 2。
- SHEIN/POP/A1B 的账号批处理采用两阶段：浏览器登录和子系统页面 warm-up 在账号级串行完成并提取一次 cookie；接口导出阶段再按 `runtime.account_module_concurrency` 做账号内模块并发。第一版不做同浏览器多 tab 并发 warm-up，避免 DrissionPage/ZiNiao 页面状态互相抢占。
- 新环境如果 DrissionPage 报 `与页面的连接已断开`，共享登录不再等满超时；应快速失败触发重试。若共享鉴权最终失败，降级到模块级鉴权，优先保住部分模块产出。
- SHEIN 登录页不能只凭 cookie 长度判定成功；`login` URL 必须视为未登录。warm-up 已请求目标页后，如果跳到登录页，应停在登录页点击/等待，不再每轮重新 `page.get(target_page)` 造成刷新。
- SHEIN/POP/A1B 同账号不同模块的 cookie 按账号级复用；目标页只用于首次 warm-up，不应让每个模块因 `target_url` 不同重复开关紫鸟。共享鉴权失败后的降级路径以稳定优先，强制账号内模块串行。
- 总调末尾可用 `runtime.final_failed_rerun_count` 做失败补跑；补跑只处理最终失败项，不处理 `no_data`，并且串行执行以优先适配低配新环境的紫鸟稳定性。

## 2026-06-19

- 每次运行的终端日志应落到 `log_root/runs/run_*.log`；日志采用 stdout/stderr tee，不替换现有控制台输出，也不替代 JSON run summary。默认开启，必要时用 `runtime.save_run_log=false` 关闭。
- 紫鸟 V6 自动重启不能依赖仍在运行的进程路径；总调需把 `software.ziniu_install_dir`、`ziniu_webdriver_host`、`ziniu_webdriver_port` 显式传给 `tools/ziniu_auth_login_extracted.py`，helper 启动命令必须使用同一端口。
