# CS-Scout v2.1.1-alpha.3 发布说明

这是 2.1.1 的第三个公开测试版，重点修复完美平台手动用户名解析和 Windows Python 安装门槛。

## 本次修复

- 完美平台手动模式不再借用 5E 用户搜索；现在使用当前完美平台登录会话的官方玩家搜索，支持中文 Steam 昵称全匹配和 SteamID64。
- 对同名玩家不再猜测账号；无法唯一确认时会提示改用 SteamID64，避免下载错人的 Demo。
- Windows 玩家不再需要预先安装 Python。没有可用的 64 位 Python 3.11/3.12 时，安装程序会自动准备私有 Python 3.12.10。
- 自动下载只使用固定的 `python.org` 地址，并校验 SHA-256 和 Python Software Foundation 数字签名。
- 已有 Python 3.13、PyCharm 解释器和系统 PATH 不会被修改；自动运行环境只供 CS-Scout 使用。
- 点击另一名玩家的回放按钮时，时间轴会回到 0 秒并自动从头播放；重复点击当前玩家不会打断进度。

## Windows 下载与更新

下载以下三个资产：

```text
CS-Scout-Windows-x64-v2.1.1-alpha.3.zip
CS-Scout-Windows-x64-v2.1.1-alpha.3.zip.sha256
SHA256SUMS.txt
```

不要下载 GitHub 自动生成的 `Source code` 压缩包。停止旧版，把 Alpha 3 完整解压到新目录，
然后依次双击：

```text
windows\Install-CS-Scout.cmd
windows\Start-CS-Scout.cmd
```

不要覆盖 Alpha 2 的旧目录。Demo 缓存和分析结果保存在 `%LOCALAPPDATA%\CS-Scout`，更换程序目录不会丢失。

## 使用提醒

- 完美平台中文用户名按 Steam 昵称全匹配；出现同名时请使用 17 位 SteamID64。
- 第一次安装需要联网下载 Python 运行环境和固定依赖，之后可重复运行安装程序修复环境。
- 这是预发行测试版；自动模式暂不可用时，可以切换到同平台的“手动”模式继续使用。

## 验证

- 新增完美平台中文昵称、精确匹配、同名拒绝和 SteamID64 回归测试。
- Windows 发布包继续执行严格文件白名单、PowerShell 5.1 语法、依赖、地图资源、敏感文件和 ZIP 清单检查。
- 安装器验证自动 Python 下载地址、固定 SHA-256、官方签名、当前用户安装以及不修改 PATH/Launcher。
- 发布资产提供独立 SHA-256 校验文件。
