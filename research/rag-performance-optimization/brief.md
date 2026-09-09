# Research Brief: RAG 系统综合性能优化

**Date**: 2026-08-20
**Depth**: standard
**Question**: 是否还有优化该 RAG 系统综合性能的办法？

## System Context

工业知识库 RAG 系统（连接器/机械设计领域），当前架构：
- **检索**: BM25 (FTS5 + jieba) + 向量 (ChromaDB + bge-large-zh-v1.5) + 图谱召回 → 动态加权 RRF 融合 → Rerank (SiliconFlow BGE → DeepSeek LLM → 本地 TF-IDF)
- **生成**: DeepSeek Flash → Pro → MiMo 三级降级，同步 POST
- **存储**: SQLite (WAL) + ChromaDB，单机部署
- **嵌入**: BAAI/bge-large-zh-v1.5 本地 CPU 或 SiliconFlow 远程
- **入库**: Stage 注册制，sync (parse→chunk→embed→store→classify→extract) + async (summarize→tag→preindex→semantic→docsim→images)
- **规模**: ~500 实体，~4000+ 关系，文档级万级 chunks

## Scope

**In scope**:
- 检索质量优化（召回率、排序精度、语义匹配）
- 延迟优化（端到端响应时间）
- 嵌入模型选型与优化
- 分块策略优化
- Rerank 策略优化
- 存储层优化
- LLM 调用优化
- 2025-2026 年最新 RAG 技术进展

**Out of scope**:
- 完全重写架构（如迁移到 PostgreSQL/Elasticsearch）
- 多模态 RAG（图片/视频检索）
- 分布式部署方案

## Angles

1. **嵌入模型升级**: 2025-2026 年中文/多语言嵌入模型 SOTA，bge-large-zh-v1.5 的替代方案
2. **分块策略优化**: 语义分块、递归分块、Small-to-Big 等最新分块技术
3. **检索融合优化**: ColBERT、交叉编码器、多阶段检索的最新进展
4. **Rerank 模型升级**: 2025-2026 年 rerank 模型 SOTA，BGE-Reranker 替代方案
5. **Query 改写与扩展**: HyDE、Multi-Query、Step-Back Prompting 等查询优化技术
6. **缓存与预计算**: 语义缓存、预设问题索引、KV 缓存优化
7. **SQLite/存储层优化**: FTS5 调优、索引策略、写入优化
