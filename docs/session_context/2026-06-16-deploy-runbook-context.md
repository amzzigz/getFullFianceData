# 2026-06-16 部署与运行上下文

## 新电脑基础环境

推荐 Python：

```text
Python 3.11
```

Python 3.12 通常也可用，但项目最低推荐仍以 `prod.json` / 部署文档中的 `python_min_version` 为准。避免使用过新的 Python 作为生产默认，除非全量测试已在目标机器通过。

依赖注意：

- `requirements.txt` 已包含 `tzdata>=2024.1`。
- Windows 上如果缺少 `tzdata`，会出现 `ZoneInfoNotFoundError: Asia/Shanghai`，这不是系统时间未同步问题。

## 环境检查

相关文件：

- `env_scan.py`
- `tests/test_env_scan.py`

环境检查应覆盖：

- Python 版本
- 依赖包
- `tzdata`
- Git
- 紫鸟安装目录
- 配置文件存在性
- 输出、下载、日志目录权限

## 紫鸟通用运行约束

本项目自动化依赖紫鸟本地 WebDriver HTTP 服务：

```text
http://127.0.0.1:16851
```

官方 WebDriver 流程要点：

1. 启动前关闭紫鸟主进程。
2. 使用 `--run_type=web_driver --ipc_type=http --port=16851` 启动客户端。
3. 调用 `updateCore`，直到 `statusCode=0`。
4. 调用 `getBrowserList` 获取店铺浏览器列表。
5. 调用 `startBrowser` 启动指定店铺。

状态码参考：

- `0`：成功。
- `-10000`：官方文档定义为未知异常或处理中，需结合 action 判断。
- `-10003`：官方文档定义为登录失败；通常检查 WebDriver 权限、登录验证、企业登录账号密码。
- `-10013`：需要设备认证。

## 紫鸟诊断脚本

相关工具：

- `tools/test_ziniu_auth.py`
- `tools/test_ziniu_auth_hardcoded.py`

使用原则：

- `test_ziniu_auth.py` 适合跟随项目 helper 检查当前配置。
- `test_ziniu_auth_hardcoded.py` 是官方 HTTP 请求格式的最小 demo，适合拿到目标机器上独立验证。
- 不要把真实密码写入可提交文件；若临时硬编码测试，测试后应从提交范围排除或清理。

## 运行与验证习惯

常用命令：

```bat
py -3 env_scan.py --env prod
py -3 scripts\validate_tasks.py --env prod
py -3 main.py --env prod --dry-run
py -3 -m pytest -q
```

涉及日期周期时，补充：

```bat
py -3 main.py --env prod --dry-run --today YYYY-MM-DD
```

涉及真实平台登录、店铺切换或导出时，单测只能证明局部逻辑，最终仍需一次现场短跑验证。
