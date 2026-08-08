# CS-Scout 2D Demo 回放架构拆解

> 使用 codebase-design skill 的 deep-module 词汇分析

## 数据流总览

```
.dem 文件
    │  demoparser2 (C++ binding)
    ▼
parse.py ──→ 回合表 / 侧别分类 / 位置采样 / 投掷物轨迹 / 死亡时间
    │
    ▼
player_json.py ──→ 组装 JSON (含 transform + rounds[])
    │
    ▼
output/player_{domain}.json
    │  HTTP /api/player/<domain>
    ▼
app.js ──→ 时钟驱动 + 视图切换 + 侧别/类型过滤
    │
    ▼
replay.js ReplayPlayer ──→ Canvas 2D 逐帧绘制
```

整个系统分两层：**服务端解析**（Python，一次性产出 JSON）和**浏览器回放**（JS，循环动画渲染）。两层之间的唯一接口是 `player_{domain}.json` 的 schema。

---

## 模块拆解

### 1. parse.py — Demo 解析（深度模块）

**接口**：`parse_demo(path, steamid) -> list[round_dict]`

**实现**：内部经过 5 个阶段，调用方完全不需要知道：

| 阶段 | 函数 | 做什么 |
|------|------|--------|
| 回合表 | `get_round_table(evts)` | 从 `round_announce_match_start` → `round_freeze_end` → `round_end` 事件序列构建回合列表 |
| 分类 | `classify_rounds(parser, rounds, sids)` | 按 CT/T 侧别 + 装备价值分类为 Pistol / Buy / 丢弃 |
| 位置 | `parse_positions(parser, classified, sid)` | 每 8 个 tick 采样 X/Y，输出 `[[t, x, y], ...]` |
| 投掷物 | `parse_grenades_for_rounds(...)` | 解析 projectile 实体的轨迹，截断静止尾巴，计算 land_t 和 expire_t |
| 死亡 | `parse_deaths_for_rounds(evts, classified, sid)` | 从 `player_death` 事件提取死亡 tick |

**深度体现**：调用方只需要 `path + steamid`，得到的就是完整的回放数据。内部依赖 demoparser2、pandas、numpy，处理 6 种事件类型、5 种投掷物、tick 级时间轴——全部藏在接口后面。

**关键设计**：
- 时间都是"从 freeze_end 开始的秒数"，不是绝对 tick。这让前端不需要知道 tickrate。
- 投掷物轨迹会截断静止尾巴（`_landing_index`），否则 smoke 会在落地后继续画 18 秒的静止点。
- 低经济回合保留 `side` 但 `rtype=None`，不产出路径/投掷物，但不会打断半场追踪。

### 2. maps.py — 坐标变换（适配器）

**接口**：`game_to_pixel(transform, gx, gy) -> (px, py)`

```
px = (gx - pos_x) / scale
py = (pos_y - gy) / scale    // Y 轴翻转
```

CS2 游戏坐标的原点在左下角、Y 向上；Canvas 像素原点在左上角、Y 向下。`transform` 来自 awpy 的 `meta.json`，含 `{pos_x, pos_y, scale}` 三个值。

这个变换在服务端（`player_json.build` 写入 transform）和浏览器（`ReplayPlayer.g2p` 执行变换）都用到，但接口只有一个函数。

### 3. replay.js ReplayPlayer — Canvas 渲染引擎（核心深度模块）

**接口**（极小）：

```javascript
new ReplayPlayer(canvas, { radar, transform, rounds, side, rtype })
player.drawAt(gameTime)      // 在指定游戏时间绘制一帧
player.setFilter(side, rtype) // 切换 CT/T + Pistol/Buy 过滤
player.toggleRound(roundId, enabled) // 开关单回合
player.destroy()              // 清理
```

**实现**（复杂）：每帧 `drawAt(gameTime)` 做以下事情：

```
1. 清空 canvas → 画雷达底图
2. 遍历所有匹配 (side, rtype) 且未禁用的回合：
   a. 画投掷物：
      - 飞行中（throw_t ≤ t < land_t）→ 画轨迹弧线 + 空中图标
      - 已落地（land_t ≤ t < expire_t）→ 画半透明范围圈 + 效果图（烟雾/火焰）
   b. 画玩家位置：
      - 已死亡（t ≥ death_t）→ 画 X 标记，跳过
      - 存活 → 插值位置 → 画圆点 + 移动方向箭头
3. 插值使用线性插值（_interp），在采样点之间平滑过渡
```

**深度体现**：
- 5 个方法构成完整接口，但背后是 375 行渲染逻辑
- `_interp` 函数同时服务于玩家位置、投掷物弹头位置、死亡标记
- `_velocityAt` 从相邻采样点推断速度方向，画箭头
- 投掷物图标的着色（`tintedGrenadeAsset`）用 canvas 合成模式实现，缓存结果
- 死亡后玩家消失（不 holdLast），但投掷物弹头在飞行中用 holdLast 保持位置

### 4. app.js — 时钟 + 视图编排（协调者）

**不是深度模块**，而是协调者。它管理：

**全局时钟**：
```
PLAYBACK_S = 10秒（动画循环周期）
WINDOW_S = 20秒（游戏时间窗口）
→ 2x 倍速：10 秒动画展示 20 秒游戏内容
```

`requestAnimationFrame` 驱动 `tick()`，每帧：
1. 计算 delta → 推进 `clock.elapsed`
2. `elapsed % PLAYBACK_S` → 循环
3. `drawAll(gameTime)` → 只画当前激活视图的 ReplayPlayer

**视图切换**：
- `replayViews` Map 管理所有视图（手枪局全员、每人 Buy、每人 Pistol）
- 同一时刻只有一个视图可见，只有它的 ReplayPlayer 被绘制
- CT/T 切换通过 `setSide()` → 遍历所有 `sideTargets` 调用 `setFilter(side, rtype)`

---

## 数据契约

两层之间的唯一接口是 JSON schema：

```json
{
  "transform": {"pos_x": -3453, "pos_y": 2887, "scale": 7},
  "radar": "/maps/de_nuke/radar.png",
  "rounds": [{
    "side": "CT",
    "rtype": "Buy",
    "round_id": 1003,
    "path": [[0.0, 2585, -344], [0.125, 2590, -340], ...],
    "grenades": [{
      "type": "smoke",
      "throw_t": 8.1,
      "land_t": 9.4,
      "arc": [[8.1, 2500, -300], [9.4, 2100, -250]],
      "land": [2100, -250],
      "expire_t": 27.4
    }],
    "death_t": 12.5
  }]
}
```

- `path` 中每个点 `[t, x, y]`：t 是从 freeze_end 起的秒数，x/y 是游戏坐标
- `grenades.arc` 同结构，`land` 是最终落点
- `death_t` 为 null 或秒数

前端完全依赖这个 schema，不需要知道 tick、tickrate、demoparser2 的存在。

---

## 设计评价

**深度好的地方**：
- `parse_demo` → 极简接口，5 阶段处理全隐藏
- `ReplayPlayer` → 5 个方法，375 行渲染逻辑
- `game_to_pixel` → 一个函数，解决坐标系翻转

**seam 放置合理处**：
- JSON schema 是服务端和浏览器之间的 seam，两侧可以独立演进
- `ReplayPlayer` 不关心数据来源（5E 在线 / 本地上传 / 完美平台）
- 时钟与渲染分离：`drawAt(gameTime)` 是纯函数式的，不依赖全局状态

**浅的地方**：
- `app.js` 的视图编排逻辑偏过程式，但这是 UI 协调者的正常形态，不需要强行加深
