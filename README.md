# 伏羲 RAG 智能知识库系统

> 仓库/工作目录别名：「更新RAG框架」。与「伏羲 RAG」为同一系统。

<p align="center">
  <strong>企业级 RAG（检索增强生成）知识库系统</strong><br/>
  支持多格式文档解析 · 混合检索 · 图谱召回 · LLM 对话 · 插件扩展
</p>

---

## 📖 项目简介

伏羲是一个基于 **FastAPI + Vue 3 + ChromaDB** 构建的智能知识库系统，核心能力是将企业内部文档（PDF/Word/Excel/PPT 等）转化为可对话的知识库。用户通过自然语言提问，系统自动检索相关文档片段并调用大语言模型生成带引用标注的专业回答。

### 核心特性

- 🔍 **混合检索**：BM25 全文检索 + 向量语义检索 + 图谱召回，RRF 融合排序
- 📄 **多格式解析**：PDF（含双栏重排/OCR）、Word、Excel、PPT、TXT、CSV 等
- 🧠 **三模式对话**：知识库检索 / 自由闲聊 / 联网搜索，智能意图路由
- 🕸️ **知识图谱**：自动提取实体与关系，支持图谱可视化与导航召回
- 🔌 **插件系统**：工具型 + 工作流型插件，支持 on_ingest / on_search 钩子
- 🛡️ **企业级特性**：JWT 认证 + RBAC 权限 + 审计日志 + 反馈降权 + 语义缓存
- 📊 **Wiki 知识库**：基于文档内容自动编译 Wiki 页面，支持双链与版本历史

---

## 🏗️ 技术架构

```
┌─────────────────────────────────────────────────────────┐
│                    Frontend (Vue 3)                      │
│         Element Plus · D3 Graph · Pinia · Vite           │
└────────────────────────┬────────────────────────────────┘
                         │ HTTP / SSE
┌────────────────────────┴────────────────────────────────┐
│                    Backend (FastAPI)                      │
│  ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌───────────┐  │
│  │ Auth/JWT  │ │  Chat    │ │ Pipeline │ │  Plugins  │  │
│  │ RBAC     │ │ Router   │ │ Engine   │ │  MCP      │  │
│  └──────────┘ └──────────┘ └──────────┘ └───────────┘  │
│  ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌───────────┐  │
│  │Retrieval │ │  LLM     │ │  Graph   │ │  Storage  │  │
│  │BM25+Vec  │ │Fallback  │ │ Recall   │ │ SQLite    │  │
│  │+Rerank   │ │Chain     │ │ Entity   │ │ ChromaDB  │  │
│  └──────────┘ └──────────┘ └──────────┘ └───────────┘  │
└─────────────────────────────────────────────────────────┘
```

### 技术栈

| 层级 | 技术 |
|------|------|
| **后端框架** | FastAPI + Uvicorn |
| **前端框架** | Vue 3 + Element Plus + Vite |
| **向量数据库** | ChromaDB（可选，有 SQLite 回退） |
| **关系数据库** | SQLite（WAL 模式） |
| **全文检索** | SQLite FTS5 + jieba 分词 |
| **Embedding** | BAAI/bge-large-zh-v1.5（本地） |
| **LLM** | DeepSeek / MiMo（多级降级链） |
| **Rerank** | SiliconFlow BGE-Reranker → DeepSeek → TF-IDF |
| **PDF 解析** | PyMuPDF + RapidOCR（可选） |

---

## 🚀 快速开始

### 环境要求

- Python 3.10+
- Node.js 18+
- npm 或 pnpm

### 1. 克隆仓库

```bash
git clone https://github.com/1PGAT6466/-RAG-.git
cd -RAG-
```

### 2. 安装后端依赖

```bash
pip install -r requirements.txt
```

### 3. 配置环境变量

复制并编辑 `.env` 文件：

```bash
cp .env.example .env
```

核心配置项：

```env
# 服务配置
HOST=127.0.0.1
PORT=8099

# LLM 配置（DeepSeek）
DEEPSEEK_API_KEY=sk-your-key-here
DEEPSEEK_BASE_URL=https://api.deepseek.com
DEEPSEEK_MODEL=deepseek-v4-pro
DEEPSEEK_FLASH_MODEL=deepseek-v4-flash

# MiMo 备选
MIMO_API_KEY=your-mimo-key
MIMO_BASE_URL=https://token-plan-cn.xiaomimimo.com/v1
MIMO_MODEL=mimo-v2.5

# 向量检索（可选）
SILICONFLOW_API_KEY=your-key
EMBEDDING_MODEL=BAAI/bge-large-zh-v1.5
EMBEDDING_DEVICE=cpu

# 数据库
DB_PATH=data/rag.db

# 跨域（开发环境）
CORS_ORIGINS=http://localhost:3000,http://127.0.0.1:3000
```

### 4. 启动后端

```bash
python server.py
# 服务启动在 http://127.0.0.1:8099
```

### 5. 启动前端（开发模式）

```bash
cd frontend
npm install
npm run dev
# 前端启动在 http://localhost:3000，自动代理 /api → :8099
```

### 6. 生产部署

```bash
# 构建前端
cd frontend
npm run build

# 启动后端（自动托管前端静态文件）
python server.py
# 访问 http://127.0.0.1:8099
```

---

## 📁 项目结构

```
├── server.py               # FastAPI 入口，lifespan 初始化，SPA 回退
├── config.py               # 所有配置项（从 .env 读取）
├── requirements.txt        # Python 依赖
│
├── src/
│   ├── api/                # HTTP 路由（/api/*）
│   │   ├── chat.py         # 对话接口（SSE 流式）
│   │   ├── documents.py    # 文档上传/管理
│   │   ├── search.py       # 检索接口
│   │   ├── wiki.py         # Wiki 知识库
│   │   └── ...
│   ├── auth/               # JWT 认证 + RBAC 权限
│   ├── chat/               # LLM 对话引擎
│   │   ├── engine.py       # 生成 + 引用标注
│   │   ├── orchestrator.py # 三模式路由调度
│   │   ├── router.py       # 意图分类
│   │   └── grounding.py    # 答案校验
│   ├── pipeline/           # 文档入库引擎
│   │   ├── engine.py       # Stage 流水线
│   │   ├── parser.py       # 多格式解析
│   │   ├── chunker.py      # 智能切块
│   │   ├── backends.py     # 解析后端抽象层
│   │   └── embedder.py     # 向量化
│   ├── retrieval/          # 混合检索
│   │   ├── search.py       # BM25 + Vector + Graph → RRF
│   │   ├── rerank.py       # 重排序
│   │   └── graph_recall.py # 图谱召回
│   ├── storage/            # 数据存储层
│   │   ├── db.py           # SQLite 连接管理
│   │   ├── chunks.py       # 切块 CRUD
│   │   ├── files.py        # 文件元数据
│   │   └── chroma_store.py # ChromaDB 向量存储
│   ├── extraction/         # 实体抽取
│   ├── plugins/            # 插件系统
│   └── mcp/                # MCP 市场集成
│
├── frontend/
│   ├── src/
│   │   ├── views/          # 页面组件
│   │   │   ├── ChatView.vue      # 对话页面
│   │   │   ├── DocumentsView.vue # 文档管理
│   │   │   ├── GraphView.vue     # 图谱可视化
│   │   │   ├── WikiView.vue      # Wiki 知识库
│   │   │   └── ...
│   │   ├── components/     # 通用组件
│   │   ├── stores/         # Pinia 状态管理
│   │   └── api/            # API 调用封装
│   └── public/
│       └── pdf-viewer.html # PDF 预览器
│
├── plugins/                # 插件目录
│   ├── example-plugin/     # 示例插件
│   └── translate-plugin/   # 翻译插件
│
├── scripts/                # 运维脚本
│   ├── smoke_test.py       # 综合冒烟测试（22 项）
│   ├── retrieval_benchmark.py # 检索评测
│   ├── backup.py           # 数据备份
│   ├── restore.py          # 数据恢复
│   └── user_manage.py      # 用户管理
│
├── data/                   # 运行时数据（gitignored）
│   ├── rag.db              # SQLite 数据库
│   ├── uploads/            # 上传的原始文件
│   └── images/             # 提取的图片
│
└── docs/                   # 项目文档
    └── structure/          # 系统结构文档
```

---

## 🔧 核心功能

### 1. 文档入库

支持格式：PDF、Word（.docx）、Excel（.xlsx/.xls）、PPT（.pptx）、TXT、CSV、Markdown

```bash
# 通过 API 上传
curl -X POST http://127.0.0.1:8099/api/upload \
  -H "Authorization: Bearer <token>" \
  -F "file=@document.pdf"
```

入库流水线：
```
解析 → 切块 → 向量化 → 存储 → 分类 → 实体抽取 → 摘要 → 标签
```

### 2. 混合检索

```bash
# 检索
curl -X POST http://127.0.0.1:8099/api/search \
  -H "Content-Type: application/json" \
  -d '{"query": "连接器镀金层厚度要求", "top_k": 5}'
```

检索流程：
```
查询 → BM25(FTS5) + Vector(ChromaDB) + Graph(实体导航)
     → RRF 融合 → 精确匹配加权 → Rerank → 返回
```

### 3. 智能对话

```bash
# 流式对话（SSE）
curl -X POST http://127.0.0.1:8099/api/chat/stream \
  -H "Authorization: Bearer <token>" \
  -H "Content-Type: application/json" \
  -d '{"query": "FAKRA连接器的特点是什么？"}'
```

三模式路由：
- **知识库模式**：检索 + LLM 生成带引用回答
- **闲聊模式**：直接 LLM 对话
- **联网模式**：Tavily 搜索 + LLM 总结

### 4. 知识图谱

自动从文档中抽取实体（连接器、材料、标准、工艺等）和关系，支持：
- 图谱可视化（D3 力导向图）
- 图谱召回（基于实体导航的检索增强）

### 5. Wiki 知识库

基于入库文档自动编译 Wiki 页面：
- 支持双链（页面间引用）
- 版本历史（可回滚）
- LLM 自动编译 + 人工编辑

---

## ⚙️ 配置说明

### Feature Flags

所有开关在 `config.py` 中定义，通过 `.env` 配置（`"1"` 启用 / `"0"` 禁用）：

| 开关 | 说明 | 默认 |
|------|------|------|
| `RAG_CHROMA` | ChromaDB 向量存储 | ON |
| `RAG_JIEBA` | jieba 中文分词 | ON |
| `RAG_DYNAMIC_RANKING` | 动态 BM25/Vector 权重 | ON |
| `RAG_RERANK` | 融合后重排序 | ON |
| `RAG_GRAPH_RECALL` | 图谱召回 | ON |
| `RAG_ENTITY_EXTRACT` | 规则实体抽取 | ON |
| `RAG_ENTITY_LLM` | LLM 实体抽取 | OFF |
| `RAG_PDF_OCR` | 扫描件 OCR | auto |
| `RAG_STREAM_INGEST` | 大 PDF 流式入库 | ON |
| `RAG_FEEDBACK_ENABLE` | 反馈降权 | OFF |

### LLM 降级链

```
对话生成：DeepSeek Flash → DeepSeek Pro → MiMo
重排序：  SiliconFlow BGE-Reranker → DeepSeek LLM → 本地 TF-IDF
Embedding：本地 bge-large-zh → SiliconFlow 远程 API
```

---

## 🧪 测试

```bash
# 综合冒烟测试（需要服务运行 + 有数据）
python scripts/smoke_test.py

# 指定远程地址
python scripts/smoke_test.py --base http://192.168.x.x:8099

# 检索评测（离线）
python scripts/retrieval_benchmark.py
```

---

## 📦 备份与恢复

```bash
# 备份（SQLite 快照 + ChromaDB + 上传文件）
python scripts/backup.py

# 恢复
python scripts/restore.py backups/rag-XXXXXXXX.zip
```

---

## 🔌 插件开发

插件目录结构：

```
plugins/my-plugin/
├── manifest.json    # 插件清单（必须包含 api_version: "1"）
└── main.py          # 入口脚本
```

manifest.json 示例：

```json
{
  "name": "my-plugin",
  "version": "1.0.0",
  "api_version": "1",
  "kind": "tool",
  "display_name": "我的插件",
  "description": "插件描述",
  "entry": "main.py",
  "tools": [
    {
      "name": "my_method",
      "description": "方法描述",
      "parameters_schema": {
        "type": "object",
        "properties": {
          "input": {
            "type": "string",
            "description": "输入参数"
          }
        },
        "required": ["input"]
      }
    }
  ]
}
```

插件类型：
- **tool**：工具型，可被用户手动调用
- **workflow**：工作流型，挂载到入库/检索节点自动执行

钩子：
- `on_ingest`：文件入库后触发
- `on_search`：检索结果返回前触发（可修改/过滤结果）

---

## 🛡️ 安全

- **认证**：JWT Token（sessionStorage，关闭浏览器即失效）
- **权限**：RBAC（admin / user 两级）
- **密码**：bcrypt 慢哈希（旧 sha256 自动透明升级）
- **限流**：登录接口 IP 级限流
- **审计**：关键操作写入 audit_log 表

---

## 📄 License

MIT License

---

## 🙏 致谢

- [FastAPI](https://fastapi.tiangolo.com/) — 后端框架
- [Vue 3](https://vuejs.org/) — 前端框架
- [ChromaDB](https://www.trychroma.com/) — 向量数据库
- [PyMuPDF](https://pymupdf.readthedocs.io/) — PDF 解析
- [jieba](https://github.com/fxsjy/jieba) — 中文分词
- [DeepSeek](https://deepseek.com/) — LLM 服务
- [BAAI/bge](https://huggingface.co/BAAI/bge-large-zh-v1.5) — Embedding 模型
