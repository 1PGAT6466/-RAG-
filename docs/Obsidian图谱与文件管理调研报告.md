# Obsidian / WorkBuddy 图谱与文件管理调研报告

> 调研时间：2026-08-19
> 调研方式：多引擎搜索 + GitHub API 抓取开源仓库真实源码
> 结论面向：伏羲系统知识图谱 + 文件管理的「Obsidian 式」改造

---

## 一、核心结论（TL;DR）

1. **Obsidian 主程序闭源**（Electron + app.asar），无法读源码；但它的图谱技术栈被社区大量开源插件复现，技术方法完全可考。
2. **图谱渲染的工业级标准做法是「Canvas/WebGL + d3-force 力导向」**，而不是 SVG。伏羲当前用 SVG 渲染 500 节点/4266 边，是性能与视觉的瓶颈根源。
3. **「节点如何出现/发展」= Timeline 时间轴**：按月直方图做桶，时间滑动条逐步「长」出节点。已有成熟开源实现（`advanced_graph_view`）。
4. **数据模型极简**：节点 = {id, name, inCount, outCount}；边 = {source, target, weight}。伏羲已有 entities/entity_relations，结构天然对齐，缺的是「入度/出度」区分和「created_at 时间轴」。

---

## 二、技术栈（来源确凿）

### 2.1 力导向布局引擎
- **`d3-force`**：几乎所有 Obsidian 图谱用这个（伏羲也在用 ✅）
- 关键差异：**逐节点居中用 `forceX/forceY`，不用 `forceCenter`**（`forceX/forceY` 可做多焦点、分组散开，`forceCenter` 会把所有节点往中心挤成一团——这正是伏羲「中间全聚团」的根因之一）

### 2.2 渲染引擎（伏羲要改的关键）
| 项目 | 渲染层 | 说明 |
|------|--------|------|
| Obsidian 原生 | Canvas | 2D Canvas 手绘，非 DOM |
| graph-plus（开源） | Canvas（2D）/ WebGL（3D） | `graphRenderer2D.ts` 用 `HTMLCanvasElement` + `forceX/forceY` |
| fast-graph（开源） | Three.js + WebGL | 2 万节点 75 FPS，GPU instancing |
| advanced_graph_view（开源） | Pixi.js v8 + WebGL | 1 万节点 50+ FPS，力模拟跑 Web Worker |
| akasha（开源） | 3d-force-graph（Three.js） | 扫描 vault |

- **SVG 适合 < 300 节点**；**Canvas 适合 < 5000 节点**；**WebGL 适合 5000+ 节点**。
- 伏羲当前 502 节点/4266 边用 SVG，已在卡顿边缘。改造方向：**先换 Canvas**（成本低、收益大），未来量大再上 WebGL。

### 2.3 社区检测 / 聚类
- **`graphology`**（Louvain 算法）——`advanced_graph_view` 用它做聚类 + 自动命名（TF-IDF）
- 伏羲可用它做「实体自动聚类」，把 502 个实体的 15 个类别自动归纳成视觉簇

---

## 三、Obsidian 图谱的核心交互（要模仿的清单）

来源：Obsidian 官方文档 + 开源插件实现：

1. **节点统一大小**，不按 degree 夸张放大（degree 用「入度/出度」而非总度）
2. **标签缩放渐显**：缩小隐藏、放大出现（伏羲上一轮已实现 ✅）
3. **hover 聚焦**：悬停节点，高亮它和直接邻居，其余淡出（伏羲已有，需配合 Canvas）
4. **局部图谱（Local Graph）**：只看当前节点 N 跳邻域，深度 1~4 可调
5. **颜色分组**：按 `path:/tag:/file:` 查询语法给节点分组着色
6. **底部工具条**：搜索、滤镜、孤儿/断链开关、图例折叠
7. **时间轴 Timeline**：播放按钮，按月「生长」节点（✅ 你要的「节点怎么出现发展」）
8. **滤镜语法**：`path:` `tag:` `file:` `-exclude`；`advanced_graph_view` 还加了 `created:>日期`、`links:>5`、`inlinks:0` 等

---

## 四、文件管理（Obsidian / WorkBuddy）核心机制

来源：Obsidian 官方特性 + 调研：

1. **Vault 仓库模型**：本地文件夹即仓库，文件 = Markdown 纯文本，零数据库依赖
2. **双向链接**：`[[笔记名]]` 建立链接，被引用方自动生成「反向链接」面板
3. **反向链接面板（Backlinks）**：核心差异化——看「哪些文件提到了我当前文件」
4. **多维度组织**：文件夹（目录树）+ 标签 + 书签 + 大纲，四维并存
5. **毫秒级全文搜索**：`tag:#工作`、`file:关键词` 语法检索
6. **Dataview**：把 Markdown 当数据库查询（列表/表格/日历视图）

### 伏羲的落地映射
| Obsidian 概念 | 伏羲现有 | 差距 |
|---------------|----------|------|
| Vault 文件夹 | files 表 + 目录浏览 | 已有，可强化 |
| 双向链接 | 实体关系（entity_relations）| 有实体关系，缺「文件↔文件」引用关系 |
| 反向链接面板 | 实体反链（GraphView drawer）| 已有雏形，可扩展到文件级 |
| 标签 | files.tags 字段 | 已有 |
| 全文搜索 | FTS5（jieba）| 已有，语法检索可加 |

---

## 五、伏羲改造方案（结合自身，做出自己的）

### 阶段 A：图谱渲染换 Canvas（先做，收益最大）
- `GraphView.vue` 的 SVG → **HTMLCanvasElement**
- 保留 d3-force，但：
  - `forceCenter` → `forceX/forceY`（解决中心聚团）
  - 边用 Canvas 绘线（复用现有 type/rel_type 配色）
  - 节点用 `arc()` 绘制
- 保留：缩放渐显标签、hover 聚焦、图例折叠、实体类型/关系筛选
- 预期：500 节点 60 FPS，视觉干净

### 阶段 B：加入度/出度 + 局部图谱
- 后端 `/api/entities/graph` 返回的 degree → 拆分 `inCount/outCount`（entity_relations 的 source_id/target_id 天然有方向）
- 加「局部图谱」：点击节点展开 N 跳邻域（深度滑块 1~4）

### 阶段 C：时间轴演进（你要的「节点怎么出现发展」）
- 后端暴露 `entities.created_at`（表和字段已存在，只需 API 返回）
- 前端加时间滑动条（references：`advanced_graph_view` 的 `timeline.ts` 按月直方图）
- 播放：节点按 created_at 月份逐步出现，边随之生长
- ⚠️ 现实约束：当前 4 份文件同一小时批量入库，时间跨度太短，演进图暂无「先后」可看。等后续持续上传不同时间的文件，价值才显现。但**机制现在就可以建好**。

### 阶段 D：文件管理优化
- 文件↔文件引用关系（类似文档引用图，但更细）
- 反向链接面板（文件级）
- 目录树浏览 + 标签 + 筛选三者统一的文件浏览体验

---

## 六、参考开源项目（MIT，可读源码）

| 项目 | GitHub | 价值 |
|------|--------|------|
| advanced_graph_view | `n23eos/advanced_graph_view` | 最完整：Timeline、PageRank、Louvain 聚类、Web Worker、滤镜语法 |
| graph-plus | `NicolasOng/graph-plus` | 2D Canvas + 3D WebGL 双渲染器，typed links |
| fast-graph | `jkRaccoon/obsidian-fast-graph` | Three.js GPU instancing，2 万节点 75 FPS |
| akasha | `chntnm/akasha` | 3d-force-graph 扫描 vault，标签渐显 |
| 4d-graph-explorer | `glebis/obsidian-4d-graph-explorer` | TypeScript 4D 图 |

---

## 七、未决事项（需与用户确认）

1. **是否引入 WebGL 依赖**（three.js / pixi.js，约 +600KB bundle）？还是先用 Canvas（零依赖、够用）？—— 建议先 Canvas。
2. **时间轴演进**是否现在做？还是等有真实时间跨度的数据后再做？
3. **文件管理**是优先「文件↔文件引用 + 反向链接」，还是优先「目录树 + 标签 + 筛选统一体验」？
