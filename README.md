# CS-Scout

> CS2 赛前情报系统 — 自动分析对手 demo，生成 CT 站位热力图与区域分布统计

**⚠️ 当前为 Alpha 阶段，仅支持 Mirage 地图**
**⚠️ 目前仅支持分析5E对战平台的数据**

---

## 功能

- 输入最多 5 个 5E 平台用户名，一键启动分析
- 自动从 5E 平台抓取最近的 Mirage 排位 demo（每人最多 10 场）
- 按回合类型（满装备 / 强买 / 省钱 / 手枪局）生成独立 CT 站位热力图
- 区域占位分布统计（A 轮防守、D 防守、K 控制等）
- Combat Stats：K/D（来自 demo 记分板）、AWP 使用率（CT 边 AWP 击杀占比）
- 下载/解析流水线并行，5 人分析约 10–20 分钟完成

## 截图

*（待补充）*

## 架构

```
浏览器 UI
   │  POST /api/analyze_by_names
   ▼
Flask 服务端 (web_server.py)
   │  后台线程
   ▼
pipeline.run_by_usernames()
   ├── 下载线程：搜索玩家 → 获取 demo 列表 → 下载解压
   └── 主线程：  解析 .dem → 生成热力图 → 计算 combat stats
   ▼
output/tile_{domain}_{rtype}.png  +  analysis_summary.json
```

下载线程和解析主线程以 demo 为单位流水线并行，下载下一个 demo 的同时解析当前 demo。

## 技术栈

| 层 | 技术 |
|----|------|
| 后端 | Python 3.11+, Flask |
| Demo 解析 | [demoparser2](https://github.com/pnxenopoulos/demoparser) |
| 数据处理 | pandas, numpy, shapely |
| 热力图渲染 | matplotlib, scipy |
| 数据来源 | [5E Platform](https://arena.5eplay.com) API |
| 前端 | 原生 HTML/CSS/JS（无框架） |

## 部署（VPS）

```bash
# 安装依赖
pip install flask numpy pandas matplotlib scipy shapely demoparser2 requests

# 启动
cd server
python web_server.py
```

访问 `http://<服务器IP>:5000`，确保云服务商安全组放通 5000 端口。


## 当前限制 / Roadmap

| 状态 | 内容 |
|------|------|
| ✅ | Mirage CT 站位热力图（4 种回合类型） |
| ✅ | 区域分布统计 |
| ✅ | K/D、AWP 率 |
| ✅ | Demo 去重、磁盘自动清理 |
| 🔲 | 支持更多地图（Inferno、Dust2 等） |
| 🔲 | T 侧进攻路线分析 |
| 🔲 | 本地客户端（GSI 自动检测对手） |
| 🔲 | 历史数据持久化 |

## 数据说明

- 仅分析 **5E 平台排位** demo（`match_type=9`），其他模式通常无 demo 下载链接
- 无 Mirage 排位记录的玩家会在结果中标记为"分析失败"
- demo 文件 200-500 MB，服务端需要足够磁盘空间（默认上限 30 GB，超出自动清理至 10 GB）

---

*本项目仅用于个人学习和战术研究，请勿用于任何违反平台服务条款的用途。*
