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


def _encode_local(texts: list[str]) -> list[bytes]:
    """本地 sentence-transformers"""
    global _embedder, _LOCAL_MODEL_DIM, _LOCAL_UNAVAILABLE
    if _LOCAL_UNAVAILABLE:
        raise RuntimeError("本地模型不可用（已缓存）")
    if _embedder is None:
        from config import EMBEDDING_DEVICE
        from sentence_transformers import SentenceTransformer
        path = _get_local_path()
        logger.info(f"加载本地模型: {path}")
        _embedder = SentenceTransformer(path, device=EMBEDDING_DEVICE)
        _LOCAL_MODEL_DIM = _embedder.get_embedding_dimension()
        logger.info(f"本地模型就绪，维度={_LOCAL_MODEL_DIM}")
    vecs = _embedder.encode(texts, normalize_embeddings=True, show_progress_bar=False)
    return [_pack(v) for v in vecs]


def _encode_remote(texts: list[str]) -> list[bytes]:
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
            if len(texts) > batch_size:
                logger.info(f"远程向量化进度: {min(i + batch_size, len(texts))}/{len(texts)}")
    return [_pack(v) for v in all_embeddings]


def encode(texts: list[str]) -> list[bytes]:
    """文本列表 → embedding bytes。

    混合策略：大 batch（> REMOTE_THRESHOLD）走远程 SiliconFlow，否则本地。
    """
    global _LOCAL_UNAVAILABLE
    n = len(texts)
    if n == 0:
        return []

    # 方案 C：超过阈值切远程（大文档快）
    if n > REMOTE_THRESHOLD:
        try:
            logger.info(f"文本数 {n} > 阈值 {REMOTE_THRESHOLD}，切换 SiliconFlow 远程向量化")
            return _encode_remote(texts)
        except Exception as e:
            logger.warning(f"远程向量化失败，降级本地: {e}")
            try:
                return _encode_local(texts)
            except Exception:
                pass
            raise

    # 本地路径存在则尝试本地，失败降级远程（本地权重文件可能缺失）
    p = _get_local_path()
    if p and not _LOCAL_UNAVAILABLE:
        try:
            return _encode_local(texts)
        except Exception as e:
            logger.warning(f"本地模型加载失败，降级远程 SiliconFlow: {e}")
            _LOCAL_UNAVAILABLE = True  # 缓存失败，后续直接远程
    try:
        return _encode_remote(texts)
    except Exception as e:
        logger.error(f"Embedding 失败（本地+远程均不可用）: {e}")
        raise


def encode_query(text: str) -> bytes:
    return encode([text])[0]


def get_embedding_dim() -> int:
    """返回当前 embedding 维度（远程 large=1024，本地 small=512 / large=1024）"""
    global _LOCAL_MODEL_DIM
    if _LOCAL_MODEL_DIM is not None:
        return _LOCAL_MODEL_DIM
    # 本地模型未加载时的预设
    from config import EMBEDDING_MODEL
    return 1024 if "large" in EMBEDDING_MODEL else 512


def _pack(vec: np.ndarray) -> bytes:
    return struct.pack(f"{len(vec)}f", *vec)


def _unpack(data: bytes) -> np.ndarray:
    return np.array(struct.unpack(f"{len(data)//4}f", data), dtype=np.float32)


def cosine_similarity(vec_a: bytes, vec_b: bytes) -> float:
    a = _unpack(vec_a)
    b = _unpack(vec_b)
    return float(np.dot(a, b))
