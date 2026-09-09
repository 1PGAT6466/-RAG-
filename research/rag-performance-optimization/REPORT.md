# RAG 系统综合性能优化研究报告

**日期**: 2026-08-20  
**系统**: 更新RAG框架（工业知识库，连接器/机械设计领域）  
**当前规模**: ~500 实体，~4000+ 关系，万级 chunks，单机部署

---

## 核心结论

该系统有 **7 大类 20+ 项可落地的优化手段**，按投入产出比排序，预计可实现：
- **检索质量**: 召回率提升 10-25%，排序精度提升 5-15%
- **端到端延迟**: 缓存命中 <200ms（当前 3-5s），缓存未命中 1-2s（当前 3-5s）
- **LLM 成本**: 减少 40-70% token 消耗
- **存储**: 向量内存减少 4-8x

---

## 一、嵌入模型升级（检索质量 +10-20%）

### 当前状态
`BAAI/bge-large-zh-v1.5`（326M 参数，1024 维，512 token 上限，C-MTEB ~64.53）

### 推荐升级路径

| 优先级 | 模型 | 参数 | 维度 | 上限 | C-MTEB | 特点 | CPU 可用 |
|--------|------|------|------|------|--------|------|---------|
| **1 (推荐)** | BAAI/bge-m3 | 568M | 1024 | 8192 | ~65+ | 混合检索(dense+sparse+ColBERT)，100+语言 | 可(量化) |
| 2 | stella-mrl-large-zh-v3.5 | ~326M | 1792 | 512 | 68.55 | Matryoshka(可截断维度)，中文特化 | 可 |
| 3 | gte-Qwen2-7B-instruct | 7B | 3584 | 32K | **72.05** | C-MTEB #1，最高质量 | 需GPU |

### 关键发现
- **BGE-M3** 是最佳 CPU 可用升级：MIT 协议，8192 token（当前 512），同时输出 dense+sparse+ColBERT 三种向量 [1][2]
- **Matryoshka 表示学习**：维度从 768 截断到 256，存储/计算减少 3x，质量损失 <2% [3]
- **BGE-M3 的 sparse 分量**天然替代 BM25，对中文工业术语匹配更好 [4]
- **无 "bge-large-zh-v2.0"**：BGE 家族已转向多语言和 LLM 模型 [5]

### 落地建议
1. 替换为 `bge-m3`，利用其 hybrid retrieval（dense+sparse）简化当前三路召回架构
2. 评估 Matryoshka 截断到 512 维的精度损失
3. 对本地 CPU 推理做 ONNX INT8 量化（2-4x 加速，<1% 质量损失）[6]

---

## 二、分块策略优化（检索质量 +3-7%）

### 当前状态
`chunker.py` 按段落分块，CHUNK_SIZE=800 字符，CHUNK_OVERLAP=100

### 研究发现

| 策略 | 检索提升 | 成本 | 适用场景 |
|------|---------|------|---------|
| **RecursiveCharacterTextSplitter 200-token/无重叠** | 88.1% recall, 最佳 IoU | 极低 | 通用默认 |
| **Late Chunking (Jina AI)** | +3.5% nDCG@10 | 零(LLM 无关) | 跨 chunk 引用 |
| **Contextual Retrieval (Anthropic)** | 减少 67% 检索失败 | ~$1/MM tokens | 高价值 KB |
| **Small-to-Big / RAPTOR** | +20% 多跳推理 | 中(LLM 摘要) | 长文档 |

### 关键发现
- **chunk overlap 通常不提升检索**，反而降低 IoU（冗余 token）[7]
- **OpenAI 默认 800-token/400-overlap 是最差配置之一** [7]
- **Late Chunking 零成本**：先过 transformer 再分块，每个 chunk 编码包含全文上下文 [8]
- **技术 PDF 的表格应作为原子单元**，不要跨表拆分 [9]

### 落地建议
1. 将 CHUNK_SIZE 从 800 降到 400-500 字符，去掉 overlap
2. 表格检测：识别表格边界，作为不可拆分原子
3. 评估 Late Chunking（需切换到支持长上下文的嵌入模型如 bge-m3 的 8192 token）
4. 可选：为每个 chunk 生成 50-100 token 上下文前缀（Contextual Retrieval）

---

## 三、检索融合与 Rerank 优化（排序精度 +5-15%）

### 当前状态
BM25 + 向量 + 图谱 → 动态加权 RRF → Rerank (SiliconFlow BGE → DeepSeek LLM → 本地 TF-IDF)

### 2025 SOTA 三阶段检索架构

```
Stage 1: BM25 + Dense + (可选 SPLADE) → RRF 融合     <50ms, >95% recall@1000
Stage 2: 轻量 cross-encoder rerank (bge-reranker-v2-m3)  <500ms
Stage 3: (可选) LLM rerank (仅精确度要求极高时)           <2s
```

### 关键发现
- **BGE-Reranker-v2-m3** 是最佳自托管多语言 reranker（18.6M 月下载，中文优秀，Apache 2.0）[10]
- **ColBERTv2** 是质量-延迟最优的 dense retrieval 方案，已集成到 Sentence-Transformers v6 [11]
- **SPLADE inference-free** 变体可在标准倒排索引上实现学习型稀疏检索，零 GPU 成本 [12]
- **CPU 部署**：ONNX 导出 bge-reranker-base，100 文档 ~2-5s [13]

### 落地建议
1. 将 SiliconFlow Rerank 替换为自托管 `bge-reranker-v2-m3`（ONNX 量化）
2. 考虑用 BGE-M3 的 ColBERT 分量替代当前向量检索（token 级匹配，对表格/规格数据更好）
3. 当前三路召回架构可保留，但图谱召回的权重可进一步调优

---

## 四、Query 改写与扩展（召回率 +5-15%）

### 推荐技术（按成本/收益排序）

| 技术 | 效果 | 延迟开销 | 推荐度 |
|------|------|---------|--------|
| **Rewrite-Retrieve-Read** | +2-5% | +0.1-0.5s | ★★★★★ |
| **HyDE** | +5-10% 召回 | +0.3-1.0s | ★★★★ |
| **Adaptive-RAG** | +2-5%，-20-40% 成本 | 负(减少) | ★★★★ |
| Multi-Query | +10-15% 相关性 | +1-3s | ★★★ |
| Query Decomposition | +10-20% F1(多跳) | +1-4s | ★★★ |

### 关键发现
- **Rewrite-Retrieve-Read 性价比最高**：一次小 LLM 调用，+0.1-0.5s，稳定提升 2-5% [14]
- **HyDE 最适合零样本**：LLM 生成假设文档用于检索，但会放大幻觉 [15]
- **Adaptive-RAG 路由查询复杂度**：简单查询跳过检索，节省 20-40% 成本 [16]
- **核心原则**：先调优基础 RAG，只在测量到退化时才加查询优化；延迟会叠加 [17]

### 落地建议
1. **立即实施**：在 `chat/router.py` 的 `classify_intent` 中加入查询改写（用 DeepSeek Flash 重写为更精确的检索查询）
2. **中期**：实现 Adaptive-RAG 路由——简单闲聊不检索，复杂问题走多步检索
3. **可选**：对专业查询（型号/标准号）实现 HyDE，生成假设技术文档用于检索

---

## 五、缓存与预计算（延迟 20-100x）

### 推荐技术

| 技术 | 延迟改善 | 实施难度 | 推荐度 |
|------|---------|---------|--------|
| **SSE 流式输出** | 感知延迟 -60-80% | 极低(1h) | ★★★★★ |
| **SQLite FTS5 调优** | 检索质量 +10-30% | 极低(2h) | ★★★★★ |
| **语义缓存** | 命中时 20-100x | 低(4h) | ★★★★ |
| **Prompt 压缩** | token -50-70% | 低(4h) | ★★★★ |
| **结构化输出** | 解析开销 -15-35% | 低(2h) | ★★★ |
| **向量量化 (SQ8)** | 内存 -8x | 极低(1h) | ★★★ |

### 关键发现
- **语义缓存 (GPTCache)**：SQLite+FAISS+ONNX 全本地，相似查询缓存 LLM 响应，命中时 ~50ms vs 3-5s [18]
- **LLMLingua-2**：BERT-base 模型压缩 prompt，20x 压缩率，CPU 上 <2GB RAM，ACL 2024 同行评审 [19]
- **SQLite FTS5 trigram tokenizer** 对中文 CJK 分词至关重要，但索引增大 3-5x [20]
- **SQ8 标量量化**：向量内存 8 倍压缩，<2% 召回损失 [21]

### 落地建议
1. **已实施**: SSE 流式输出（`/api/chat/stream`）
2. **立即实施**: FTS5 添加 trigram tokenizer 支持中文子串匹配
3. **短期**: 语义缓存——对重复/相似查询缓存 LLM 响应
4. **短期**: Prompt 压缩——在发送给 LLM 前压缩检索上下文
5. **中期**: 向量 SQ8 量化减少内存占用

---

## 六、LLM 调用优化

### 当前状态
DeepSeek Flash → Pro → MiMo 三级降级，同步 POST

### 优化方向

| 优化 | 效果 | 已实施 |
|------|------|--------|
| SSE 流式输出 | 感知延迟 -60-80% | ✅ 已实施 |
| 结构化输出 (JSON mode) | token 浪费 -10-30% | ❌ |
| Prompt 压缩 | token -50-70% | ❌ |
| 语义缓存 | 命中时跳过 LLM | ❌ |
| DeepSeek Flash 优先 | 非推理模型，快 3-5x | ✅ 已实施 |

### 落地建议
1. 对实体抽取、标签生成等结构化任务使用 JSON mode（减少重试和解析开销）
2. 对 RAG 对话的参考资料上下文做 LLMLingua-2 压缩（2000→400 tokens）
3. 语义缓存覆盖 `/api/chat` 端点

---

## 七、存储层优化

### SQLite FTS5 调优清单

| 参数 | 当前 | 建议 | 效果 |
|------|------|------|------|
| tokenizer | unicode61 | unicode61 + trigram | 中文子串匹配 |
| detail | default(full) | column | 索引 -30-40% |
| content | default(存储原文) | contentless | FTS 表 -60-80% |
| optimize | 未调用 | 批量入库后调用 | 查询加速 |

### 向量索引优化

| 方案 | 适用规模 | 查询延迟 | 内存 |
|------|---------|---------|------|
| 当前: ChromaDB | <100k | ~1-5ms | ~300MB |
| 优化: HNSW Flat | <1M | 0.1-1ms | ~500MB |
| 极致: HNSW+SQ8 | 1M-10M | 0.5-5ms | ~60MB/100k |

---

## 优先实施路线图

| 阶段 | 优化项 | 预计工时 | 预计收益 |
|------|--------|---------|---------|
| **Phase 1 (本周)** | FTS5 trigram + FTS5 调优 | 2h | 检索质量 +10-30% |
| | Chunk 策略调整(200-400 token, 去 overlap) | 1h | 检索精度 +3-7% |
| | Query 改写(Rewrite-Retrieve-Read) | 2h | 召回率 +2-5% |
| **Phase 2 (下周)** | 嵌入模型升级 bge-m3 | 4h | 检索质量 +10-20% |
| | Rerank 升级 bge-reranker-v2-m3 (ONNX) | 4h | 排序精度 +5-15% |
| | 语义缓存 (GPTCache) | 4h | 重复查询 20-100x |
| **Phase 3 (月内)** | Prompt 压缩 (LLMLingua-2) | 4h | token -50-70% |
| | 向量 SQ8 量化 | 1h | 内存 -8x |
| | Adaptive-RAG 查询路由 | 4h | 成本 -20-40% |
| | Contextual Retrieval 前缀 | 8h | 检索失败 -67% |

---

## Open Questions

1. bge-m3 的 hybrid retrieval (dense+sparse+ColBERT) 是否能替代当前 BM25 + 向量 + 图谱的三路架构？
2. 工业领域（连接器/机械）的 C-MTEB 表现是否与通用基准一致？需要在自有数据集上评估
3. Late Chunking 与 bge-m3 的 8192 token 上下文结合效果如何？
4. 语义缓存的 similarity_threshold 在工业查询场景下的最优值是多少？

---

## Sources

[1] BAAI/bge-m3 HuggingFace model card, https://huggingface.co/BAAI/bge-m3, arXiv:2402.03216 (accessed 2026-08-20)  
[2] BGE family collection, https://huggingface.co/collections/BAAI/bge (accessed 2026-08-20)  
[3] Matryoshka Representation Learning, nomic-embed-text-v1.5, https://huggingface.co/nomic-ai/nomic-embed-text-v1.5, arXiv:2205.13147 (accessed 2026-08-20)  
[4] BGE-M3 hybrid retrieval documentation, https://huggingface.co/BAAI/bge-m3 (accessed 2026-08-20)  
[5] BGE collection timeline, https://huggingface.co/collections/BAAI/bge (accessed 2026-08-20)  
[6] sentence-transformers ONNX quantization docs (accessed 2026-08-20)  
[7] Chroma Technical Report, "Evaluating Chunking Strategies for Retrieval", https://research.trychroma.com/evaluating-chunking, Jul 2024 (accessed 2026-08-20)  
[8] Late Chunking, Jina AI, arXiv:2409.04701v3, Sep 2024 (accessed 2026-08-20)  
[9] Anthropic, "Introducing Contextual Retrieval", https://www.anthropic.com/news/contextual-retrieval, Sep 2024 (accessed 2026-08-20)  
[10] BGE-Reranker-v2-m3 HuggingFace model card, https://huggingface.co/BAAI/bge-reranker-v2-m3 (accessed 2026-08-20)  
[11] ColBERTv2, arXiv:2112.01488, Sentence-Transformers v6 Multi-Vector Encoder docs (accessed 2026-08-20)  
[12] SPLADE, arXiv:2107.05720, Sentence-Transformers SparseEncoder docs (accessed 2026-08-20)  
[13] Sentence-Transformers efficiency docs, bge-reranker ONNX deployment (accessed 2026-08-20)  
[14] Ma et al., "Query Rewriting for Retrieval-Augmented LLMs", arXiv:2305.14283, EMNLP 2023 (accessed 2026-08-20)  
[15] Gao et al., "Precise Zero-Shot Dense Retrieval without Relevance Labels", arXiv:2212.10496, Dec 2022 (accessed 2026-08-20)  
[16] Jeong et al., "Adaptive-RAG", arXiv:2403.14403, NAACL 2024 (accessed 2026-08-20)  
[17] Gao et al., "Modular RAG", arXiv:2407.21059, Jul 2024 (accessed 2026-08-20)  
[18] GPTCache, https://github.com/zilliztech/GPTCache (accessed 2026-08-20)  
[19] LLMLingua-2, Microsoft, https://github.com/microsoft/LLMLingua, ACL 2024 (accessed 2026-08-20)  
[20] SQLite FTS5 documentation, https://sqlite.org/fts5.html (accessed 2026-08-20)  
[21] FAISS vector codecs, https://github.com/facebookresearch/faiss/wiki/Faiss-indexes (accessed 2026-08-20)
