# 伏羲 × SeedDMS 接入方案

> 最后更新：2026-09-03

## 一、目标与定位

引入 SeedDMS 作为伏羲的**受控文档源头**，解决"随手传文件到文件夹、无规划无统筹"的问题。

- **SeedDMS** = 文档的规整仓库（管：进来、分类、命名、版本、审批、权限）
- **伏羲** = 知识的加工引擎（管：清洗、切片、向量化、检索、问答）

两者通过一条**单向、可追溯的导入管道**连接。

## 二、数据流（单向）

```
SeedDMS（受控源头）  --拉取导入-->  伏羲 enqueue() 入库引擎
  文件夹树（规划分类）              ├─ parse（清洗/OCR/繁转简）
  文档 + 版本 + 元数据              ├─ chunk / embed / store
  审批流 + 权限                    ├─ classify / extract
                                   └─ summarize / preindex（异步）
                                            ↓
                                   检索 + 图谱 + 问答
```

## 三、核心决策（2026-09-03 用户拍板）

1. **手动勾选导入**：用户浏览 SeedDMS 文件夹树，勾选文档/文件夹后显式导入；不做定时/全量自动同步。
2. **自动替换旧数据**：文档出新版本时自动替换伏羲里的旧数据，顺序为 **先删数据库（SQLite）→ 再删 Chroma 向量缓存 → 重新入库**。
   - **旧版本不留历史**：DMS 已管版本生命周期，伏羲只保留最新版，不额外维护历史。
3. **不回写元数据**：保持单向，不做伏羲→SeedDMS 反向增强。
4. **预留 SeedDMS 连接配置**：地址/账号可配置，先落 `.env` + 后端配置接口，后续接配置面板 UI。

## 四、架构设计

### 新增模块

```
src/dms/
  ├── __init__.py
  ├── seeddms_client.py     # 封装 SeedDMS REST API
  ├── import_service.py     # 导入编排
  └── sync_state.py         # dms_imports 映射表读写 + 替换逻辑

src/api/dms.py              # /api/dms/* 路由
frontend/src/views/DmsImport.vue
frontend/src/api/dms.js
```

### 映射表 `dms_imports`

```sql
CREATE TABLE dms_imports (
  id            INTEGER PRIMARY KEY,
  file_id       INTEGER,          -- 伏羲 files.id
  dms_doc_id    INTEGER,          -- SeedDMS 文档 id
  dms_version   INTEGER,          -- SeedDMS 版本号
  dms_folder_path TEXT,           -- SeedDMS 文件夹路径快照
  dms_name      TEXT,             -- SeedDMS 文档名
  content_hash  TEXT,             -- 内容哈希（判版本是否变化）
  imported_at   TEXT,
  status        TEXT              -- imported / replaced / failed / removed
);
```

## 五、SeedDMS REST API 对接清单（已实测确认）

| 用途 | 接口 | 说明 |
|------|------|------|
| 登录 | `POST /restapi/index.php/login` | body `{user, pass}`，返回 `mydms_session` cookie |
| 根文件夹 | `GET /restapi/index.php/folder/{id}/children`（id 省略/0=根） | 子文件夹 + 文档 |
| 下载文档 | `GET /restapi/index.php/document/{id}/content` | 二进制流 |
| 版本列表 | `GET /restapi/index.php/document/{id}/versions` | 判断新版本 |
| 搜索 | `GET /restapi/index.php/search?query=` | 可选 |

认证：登录 cookie 存入进程级 httpx session，后续请求自动携带。

## 六、配置项（.env）

```env
SEEDDMS_URL=http://localhost:8080
SEEDDMS_USER=admin
SEEDDMS_PASS=admin
```

## 七、导入流程

1. 前端 `GET /api/dms/tree` → 后端递归拉文件夹树 + 文档
2. 用户勾选 → `POST /api/dms/import` `{doc_ids:[], folder_ids:[]}`
3. 后端对每个选中项：
   - 未导入 → 下载 → enqueue → 记映射(imported)
   - 已导入但 hash 变了（新版本）→ 下载 → 替换旧数据 → 更新映射(replaced)
   - 已导入且 hash 未变 → 跳过(幂等)
4. 返回 `{imported, replaced, skipped, failed:[...]}`

## 八、边界与异常

- SeedDMS 不可达 → 前端"未连接"提示，接口返回明确错误
- 单个文档失败 → 不阻断批量，单独标记 failed
- 权限 → 复用 SeedDMS 访问过滤（受限账号只见其有读权限的文件夹）
- 超大文件 → 复用 `MAX_UPLOAD_SIZE_MB`，超限跳过

## 九、实施清单（按序）

- [x] 1. 方案文档（本文档）
- [x] 2. config.py 新增 SeedDMS 配置
- [x] 3. db.py 新增 dms_imports 表 + 迁移
- [x] 4. sync_state.py 映射表读写
- [x] 5. seeddms_client.py REST API 封装
- [x] 6. import_service.py 导入编排 + 替换
- [x] 7. api/dms.py 路由 + 注册
- [x] 8. 前端 DmsImport.vue + dms.js + 路由/侧边栏
- [x] 9. 构建 + 端到端验证

## 十、验证结果（2026-09-03）

- 导入：拉取 SeedDMS 文档 → enqueue 完整入库 pipeline → 自动分类「连接器」+ 抽实体 + 记映射 ✅
- 幂等：hash 未变时跳过 ✅
- 脏数据恢复：映射 file_id 指向已删文件时重新入库 ✅
- 替换清理：先删 SQLite（files/chunks/FTS/entities/links）→ 再删 Chroma 向量 → 磁盘图片 ✅
- 权限：普通用户 403、无 token 401、管理员可通过 ✅
- 现有测试 167 → 全绿（测试数据污染已清理）✅

## 十一、已知边界（重要）

SeedDMS REST API **不提供「直接更新文档内容」的接口**——内容版本更新必须走 SeedDMS 的
checkout/checkin 流程（Web UI 或 op.CheckOutDocument.php / op.CheckInDocument.php）。
`POST /document/{id}/attachment` 上传的是「附件」不是「内容新版本」，不会改变
`download_document` 返回的 `getLatestContent()`。

因此伏羲的「自动替换」触发依赖 SeedDMS 里文档内容真实变化（hash 变化检测），
而内容变化由用户在 SeedDMS 侧通过正规 checkin 流程完成。伏羲侧检测到 hash 变化即自动替换。
