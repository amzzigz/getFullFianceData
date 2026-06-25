# 财务数据采集

这是一个全新的财务数据采集项目，开发测试在本机执行，上线后迁移到新电脑运行。

## Git 同步原则

- 提交代码、任务配置、bat 入口、工具脚本和部署说明。
- 不提交导出文件、运行抓包、日志、HAR、密钥文件。
- 项目路径不强制绑定 E 盘；配置默认使用项目相对路径，换电脑后可以放在 `E:\自动化\财务`，也可以放在其他盘。

## 目录

```text
财务/
  main.py
  env_scan.py
  install.bat
  run.bat
  requirements.txt
  DEPLOY.md
  config/
    local.json
    prod.json
    accounts.local.json
    accounts.prod.json
    tasks.json
    secrets.example.json
  src/
    finance_crawler/
  output/
  logs/
```

## 两套配置

- `config/local.example.json`：本地开发测试配置模板。
- `config/prod.example.json`：上线实际环境配置模板。
- `config/accounts.local.example.json`：本地测试账号组模板。
- `config/accounts.prod.example.json`：上线账号组模板。
- `config/local.json`、`config/prod.json`、`config/accounts.*.json`：每台电脑自己的真实配置，不提交 Git。
- `config/secrets.example.json`：敏感配置模板，真实 `secrets.*.json` 不提交。

## 常用命令

```bash
install.bat
py -3 env_scan.py --env local
py -3 env_scan.py --env prod
py -3 main.py --env local
py -3 main.py --env prod
```

## 新电脑部署

先安装 Git、Python 3.11+、Chrome、紫鸟浏览器。然后拉取仓库并运行 `install.bat`。

详细步骤见 `DEPLOY.md`。

## 配置重点

除了 Chrome、紫鸟安装目录、紫鸟账号密码，还要配置：

- 紫鸟鉴权脚本路径
- 紫鸟 webdriver host/port
- 平台账号组和店铺名
- 输出目录、下载目录、日志目录
- 并发数、登录超时、请求超时、重试次数
- 是否保留浏览器
- 任务启用开关
- 平台日期口径
- 新电脑下载目录权限和磁盘空间
