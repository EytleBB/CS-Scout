# CS-Scout 第三方声明

本文件适用于 CS-Scout v2.0.1，用于说明项目与第三方平台、软件和素材之间的关系。它只提供事实性信息，不构成法律意见，也不替代各上游项目的完整许可证或服务条款。

CS-Scout 项目原创源代码采用仓库根目录 `LICENSE` 中的 MIT License。下述第三方组件、
商标和素材说明不因项目主许可证而被重新授权。

本声明和 MIT 主许可证针对 v2.0.1 的当前源代码快照与发布包。Git 历史中已经删除的
第三方文件仍遵循其原先标示的单独条款，不因本次发布而被重新授权，也不属于 v2.0.1
玩家发布包。

## 非官方关系与商标

CS-Scout 是独立开发的社区工具，不是 Valve Corporation、5E 对战平台、Awpy 或下列第三方依赖项目的官方产品，也未得到这些主体的赞助或背书。

Counter-Strike、Counter-Strike 2、CS2、Steam 及其相关名称和标识的权利归 Valve 及相应权利人所有。5E 的名称、标识及平台内容的权利归 5E 及相应权利人所有。本项目提及这些名称，仅用于说明兼容对象、数据来源或功能用途，不表示任何官方关联。

- Valve 法律与商标信息：<https://store.steampowered.com/legal/>
- 5E 用户服务协议：<https://csgo.5eplay.com/page/service>

## 雷达地图、图标与 Logo

Windows 发布包中的雷达地图通过 Awpy 工具链准备，再由本项目的 `server/setup_maps.py` 转换为运行时所需的地图目录和坐标数据。项目维护者已确认：v2.0.1 公开发布包内所含雷达图、现有 SVG 图标和 Logo 可由本项目公开使用。

这项确认只说明本项目发布这些素材的依据，不表示 Valve、5E、Awpy 或其他第三方对 CS-Scout 作出授权、赞助或背书，也不自动授予发布包接收者将素材单独提取、再许可或用于其他项目的权利。如需在 CS-Scout 之外复用这些素材，请另行确认适用权限。

Awpy 自身以 MIT License 发布；该软件许可证适用于 Awpy 软件本身。本项目没有仅凭 Awpy 的 MIT License 推断雷达图、图标或 Logo 的使用权限，而是依据上述项目方的单独确认。

- Awpy 官方仓库及许可证：<https://github.com/pnxenopoulos/awpy>

## 直接 Python 依赖

下表列出 v2.0.1 的直接依赖及其上游声明的许可证。Windows 安装脚本通过 `pip` 下载运行依赖；Linux 部署另外使用 Gunicorn；Awpy 只用于准备地图。请以链接中的上游许可证原文为准。

| 组件 | v2.0.1 固定版本 | 用途 | 上游许可证 | 官方链接 |
| --- | ---: | --- | --- | --- |
| Flask | 3.1.3 | Web 服务与 API | BSD-3-Clause | <https://github.com/pallets/flask> |
| Requests | 2.34.2 | HTTPS 请求与 Demo 下载 | Apache-2.0 | <https://github.com/psf/requests> |
| urllib3 | 2.7.0 | HTTP 连接、重试与传输支持 | MIT | <https://github.com/urllib3/urllib3> |
| pandas | 3.0.3 | Demo 解析后的表格数据处理 | BSD-3-Clause | <https://github.com/pandas-dev/pandas> |
| NumPy | 2.4.6 | 数值和数组处理 | BSD-3-Clause | <https://github.com/numpy/numpy> |
| demoparser2 | 0.41.4 | Counter-Strike 2 Demo 解析 | MIT | <https://github.com/LaihoE/demoparser> |
| Gunicorn | 23.0.0 | Linux 生产环境 WSGI 服务，仅非 Windows 安装 | MIT | <https://github.com/benoitc/gunicorn> |
| Awpy | 2.0.2 | 一次性地图准备工具，非运行时依赖 | MIT | <https://github.com/pnxenopoulos/awpy> |

这些组件还可能通过 `pip` 安装各自的间接依赖。每个间接依赖仍受其自身许可证约束；可在安装后的 Python 环境及对应的 `*.dist-info` 元数据目录中查看实际安装版本和许可证文件。本清单没有复制完整许可证文本，也不改变任何第三方许可证的条件。

## 平台使用与隐私提醒

CS-Scout 会访问 5E 相关服务以查找比赛和下载 Demo。使用者应在自己有权访问数据的范围内使用本工具，并自行阅读和遵守当时适用的 5E 服务条款、平台规则及当地法律。第三方接口、页面和条款可能变化；本项目不保证第三方服务持续可用或永久兼容。

下载的 Demo、分析输出和运行日志可能包含玩家用户名、平台标识符、SteamID、比赛历史、位置轨迹和战斗统计等信息。请仅为正当用途处理这些数据，并注意：

- 不要把 Demo、`output` 目录、日志或未脱敏的分析结果直接上传到公开 Issue、网盘或代码仓库；
- 分享或发布分析结果前，应确认自己有权这样做，并按需要取得相关人员同意或进行脱敏；
- 不要公开 `%LOCALAPPDATA%\CS-Scout\secret.key`、服务器访问密钥或其他凭据；
- 删除本地数据前先停止分析任务并确认已有必要备份。
