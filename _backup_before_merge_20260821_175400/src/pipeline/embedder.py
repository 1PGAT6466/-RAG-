"""
Embedding 引擎
=============
混合策略（方案 C）：
  - 文本数 <= 阈值：本地 sentence-transformers（离线、零成本）
  - 文本数 > 阈值：SiliconFlow API 远程（快，大文档秒级）
  - 本地模型按 EMBEDDING_MODEL 配置选择 large/small，均离线可用
"""
import struct
import logging
import os
import numpy as np

logger = logging.getLogger("rag.embedder")

_embedder = None
_LOCAL_MODEL_PATH = None
_LOCAL_MODEL_DIM = None
# 本地模型不可用缓存：加载失败一次后不再重复尝试（避免流式入库每批都白加载）
_LOCAL_UNAVAILABLE = False
_LOCAL_CHECKED = False
_embedder_lock = __import__('threading').Lock()

# 超过该 chunk 数自动切远程（本地 CPU 向量化大文档太慢）
from config import EMBED_REMOTE_THRESHOLD as REMOTE_THRESHOLD


def _get_local_path() -> str:
    """根据 EMBEDDING_MODEL 配置解析本地模型路径（large/small）。

    带权重文件存在性校验：目录存在但缺 model.safetensors/pytorch_model.bin
    时视为「无可用本地模型」，返回空串（避免每次 encode 重复加载失败）。"""
    global _LOCAL_MODEL_PATH, _LOCAL_CHECKED
    if _LOCAL_CHECKED:
        return _LOCAL_MODEL_PATH or ""

    from config import EMBEDDING_MODEL, BASE_DIR, DATA_DIR
    os.environ.setdefault("HF_HOME", str(DATA_DIR))

    from pathlib import Path
    # EMBEDDING_MODEL 形如 "BAAI/bge-small-zh-v1.5"，转成 HF 目录名 "BAAI--bge-small-zh-v1.5"
    model_dir_name = EMBEDDING_MODEL.replace("/", "--")
    base = DATA_DIR / f"models/models--{model_dir_name}/snapshots"
    if base.exists():
        snaps = sorted(base.iterdir(), reverse=True)
        for s in snaps:
            # 跳过 main 指针，找真实 hash 快照目录
            if s.is_dir() and any(s.iterdir()):
                if _has_weights(s):
                    _LOCAL_MODEL_PATH = str(s)
                    _LOCAL_CHECKED = True
                    return str(s)
                # 目录在但无权重：不 return，继续搜旧路径（权重可能在 models/models/ 下）
                logger.warning(f"标准路径缺权重（{s}），继续搜旧路径...")

    # 兼容旧路径 1：models/models/{dir}/snapshots（旧框架遗留，权重实际在这里）
    legacy = DATA_DIR / f"models/models/{model_dir_name}/snapshots"
    if legacy.exists():
        snaps = sorted(legacy.iterdir(), reverse=True)
        for s in snaps:
            if s.is_dir() and _has_weights(s):
                _LOCAL_MODEL_PATH = str(s)
                _LOCAL_CHECKED = True
                return str(s)

    # 兼容旧路径 2：models/models/{dir}/（无 snapshots 子目录，权重直接在目录下）
    legacy2 = DATA_DIR / f"models/models/{model_dir_name}"
    if legacy2.exists() and _has_weights(legacy2):
        _LOCAL_MODEL_PATH = str(legacy2)
        _LOCAL_CHECKED = True
        return str(legacy2)

    _LOCAL_CHECKED = True
    _LOCAL_MODEL_PATH = ""
    return ""


def _has_weights(model_dir) -> bool:
    """检查模型目录里是否有真正的权重文件（非仅 config）"""
    from pathlib import Path
    d = Path(model_dir)
    if not d.is_dir():
        return False
    for f in d.iterdir():
        if f.name in ("model.safetensors", "pytorch_model.bin", "model.bin", "pytorch_model.bin.index.json"):
            return True
        if f.name.startswith("model-") and f.name.endswith(".safetensors"):  # 分片 safetensors
            return True
    return False


def _encode_local(texts: list[str], progress_cb=None) -> list[bytes]:
    """本地 sentence-transformers（可选 ONNX INT8 量化加速）"""
    global _embedder, _LOCAL_MODEL_DIM, _LOCAL_UNAVAILABLE
    if _LOCAL_UNAVAILABLE:
        raise RuntimeError("本地模型不可用（已缓存）")
    if _embedder is None:
        with _embedder_lock:
            if _embedder is None:  # double-checked locking
                from config import EMBEDDING_DEVICE, EMBEDDING_ONNX
                path = _get_local_path()
                logger.info(f"加载本地模型: {path}")
                if EMBEDDING_ONNX == "1":
                    try:
                        from optimum.onnxruntime import ORTModelForFeatureExtraction
                        from sentence_transformers import SentenceTransformer
                        # ONNX 量化推理：2-4x CPU 加速，INT8 量化
                        _embedder = SentenceTransformer(path, device=EMBEDDING_DEVICE,
                            model_kwargs={"export": True, "provider": "CPUExecutionProvider"})
                        logger.info("使用 ONNX 量化推理")
                    except Exception as e:
                        logger.warning(f"ONNX 加载失败，回退标准模式: {e}")
                        from sentence_transformers import SentenceTransformer
                        _embedder = SentenceTransformer(path, device=EMBEDDING_DEVICE)
                else:
                    from sentence_transformers import SentenceTransformer
                    _embedder = SentenceTransformer(path, device=EMBEDDING_DEVICE)
                _LOCAL_MODEL_DIM = _embedder.get_embedding_dimension()
                logger.info(f"本地模型就绪，维度={_LOCAL_MODEL_DIM}")

    # 大 batch 在 CPU 上会长时间阻塞无反馈，分批编码并回调进度
    batch_size = 64
    all_vecs = []
    for i in range(0, len(texts), batch_size):
        batch = texts[i:i + batch_size]
        vecs = _embedder.encode(batch, normalize_embeddings=True, show_progress_bar=False)
        all_vecs.extend(_pack(v) for v in vecs)
        if progress_cb and len(texts) > batch_size:
            progress_cb(min(i + batch_size, len(texts)), len(texts))
    return all_vecs


def _encode_remote(texts: list[str], progress_cb=None) -> list[bytes]:
    """SiliconFlow API（bge-large-zh-v1.5，1024 维）"""
    import httpx
    from config import SILICONFLOW_API_KEY
    url = "https://api.siliconflow.cn/v1/embeddings"
    if not SILICONFLOW_API_KEY:
        raise ValueError("缺少 SILICONFLOW_API_KEY")

    # bge-large-zh max_seq_length=512 token，中文约 1 字≈1 token；
    # 超长会触发 SiliconFlow 400 (code 20015)，故截断到安全长度
    MAX_CHARS = 400
    texts = [t[:MAX_CHARS] for t in texts]

    # 分批，每批最多 32 条（SiliconFlow 限制）
    all_embeddings = []
    batch_size = 32
    with httpx.Client(timeout=120) as client:
        for i in range(0, len(texts), batch_size):
            batch = texts[i:i + batch_size]
            resp = client.post(
                url,
                headers={"Authorization": f"Bearer {SILICONFLOW_API_KEY}", "Content-Type": "application/json"},
                json={"model": "BAAI/bge-large-zh-v1.5", "input": batch},
            )
            resp.raise_for_status()
            data = resp.json()
            batch_embs = [np.array(d["embedding"], dtype=np.float32) for d in data["data"]]
            all_embeddings.extend(batch_embs)
            if progress_cb and len(texts) > batch_size:
                progress_cb(min(i + batch_size, len(texts)), len(texts))
                logger.info(f"远程向量化进度: {min(i + batch_size, len(texts))}/{len(texts)}")
    return [_pack(v) for v in all_embeddings]


def encode(texts: list[str], progress_cb=None) -> list[bytes]:
    """文本列表 → embedding bytes。

    混合策略：大 batch（> REMOTE_THRESHOLD）走远程 SiliconFlow，否则本地。
    progress_cb(done, total)：可选进度回调，逐批上报（本地/远程均支持），
    用于入库时向任务状态实时上报进度（大 PDF 向量化不再干等无反馈）。
    """
    global _LOCAL_UNAVAILABLE
    n = len(texts)
    if n == 0:
        return []

    # 方案 C：超过阈值切远程（大文档快）
    if n > REMOTE_THRESHOLD:
        try:
            logger.info(f"文本数 {n} > 阈值 {REMOTE_THRESHOLD}，切换 SiliconFlow 远程向量化")
            return _encode_remote(texts, progress_cb)
        except Exception as e:
            logger.warning(f"远程向量化失败，降级本地: {e}")
            try:
                return _encode_local(texts, progress_cb)
            except Exception:
                pass
            raise

    # 本地路径存在则尝试本地，失败降级远程（本地权重文件可能缺失）
    p = _get_local_path()
    if p and not _LOCAL_UNAVAILABLE:
        try:
            return _encode_local(texts, progress_cb)
        except Exception as e:
            logger.warning(f"本地模型加载失败，降级远程 SiliconFlow: {e}")
            _LOCAL_UNAVAILABLE = True  # 缓存失败，后续直接远程
    try:
        return _encode_remote(texts, progress_cb)
    except Exception as e:
        logger.error(f"Embedding 失败（本地+远程均不可用）: {e}")
        raise


def encode_query(text: str) -> bytes:
    return encode([text])[0]


def _pack(vec: np.ndarray) -> bytes:
    return struct.pack(f"{len(vec)}f", *vec)


def _unpack(data: bytes) -> np.ndarray:
    return np.array(struct.unpack(f"{len(data)//4}f", data), dtype=np.float32)


def cosine_similarity(vec_a: bytes, vec_b: bytes) -> float:
    a = _unpack(vec_a)
    b = _unpack(vec_b)
    norm_a = np.linalg.norm(a)
    norm_b = np.linalg.norm(b)
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return float(np.dot(a, b) / (norm_a * norm_b))


# === SQ8 标量量化（向量存储 4 倍压缩）===
# float32 (4 bytes/dim) → int8 (1 byte/dim) + 8 bytes scale/offset
# 适用场景：向量存储空间紧张时，用 SQ8 压缩到 1/4，<2% 召回损失

def quantize_sq8(vec: bytes) -> tuple[bytes, float, float]:
    """SQ8 量化：float32 向量 → (int8 bytes, min_val, scale)"""
    a = _unpack(vec)
    vmin, vmax = float(a.min()), float(a.max())
    if vmax == vmin:
        return b'\x00' * len(a), vmin, 1.0
    scale = (vmax - vmin) / 255.0
    quantized = np.clip((a - vmin) / scale, 0, 255).astype(np.uint8)
    return quantized.tobytes(), vmin, scale


def dequantize_sq8(data: bytes, vmin: float, scale: float) -> np.ndarray:
    """SQ8 反量化：(int8 bytes, min, scale) → float32 向量"""
    q = np.frombuffer(data, dtype=np.uint8).astype(np.float32)
    return q * scale + vmin


def cosine_similarity_sq8(vec_a: bytes, vmin_a: float, scale_a: float,
                          vec_b: bytes, vmin_b: float, scale_b: float) -> float:
    """SQ8 量化向量的余弦相似度（反量化后计算）"""
    a = dequantize_sq8(vec_a, vmin_a, scale_a)
    b = dequantize_sq8(vec_b, vmin_b, scale_b)
    norm_a = np.linalg.norm(a)
    norm_b = np.linalg.norm(b)
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return float(np.dot(a, b) / (norm_a * norm_b))
