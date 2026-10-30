# AzerothCore Playerbot 玩家中心

为全新 AzerothCore Playerbot 服务部署的网站源码，包含账号注册与登录、角色列表、
GM 世界监控、机器人状态和机器人中文对话设置。

## 配套仓库

- 核心：<https://github.com/botao03/azerothcore-wotlk/tree/codex/personal-changes>
- 机器人模块：<https://github.com/botao03/mod-playerbots/tree/codex/personal-changes>
- 世界内容数据库：<https://github.com/botao03/azerothcore-playerbot-database>
- 全新部署说明：核心仓库的 `deploy/fresh-server/README.zh-CN.md`。

## 安装与启动

要求 Python 3.10+。Windows 用 `py -3` 创建环境，macOS/Linux 用 `python3`。

```text
python -m venv .venv
```

Windows 激活 `.venv\Scripts\activate`；macOS/Linux 激活 `source .venv/bin/activate`。

```text
python -m pip install -r requirements.txt
```

使用核心仓库的 `deploy/fresh-server/configure.py` 可同时生成游戏与网站配置。
也可以将 `config.example.json` 复制为 `config.json`，设置 `world_config` 为新服务的
`worldserver.conf` 路径，将 `world_snapshot` 指向游戏端生成的 CSV。

```text
python manage.py check
python manage.py run
```

`check` 只验证数据库结构，不建立游戏账号。`run` 在当前终端运行网站，用 Ctrl+C 停止。
新发布的管理入口不依赖 Linux 的 `/proc` 或 `fcntl`。
网站默认监听 `127.0.0.1:8089`；需要局域网访问时修改 `config.json` 的 `host`。

## 全新用户与机器人对话

不会导入旧账号、角色、会话、机器人记忆或 API key。
在新的 auth 数据库初始化后，可以通过网站注册新账号；GM 等级应由新服务管理员通过
worldserver 控制台按需授予。机器人对话设置只对当前区服 GM 等级 >= 3 的账号开放。

API key 在新网站页面重新填写，存放在私有的 `state/playerbots-ai.json`。
游戏模块的 `BotMinds.ApiKeyFile` 必须指向同一个文件。
聊天记忆由模块在新的 `run/` 目录中重新生成。

## 实时监控

游戏端 `Playerbots.WorldSnapshotPath` 和网站 `world_snapshot` 必须指向同一个文件。
快照无效或过期时页面明确显示数据库状态，不将历史位置当作实时状态。
普通账号只能查看自己的角色，GM 权限由后端按当前区服验证。

## 上传范围

仓库只包含应用源码、静态资源、配置示例、依赖清单和已有的离线测试。
`.venv/`、`state/`、实际 `config.json`、会话、日志和密钥均被忽略。
没有携带旧用户信息，也不需要修改游戏数据库结构。

现有离线测试使用模拟数据库：

```text
python -m unittest discover -s tests -v
```

若部署到公网，应配置 HTTPS，并设置 `secure_cookies = true`。
网站数据库凭据只在服务端读取，不通过静态文件路由公开。

## 来源与验证范围

从本机已运行的玩家中心源码整理发布，密码算法依据配套核心的 SRP6 实现。
本次发布保留现有业务行为，仅更换发布副本的启动入口与配置示例。
Windows/macOS 入口不代表已经在这两种操作系统完成实机验证。
