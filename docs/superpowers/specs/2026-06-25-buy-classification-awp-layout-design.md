# 设计：个人买局分类 + 持狙占比 + 合并布局

日期：2026-06-25
状态：已批准设计，待写实现计划

## 背景与目标

CS-Scout 2.0 当前把回合分为 Pistol/Full/Eco（按全队平均装备值），每名对手一张卡片、
三宫格（Pistol/Full/Eco）、每卡各自带 CT/T tabs；AWP 率口径为"仅 CT 方 AWP 击杀 / CT 方
总击杀"，数据稀疏导致几乎总是 0%。

本次改动三件事：

1. 回合分类改为**个人判定**，去掉 Eco，丢弃个人装备过低的局，只保留 **Pistol** 和 **Buy**。
2. AWP 率改为**持狙回合占比**，修掉一直 0% 的问题。
3. 前端重构：顶部**统一 CT/T 切换**；**手枪局把 5 名对手合并到一张图**置顶；每名对手卡片只留
   一张**买局**图。

## 1. 回合分类（`parse.py` `classify_rounds`）

判定点不变：每回合 freeze_end 快照，字段 `current_equip_value`。

- **改个人判定**：用目标玩家个人的 `current_equip_value`，不再算全队平均。
- 类型 3 种 → 2 种：
  - **Pistol** — 每半场首回合（现有 `pistol_num` / 换边逻辑不变；无条件保留，不受 2000 门槛限制）。
  - **Buy** — 非手枪局且个人装备 `>= EQ_BUY_MIN`（2000）。
  - 非手枪局且个人装备 `< 2000` → **丢弃**：`rtype = None`，不进入 JSON。
- **换边检测不能被丢弃局打断**：手枪局判定依赖 `prev_side`。因此即使某局被丢弃，`side` 仍按
  真实 CT/T 计算并用于 `prev_side` 跟踪，只把 `rtype` 置 `None` 表示丢弃。否则下一局会被
  误判为半场首局（=手枪局）。
- **下游过滤**：`parse_positions` / `parse_grenades_for_rounds` / `parse_deaths_for_rounds`
  当前的 `active = [r for r in classified if r["side"]]` 改为
  `if r["side"] and r["rtype"]`，从而排除被丢弃的局。
- `config.py` 新增 `EQ_BUY_MIN = 2000`。`EQ_FULL_BUY` 不再被 `classify_rounds` 使用（保留常量，
  避免触碰旧引用；如确认无其他引用可一并删除）。

## 2. AWP 率 → 持狙回合占比（`combat.py`）

- 废弃旧指标（CT 方 AWP 击杀 / CT 方击杀）。
- 新指标：**持狙回合数 / 总回合数 × 100**。
- **"持狙"判定**：在该回合的采样 tick 上读目标的 `inventory`（demoparser2 tick 字段，返回武器名
  列表），任一采样点的 inventory 含 AWP（匹配 `awp` / `weapon_awp`，大小写不敏感）即记该回合为
  持狙回合。
  - 实现前先用真实 demo 验证 `inventory` 字段可用及其武器名格式。
  - **回退方案**：若 `inventory` 不可用，退化为"该回合内目标有 AWP 击杀"（player_death，
    attacker=目标 且 weapon=awp，tick 落在回合窗口）。
- **分母 = 该玩家出场的全部回合**（CT + T，不限经济，含被分类层丢弃的 eco 局）。即用
  `classify_rounds` 结果里 `side` 非空的全部回合，不要求 `rtype`。
- `parse_combat_stats` 返回 `{kd, awp_rounds, total_rounds}`。
- `aggregate_combat_stats`：`awp_rate = round(sum(awp_rounds)/sum(total_rounds)*100, 1)`，
  分母 0 时为 0.0。`kd` 聚合不变。

## 3. 前端布局

### 统一 CT/T 切换（`index.html` header + `app.js`）
- 在 header 的 `.ctrls`（与播放/进度条同排）放一个 CT/T 切换（两个按钮，默认 CT）。
- 该开关控制页面上**所有** canvas 的 side：手枪合并图 + 每个对手买局图。一次只看一边。
- 移除每张卡片内的 CT/T tabs。

### 手枪局合并区（`#pistol`，置于 `#cards` 上方）
- **一张大 canvas**，叠加本次扫描到的最多 5 名对手的**手枪局**路径。
- **每名玩家一种颜色** + 文字图例（玩家名 ↔ 颜色）。
- 受顶部统一开关控制：显示当前 side 的手枪局。
- 实现：构造合并轮次列表 = 各玩家 `rounds` 中 `rtype=="Pistol"` 的局，每局打上该玩家颜色 tag，
  作为单个 `ReplayPlayer` 的 `rounds`，`rtype="Pistol"`。

### 每名对手卡片（`#cards`）
- 保留：玩家名、K/D、AWP%（持狙占比）、回合数。
- 去掉：CT/T tabs、Pistol/Full/Eco 三宫格。
- 只留**一张 canvas**，叠加该玩家所有 **Buy** 局，`rtype="Buy"`，按 side 上色。

### `replay.js`（最小改动）
- 支持 per-round 颜色覆盖：dot/X/arrow 颜色取 `r.color || SIDE_COLOR[this.side]`
  （把 `col` 计算移入按 round 循环内）。单人买局图不带 `color`，仍按 side 上色；合并图每局带
  `color`。
- `setFilter(side, rtype)` 不变。统一开关切 side 时，对每个 `ReplayPlayer` 调用
  `setFilter(side, 该图固定 rtype)`（合并图固定 Pistol，买局图固定 Buy）。
- `RTYPES = ["Pistol","Full","Eco"]` 常量移除/改写为新布局逻辑。

## 影响文件

| 文件 | 改动 |
|------|------|
| `server/config.py` | 新增 `EQ_BUY_MIN = 2000` |
| `server/parse.py` | `classify_rounds` 个人判定 + 丢弃逻辑；3 个下游过滤条件加 `and r["rtype"]` |
| `server/combat.py` | AWP 改持狙占比（inventory 检测 + 回退）；返回值与聚合调整 |
| `server/templates/index.html` | header 加统一 CT/T 开关；main 加 `#pistol` 区 |
| `server/static/app.js` | 新布局：合并手枪图、单买局图、统一开关接线；移除三宫格/每卡 tabs |
| `server/static/replay.js` | per-round 颜色覆盖 |
| `server/player_json.py` | 无需改（rtype 自然变 Pistol/Buy） |

## 验证

- 单元/集成：对 fixture demo `demos_analysis/g161-...de_mirage.dem` 验证
  `classify_rounds` 只产出 Pistol/Buy/None，个人判定生效；`parse_combat_stats` 持狙占比对已知
  AWPer（如 76561199755381652，26 次 AWP 击杀）> 0。
- 视觉：重启 `web_server.py`，跑一次真实扫描或用 `replay_test.html`，确认顶部统一开关切换全部
  canvas、手枪合并图多色叠加、每卡单买局图正常。

## 不做（YAGNI）

- 不恢复 Force Buy 档。
- 不做按回合逐个开关（每图整体叠加即可）。
- 不改下载/管线/5E 抓取逻辑。
