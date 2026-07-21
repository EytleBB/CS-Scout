# CS-Scout 2.0

> 面向 CS2 赛前准备的 5E Demo 分析与多回合浏览器回放工具。

CS-Scout 输入最多 5 个 5E 用户名和一张地图，自动检索、下载并解析历史
Demo，然后把每名玩家的移动路径、投掷物和死亡时间输出为 JSON。浏览器使用
Canvas 将多个回合叠加为同步回放，不在服务器端渲染热力图。

## Windows 玩家下载版

普通玩家不需要部署服务器。请从
[GitHub Releases](https://github.com/EytleBB/CS-Scout/releases/latest) 下载名称类似
`CS-Scout-Windows-x64-v2.0.2.zip` 的独立 Windows 发布包，完整解压后依次双击：

1. `windows\Install-CS-Scout.cmd`：创建独立 Python 环境并安装固定版本依赖；
2. `windows\Start-CS-Scout.cmd`：启动本机服务、复制访问密钥并打开浏览器。

不要下载 GitHub 自动生成的 `Source code (zip)`，它不包含被 Git 忽略的运行时雷达图。
详细要求、升级、卸载和排障见
[`windows/README-PLAYER-ZH.md`](windows/README-PLAYER-ZH.md)。玩家版只监听
`127.0.0.1`，由 Windows 自动分配空闲端口，并在启动窗口显示实际地址；不需要也不应开放
公网或防火墙端口。密钥、Demo 和分析输出保存在
`%LOCALAPPDATA%\CS-Scout`，升级程序时会继续保留。

## 主要功能

- 最多同时分析 5 名 5E 玩家，每人可选 1–10 场 Demo。
- 支持 CT/T 全局切换、统一播放/暂停、可拖动时间轴，以及 1x / 2x / 4x 播放速度（默认 2x）。
- 回放区顶部用“手枪局（全员）”和各玩家用户名按钮切换，CT/T 固定在选择栏右侧；用户名按钮显示该玩家
  的 Buy 回合，任一时刻只显示一个完整雷达。
- 回放玩家位置、移动方向、死亡点以及烟雾、闪光、高爆、燃烧弹和诱饵弹。
- 统计全局 K/D 与 AWP 持有回合占比。
- 扫描按钮上方可切换“普通 / 快速”：普通模式保留原有稳定流水线；快速模式并行完成玩家发现、
  Demo 下载和多进程解析。
- 快速模式即使任务完成顺序不同，也会按输入玩家及原 Demo 顺序生成确定结果；同一比赛只会有
  一个实际下载任务，其余并发请求复用该结果。
- 按比赛 ID 跨玩家复用 Demo；生产默认缓存超过 16 GB 时清理到不高于 10 GB，并保留
  至少 8 GB 文件系统空闲空间。
- 当前部署数据包含 `de_ancient`、`de_anubis`、`de_dust2`、`de_inferno`、
  `de_mirage`、`de_nuke`、`de_overpass` 和 `de_train`。

## 系统结构

```text
浏览器（templates/index.html + static/app.js + static/replay.js）
    │ POST /api/analyze（mode: normal | fast，默认 normal）
    ▼
Flask（server/web_server.py）
    ├─ 普通：pipeline.run(...)
    │    └─ 原有生产者/消费者流水线
    └─ 快速：pipeline.run_fast(...)
         ├─ 并行玩家发现 + 并发 Demo 下载
         └─ ProcessPool 并行解析
    ▼
server/output/player_<domain>.json
server/output/analysis_summary.json
```

主要模块：

| 文件 | 作用 |
|---|---|
| `server/api_client.py` | 5E Arena/Gate 查询、历史比赛分页和 Demo 下载 |
| `server/pipeline.py` | 普通/快速流水线、Demo 去重与同比赛 single-flight、磁盘清理 |
| `server/parse.py` | 回合分类、位置、投掷物和死亡时间解析 |
| `server/combat.py` | K/D 与 AWP 持有率统计 |
| `server/player_json.py` | 生成浏览器消费的玩家 JSON |
| `server/maps.py` | 地图元数据加载与游戏坐标转换 |
| `server/web_server.py` | Flask API 和静态资源路由 |

`tools/` 中保留了 1.0 的离线热力图、路径查看、地图校准和区域编辑工具；
它们不参与 2.0 的 Web 服务流程。

## 本地运行

建议使用 Python 3.11 或 3.12。以下命令均从仓库根目录执行：

```bash
python -m venv .venv

# Linux/macOS
source .venv/bin/activate

# Windows PowerShell
# .\.venv\Scripts\Activate.ps1

pip install -r server/requirements.txt
```

首次部署需要准备雷达图和坐标变换：

```bash
awpy get maps
python server/setup_maps.py
```

分析接口必须显式设置访问密钥；未设置时 `/api/analyze` 返回 503，不会启动高成本的
下载和解析任务。本地开发服务器默认只监听 `127.0.0.1`：

```bash
# Linux/macOS
export CS_SCOUT_SECRET_KEY='replace-with-a-random-secret'

# Linux/macOS（仓库根目录）
./.venv/bin/python ./server/web_server.py
```

Windows PowerShell 从仓库根目录启动：

```powershell
$env:CS_SCOUT_SECRET_KEY='replace-with-a-random-secret'
.\.venv\Scripts\python.exe .\server\web_server.py
```

服务默认监听 `127.0.0.1:5000`。生产部署还应使用反向代理、HTTPS 和适当的网络
访问控制，不建议直接把 Flask 开发服务器暴露到公网。

快速模式可通过以下环境变量调节资源占用；未设置时会使用安全的自动默认值：

| 环境变量 | 作用 | 默认值 |
|---|---|---|
| `CS_SCOUT_FAST_DOWNLOAD_WORKERS` | 并发 Demo 下载线程数 | `max(6, min(12, CPU×2))`，上限 32 |
| `CS_SCOUT_FAST_PARSE_WORKERS` | 并行解析进程数 | CPU ≤ 2 时为 1，否则为 2，上限 16 |
| `CS_SCOUT_FAST_PARSE_MEMORY_PER_WORKER_MB` | 每个解析进程预估内存，用于自动限流 | 2048 MB |
| `CS_SCOUT_FAST_PARSE_MEMORY_RESERVE_MB` | 启动解析进程前为系统预留的可用内存 | 1024 MB |
| `CS_SCOUT_DEMO_MAX_DOWNLOAD_MB` | 单个压缩 Demo 最大下载量 | 1024 MB |
| `CS_SCOUT_DEMO_CACHE_LIMIT_GB` | 触发缓存清理的大小 | 16 GB |
| `CS_SCOUT_DEMO_CACHE_TARGET_GB` | 清理后的缓存目标 | 10 GB |
| `CS_SCOUT_DEMO_MIN_FREE_GB` | Demo 文件系统最低保留空间 | 8 GB |
| `CS_SCOUT_DEMO_TASK_DOWNLOAD_LIMIT_GB` | 单次分析累计下载上限 | 12 GB |
| `CS_SCOUT_DEMO_REQUIRE_PUBLIC_DNS` | 是否拒绝解析到私网地址的 CDN DNS；国内加速环境谨慎开启 | `false` |

## Ubuntu 24.04 生产部署

仓库提供了一套面向 4 核、4 GB 内存、60 GB 磁盘轻量 VPS 的部署基线，详见
[`deploy/README.md`](deploy/README.md)：

- Nginx 负责 HTTPS、安全响应头和扫描接口限速，并为启动分析原样透传应用 Bearer 密钥。
- Gunicorn 只监听 `127.0.0.1:5000`，固定 **1 个 worker / 4 个线程**。当前任务状态和
  去重锁位于进程内，不能直接增加 Gunicorn worker 数。
- 运行数据保存在 `/var/lib/cs-scout`，密钥保存在
  `/etc/cs-scout/cs-scout.env`，代码使用不可变版本目录并支持软链接回滚。
- 4 GB 主机建议从 4 个下载线程、1 个解析进程开始，测量内存峰值后再调整。

主要部署文件：

| 文件 | 作用 |
|---|---|
| `deploy/cs-scout.service` | systemd 服务、单 worker Gunicorn 和运行时加固 |
| `deploy/nginx-cs-scout.conf` | HTTPS 反向代理、分析密钥 Bearer 透传、安全头和限流 |
| `deploy/nginx-cs-scout-bootstrap.conf` | 首次申请 TLS 证书的 HTTP-only 配置 |
| `deploy/verify_release.sh` | 发布前只读校验依赖、目录和 8 张地图 |
| `.env.example` | 生产环境变量模板，不包含真实密钥 |

生产服务器只需安装 `server/requirements-runtime.txt`。`awpy` 已拆到
`server/requirements-maps.txt`，仅在生成地图时使用；原来的
`server/requirements.txt` 仍会安装两者，保持本地安装命令兼容。

## API

| 方法 | 路径 | 说明 |
|---|---|---|
| `POST` | `/api/analyze` | 启动分析，正文为 `usernames`、`map`、`max_demos`、`key`，可选 `mode` |
| `GET` | `/healthz` | 公开存活探针 |
| `GET` | `/readyz` | 公开就绪探针，检查密钥、地图和运行目录 |
| `GET` | `/api/status` | 公开查询实时任务状态、逐玩家进度和最近一次结果 |
| `GET` | `/api/maps` | 返回服务器已准备的地图 |
| `GET` | `/api/player/<domain>` | 公开返回一名玩家的回放 JSON |
| `GET` | `/api/results` | 公开返回最近一次分析摘要 |
| `GET` | `/output/<file>` | 公开返回生成的分析 JSON |
| `GET` | `/maps/<path>` | 返回雷达资源 |
| `GET` | `/icons/<path>` | 返回投掷物 SVG 图标 |

示例：

```json
{
  "usernames": ["player-a", "player-b"],
  "map": "de_mirage",
  "max_demos": 6,
  "mode": "fast",
  "key": "replace-with-a-random-secret"
}
```

`mode` 只接受 `"normal"` 或 `"fast"`，省略时默认为 `"normal"`，因此旧客户端仍使用
原有稳定流水线。`analysis_summary.json` 和 `/api/results` 返回的摘要均包含最终采用的 `mode`。
查看页面、实时进度和已有分析结果不需要密钥。只有 `POST /api/analyze` 使用
`Authorization: Bearer <分析密钥>`；同时继续兼容旧客户端 JSON 中的 `key` 字段。

非手枪回合只有目标玩家自身 `current_equip_value >= 2000` 时才会保留为
`Buy`；低于该值的回合不会进入回放 JSON。每个半场段的第一个回合始终保留为
`Pistol`。

## 测试

```bash
cd server
python -m pytest tests/ -v --basetemp ../.pytest-tmp
```

部分集成测试使用：

```text
demos_analysis/g161-n-20260123174821830606429_de_mirage.dem
```

该大文件不纳入 Git；缺失时相关测试会自动跳过。

## 数据与限制

- 5E 用户名发生变更后，旧比赛中按用户名解析 Steam ID 可能失败。
- 没有目标地图历史 Demo 的玩家会出现在失败列表中，不会阻断其他玩家。
- 5E 接口或网络故障会与“有效查询但没有比赛”分别报告。
- `server/demos_opponents/`、`server/output/` 和生成的地图资源均为运行时数据，
  不应提交到 Git。
- 本项目仅用于个人学习和战术研究；使用时请遵守平台服务条款。

## 许可证

项目原创源代码采用 [MIT License](LICENSE)。第三方依赖、商标和素材说明见
[`THIRD_PARTY_NOTICES.md`](THIRD_PARTY_NOTICES.md)。
