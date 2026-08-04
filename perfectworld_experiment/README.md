# CS-Scout 完美平台自动侦察

这个目录实现完美平台的数据链路，并已接入 CS-Scout 原有网页：

> 用户匹配到完美平台对局 → 自动识别当前地图和本局阵容 → 以只读用户名显示对手 → 用户确认并点击开始分析 → 查询这些人的历史对局并下载已有 Demo → 复用 CS-Scout 回放分析与网页展示。

匹配到当前比赛只是自动识别对手的触发点。程序**不会等待全员进入 CS2 或当前比赛结束**，分析的数据来自对手已经存在的历史比赛；用户只需确认名单并点击一次，不需要输入用户名、SteamID、地图或 Token。

## 已实现

- 解码当前完美平台桌面客户端的本地日志，读取游戏开始通知、当前 `match_id`、地图、阵容、分队/槽位信息；
- 从本地日志中的已登录会话恢复 `access_token`、`Pwa-Jt` 和登录账号 SteamID，只在进程内存中使用，不写入文件和输出；
- 调用官方 `/user/playersInfo` 接口补全本局玩家昵称和 SteamID；
- 默认自动选择对手；无法获得分队信息时退化为分析除自己以外的玩家，也可显式分析整局；
- 为每名目标查询该地图的历史对局，签名并下载已有 Demo；
- 多名目标共享同一历史比赛时，Demo 只下载、解压一次；
- 复用现有 CS-Scout Demo 解析、K/D、AWP 持有率、路径与投掷物回放；
- 原 CS-Scout 网页切到“完美平台”后会持续等待对局，识别后把对手显示在不可编辑的用户名输入框中，点击“开始分析”后显示进度与玩家回放。

5E 流水线保持不变；`server/web_server.py` 只在用户切到完美平台时按需启动本目录的监听服务。源码运行时，完美版 Demo 和输出默认分别位于 `perfectworld_experiment/demos/` 与 `perfectworld_experiment/output/`；Windows 玩家包通过环境变量把它们放到 `%LOCALAPPDATA%\CS-Scout\perfectworld`，两种情况都不会覆盖 5E 数据。

## 已还原的客户端协议

- Web API 使用 `a=20000`、六位随机数 `r`、Unix 秒 `t` 和签名 `s`；签名复用官方安装目录中的 `PvpAlive.dll` `swapData` 导出。
- 加密响应 `data.e/data.t` 使用 AES-256-ECB 与 PKCS#7 解密。
- OSS 下载头 `X-PWA-Signature` 使用 AES-128-CBC；公网 IPv4、时间戳与 SteamID 共同参与签名。
- Demo 地址为 `/csgo/demo/{match_id}_{cup_id}.dem`。
- 当前桌面客户端版本验证基线：`1.0.26073111`。

## 安装

项目现有 `.venv` 已包含 5E 运行环境，只需补装完美版的加密依赖：

```powershell
.\.venv\Scripts\python.exe -m pip install -r .\perfectworld_experiment\requirements.txt
```

本机需要安装并登录完美世界竞技平台。签名桥默认读取：

```text
C:\Program Files (x86)\perfectworldarena\plugin\PvpAlive.dll
```

安装位置不同时可设置 `CS_SCOUT_PWA_DLL`。

## 使用

推荐从仓库根目录启动合并后的主网页：

```powershell
$env:CS_SCOUT_LOCAL_MODE='1'
.\.venv\Scripts\python.exe .\server\web_server.py
```

浏览器打开 `http://127.0.0.1:5000`，在左侧切换到“完美平台”。保持网页与完美平台打开，匹配到一场平台对局后，页面会自动填入对手；确认名单后点击“开始分析”。

原独立网页仍保留为协议调试入口：

```powershell
.\.venv\Scripts\python.exe -m perfectworld_experiment.web_server
```

也可以只运行自动命令行流程：

```powershell
.\.venv\Scripts\python.exe .\perfectworld_experiment\run.py auto --max-demos 3
```

默认只分析对手。调试整局时增加 `--all-players`。以下环境变量可调整本地服务：

- `CS_SCOUT_PWA_MAX_DEMOS`：每名目标的历史 Demo 深度；独立调试服务默认 3，合并主网页默认 6；
- `CS_SCOUT_PWA_DEMO_DIR` / `CS_SCOUT_PWA_OUTPUT_DIR`：完美模式的持久 Demo 缓存与输出目录；
- `CS_SCOUT_PWA_ALL_PLAYERS=1`：分析整局而非只分析对手；
- `CS_SCOUT_PWA_DISCOVERY_WORKERS`：并发查询玩家历史记录，默认 5，上限 5；
- `CS_SCOUT_PWA_DOWNLOAD_WORKERS`：并发准备 Demo，默认 6，上限 12；
- `CS_SCOUT_PWA_PARSE_WORKERS`：并行生成玩家数据，默认 2，上限 4；默认值会根据可用内存自动降低；
- `CS_SCOUT_PWA_HOST` / `CS_SCOUT_PWA_PORT`：默认 `127.0.0.1:5010`。

`probe` 和 `analyze` 的手动 SteamID/地图参数只保留为协议调试入口，不属于最终用户流程。

## 安全边界

- 不修改、注入或代理完美平台客户端；只读其本地日志和官方接口；
- Token、`Pwa-Jt`、签名与带签名下载 URL 不写入日志、JSON 或网页状态；
- 网页默认只监听 `127.0.0.1`，不应直接暴露到公网；
- 完美接口仅接受本机回环地址访问，完美输出与 5E 输出分目录保存。

## 后续稳定性验收

1. 使用真实完美天梯开局通知连续正确识别双方阵容、地图与对手；
2. 至少两个账号、三张地图的历史 Demo 发现、下载和解析稳定；
3. Token 过期、无历史 Demo、Demo 未发布与平台限流都有清晰状态；
4. 凭据和签名不出现在日志、输出 JSON或网页接口；
5. 原有 5E 测试持续全部通过。
