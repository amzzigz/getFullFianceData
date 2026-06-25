# 财务采集项目新电脑部署与验收

本文用于把项目部署到新的 Windows 电脑，并完成正式投入运行前的验收。

## 1. 部署完成标准

新电脑需要同时满足：

- Git、Python 3.11+、Chrome、紫鸟已安装。
- 紫鸟已登录，并能打开需要采集的 SHEIN、TEMU、TikTok 等店铺。
- 项目配置、账号池和紫鸟鉴权脚本已就位。
- `install.bat` 执行成功。
- 单账号实跑成功，文件能写入 `output\downloads\`。

## 2. 从旧电脑准备资料

部署前先从当前可运行电脑确认并备份以下内容：

- 紫鸟账号和店铺权限。
- `config\prod.json`
- `config\accounts.prod.json`（如果使用）
- `config\secrets.prod.json`（如果使用）
- `tools\` 下各平台账号池文件。
- 最新可用的 `tools\ziniu_auth_login_extracted.py`。

`config\prod.json`、账号配置、密钥配置通常不会提交到 Git，不能只依赖克隆仓库。

## 3. 安装基础软件

安装以下软件：

1. Git for Windows。
2. Python 3.11 或 3.12 64 位版，安装时勾选 `Add python.exe to PATH`。
3. Google Chrome。
4. 紫鸟浏览器。

安装后重新打开 PowerShell，检查：

```powershell
git --version
py -3 --version
```

## 4. 克隆项目

推荐使用短路径：

```powershell
git clone https://github.com/amzzigz/getFianceData.git E:\自动化\财务
cd E:\自动化\财务
```

项目大部分路径使用相对路径，也可部署到其他盘符。

## 5. 初始化本机配置

首次部署可从模板创建配置：

```powershell
Copy-Item config\prod.example.json config\prod.json
Copy-Item config\accounts.prod.example.json config\accounts.prod.json
Copy-Item config\secrets.example.json config\secrets.prod.json
```

重点核对 `config\prod.json`：

- `paths.output_root`、`paths.log_root`、`paths.download_root`
- 各平台账号池文件路径
- `paths.desktop_auth_path`
- `software.chrome_path`
- `software.ziniu_install_dir`
- `software.ziniu_webdriver_host` 和 `ziniu_webdriver_port`

账号池既可以写在 `config\accounts.prod.json`，也可以使用 `tools\` 下的平台账号池文本。真实密码和临时密钥只放在本机配置，不要提交 Git。

## 6. 准备紫鸟

1. 启动紫鸟并完成登录。
2. 确认新电脑已获得目标店铺权限。
3. 手动打开每个平台至少一个店铺，确认没有验证码、设备验证或权限提示。
4. 保持紫鸟运行，再执行安装与严格扫描。

如需 SHEIN、TEMU、TikTok 模拟点击登录，项目优先使用 `tools\ziniu_auth_login_extracted.py`；也可在配置中指向桌面的可用脚本。

## 7. 安装依赖并严格验收

双击或在命令行运行：

```powershell
.\install.bat
```

它会依次：

1. 检查 Python 版本。
2. 安装 `requirements.txt`。
3. 校验任务定义。
4. 用严格模式运行环境扫描。

严格扫描也可单独执行：

```powershell
py -3 env_scan.py --env prod --strict
```

扫描结果会写入 `output\env_scan\`。每个 `WARN` / `ERROR` 下方都有处理建议。

- 普通扫描：存在 `ERROR` 才返回失败，适合日常排查。
- 严格扫描：存在 `WARN` 或 `ERROR` 都返回失败，适合新电脑交付验收。

## 8. 运行项目自检

先运行不打开浏览器的检查：

```powershell
py -3 scripts\validate_tasks.py
py -3 scripts\smoke_plan.py --env prod
py -3 main.py --env prod --dry-run
```

然后对实际要使用的平台各选一个账号实跑。不要第一次就运行全部账号；先确认登录、日期范围、导出接口和本地文件均正常。

常用 BAT 入口位于：

- `shein\`
- `temu\`
- `tiktok\`
- `tiktok\E1E2\`
- `aliexpress\`

导出文件默认写入：

```text
output\downloads\
```

## 9. 配置 Windows 定时任务

浏览器自动化建议设置为“仅当用户登录时运行”：

1. 打开“任务计划程序”，创建任务。
2. 操作选择“启动程序”。
3. 程序填写 `cmd.exe`。
4. 参数填写：

```text
/c "E:\自动化\财务\目标平台\目标任务.bat"
```

5. “起始于”填写：

```text
E:\自动化\财务
```

6. 先手动运行一次计划任务，确认紫鸟窗口、日志和导出文件均正常。

## 10. 日常更新

更新项目后重新安装依赖并扫描：

```powershell
git pull
.\install.bat
```

正式运行前可再做一次：

```powershell
py -3 scripts\smoke_plan.py --env prod
```

## 11. 常见问题

### 未找到 `py`

重新安装 Python，勾选 PATH 和 Python Launcher，然后重新打开 PowerShell。

### 缺少账号池

查看扫描中的 `enabled_task_accounts`，填写对应 `config\accounts.prod.json` 或 `tools\` 账号池文件。

### 未检测到紫鸟或端口未监听

启动并登录紫鸟；若仍失败，核对 `config\prod.json` 的安装目录、主机和端口。

### 找不到紫鸟鉴权脚本

从可运行电脑同步最新脚本到 `tools\ziniu_auth_login_extracted.py`，或配置 `desktop_auth_path`。

### Git 工作区有改动

严格模式会提示该警告。确认改动已提交、已备份或确实属于该部署电脑后再交付。

### 磁盘空间不足或目录不可写

清理磁盘，或将输出路径改到有权限且空间充足的目录。建议至少保留 5 GB。

## 12. 最终交付清单

- [ ] `.\install.bat` 成功结束。
- [ ] `py -3 env_scan.py --env prod --strict` 无 WARN / ERROR。
- [ ] 任务定义和 dry-run 通过。
- [ ] 每个平台至少一个账号实跑成功。
- [ ] 导出文件可在 `output\downloads\` 找到。
- [ ] 业务人员知道如何运行 BAT、查看日志和环境扫描结果。
