"""端到端闭环（真实组件版）（#15 增强，2026-09-21）

与 tests/test_e2e_flow.py 的区别：
  - test_e2e_flow.py  ：**离线打桩**，embedding / LLM 全部 mock，只验证链路装配与鉴权契约，永远可跑。
  - test_e2e_real_flow.py（本文件）：**真实闭环**，不 mock 检索链，跑
        「写入 → 分块 → 真实 BGE 向量化 → SQLite 存储 → 真实混合检索 → 召回命中」
    验证的是「向量化 → 索引 → 召回」这条最容易静默出错的链路确实能命中。

LLM 生成仍打桩（只验证「检索到 → 组装 prompt → 调用 LLM」的装配，不消耗额度）。

运行：
  python -m pytest tests/test_e2e_real_flow.py -q            # 默认：有本地模型则跑，无则 skip
  RAG_E2E_REAL=force python -m pytest tests/test_e2e_real_flow.py -q   # 强制跑（无模型则 fail）
  RAG_E2E_REAL=0     python -m pytest tests/test_e2e_real_flow.py -q   # 强制跳过

隔离：临时 DB + 临时 model cache 探测，不触碰真实 data/rag.db。
"""
import io
import math
import os
import struct
import sys
import tempfile
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

# ⚠️ 全局串行锁：本文件会启动真实入库线程（真实 BGE + SQLite 写）。
#    若与其它测试并发跑在同一 uvicorn 线程池/共享 connection 状态上，
#    会出现 event loop / 连接串扰导致随机失败。锁内串行保证可重复。
_REAL_E2E_LOCK = threading.Lock()


# ---------- 可用性探测 ----------

def _local_model_available() -> bool:
    """探测本地 BGE 模型是否可用（有权重文件）。"""
    try:
        from src.pipeline import embedder
        return bool(embedder._get_local_path())
    except Exception:
        return False


_MODE = os.environ.get("RAG_E2E_REAL", "auto").lower()
_HAS_MODEL = _local_model_available()

if _MODE == "0":
    pytest.skip("RAG_E2E_REAL=0：显式跳过真实闭环", allow_module_level=True)
if _MODE == "auto" and not _HAS_MODEL:
    pytest.skip("无本地 embedding 模型权重，跳过真实闭环（设 RAG_E2E_REAL=force 强制）",
                allow_module_level=True)
if _MODE == "force" and not _HAS_MODEL:
    pytest.fail("RAG_E2E_REAL=force 但本地无 embedding 模型权重")


# ---------- 唯一性语料（避免与真实库/其它测试串味） ----------

_UNIQUE = "XQZ" + os.urandom(3).hex().upper()
DOC_TEXT = f"""
伏羲端到端闭环验证文档（批次 {_UNIQUE}）

第一段（唯一标识）：本系统的向量检索验证码为 {_UNIQUE}-ALPHA。
该验证码用于确认「真实 BGE 向量化 → 存储 → 向量召回」链路可命中。

第二段（语义提示）：连接器镀金层厚度要求不低于 0.8 微米，
镀层均匀性偏差需控制在 ±10% 以内，以保证高频信号传输的阻抗稳定性。

第三段（无关噪声）：员工食堂本周菜单包含红烧肉、清蒸鱼与麻婆豆腐，
午餐供应时间为每日十一时三十分至十三时。
""".strip()


@pytest.fixture(autouse=True)
def _serial_lock():
    """串行执行本文件的真实入库用例，避免与其它测试并发串扰。"""
    with _REAL_E2E_LOCK:
        yield


@pytest.fixture
def client(monkeypatch, tmp_path):
    """真实检索链路的 TestClient（隔离 DB + 复用本地 BGE 权重）。"""
    db = tmp_path / "e2e_real.db"

    import src.storage.connection as conn_mod
    monkeypatch.setattr(conn_mod, "DB_PATH", str(db), raising=False)
    monkeypatch.setattr(conn_mod, "_local", __import__("threading").local(), raising=False)

    # ⚠️ 关键：connection.py 顶层 `from config import DB_PATH` 把路径快照进了模块变量，
    #    所有连接（含后台入库线程）都读 conn_mod.DB_PATH。只 monkeypatch config.DB_PATH 无效，
    #    必须直接改 connection.DB_PATH，否则入库线程会写入真实 data/rag.db。
    #    另外入库在 daemon 线程中执行，threading.local 会为该线程新建连接，同样走 conn_mod.DB_PATH，✅ 一致。
    try:
        import config
        monkeypatch.setattr(config, "DB_PATH", str(db), raising=False)
    except Exception:
        pass

    from src.storage.connection import init_db
    init_db()

    # 真实向量化：不 mock embedder。用 SQLite 兜底检索路径（避免依赖 Chroma 持久目录）
    monkeypatch.setenv("RAG_CHROMA", "0")

    # 打桩 LLM（真实 API 消耗额度，且与「检索是否命中」无关）
    import src.chat.engine as eng
    captured = {}

    async def _fake_llm(messages, max_tokens=1024):
        captured["messages"] = messages
        return "（打桩回答）已基于参考资料回答。"

    monkeypatch.setattr(eng, "_call_llm_with_fallback", _fake_llm, raising=False)

    from server import app
    from fastapi.testclient import TestClient
    with TestClient(app) as c:
        c._captured_llm = captured
        yield c


def _vec_similarity(a: bytes, b: bytes) -> float:
    """解包两段 float32 向量算余弦相似度（独立于项目实现，用于断言自证）。"""
    n = len(a) // 4
    va = struct.unpack(f"<{n}f", a)
    vb = struct.unpack(f"<{n}f", b)
    dot = sum(x * y for x, y in zip(va, vb))
    na = math.sqrt(sum(x * x for x in va))
    nb = math.sqrt(sum(x * x for x in vb))
    return dot / (na * nb + 1e-9)


# ---------- 测试 ----------

def test_real_embedding_roundtrip_and_similarity():
    """真实 BGE：encode 产出定长向量，语义相近文本相似度显著高于无关文本。"""
    from src.pipeline.embedder import encode, cosine_similarity

    vecs = encode([
        "连接器镀金层厚度要求不低于0.8微米",
        "镀金层厚度需大于等于0.8微米，保证阻抗稳定",
        "员工食堂今天供应红烧肉和清蒸鱼",
    ])
    assert len(vecs) == 3
    dim = len(vecs[0]) // 4
    assert dim >= 384, f"向量维度异常：{dim}"
    assert all(len(v) == len(vecs[0]) for v in vecs), "向量长度不一致"

    sim_rel = _vec_similarity(vecs[0], vecs[1])   # 语义相近
    sim_irr = _vec_similarity(vecs[0], vecs[2])   # 无关
    assert sim_rel > sim_irr + 0.1, f"语义区分度不足：相关={sim_rel:.3f} 无关={sim_irr:.3f}"

    # 项目自带 cosine_similarity 与独立实现一致
    assert abs(cosine_similarity(vecs[0], vecs[1]) - sim_rel) < 1e-3


def test_real_ingest_index_retrieve(client, monkeypatch):
    """真实闭环：写入 → 分块 → 真实向量化 → 存储 → 检索命中唯一标识。"""
    import asyncio
    # 1) 直连引擎入库（绕过 HTTP 上传对 DMS 的依赖，聚焦向量链路）
    #    注意：_stage_store 会自己 add_file 生成 file_id，切勿提前手动 add_file
    #    （会造成 files 表一条孤儿记录，且测试拿到的 id ≠ chunk 实际归属 id）。
    from src.pipeline import engine

    tmp = tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False, encoding="utf-8")
    tmp.write(DOC_TEXT)
    tmp.close()
    try:
        task_id = engine.enqueue(tmp.name, filename=f"e2e_real_{_UNIQUE}.txt")
        assert task_id

        # 2) 同步等待入库完成（真实向量化，给足时间）
        import time
        status = None
        for _ in range(120):  # 最多 ~120s
            status = engine.get_status(task_id)
            if status and status.get("status") in ("done", "success", "completed", "failed", "error"):
                break
            time.sleep(1)
        assert status, "无法获取任务状态"
        assert status.get("status") in ("done", "success", "completed"), \
            f"入库未成功：{status}"
        # 用任务实际生成的 file_id（而非测试自建）
        fid = status.get("file_id")
        assert fid, f"任务未返回 file_id：{status}"

        # 3) 校验 chunk 真的落库且带真实向量
        from src.storage.db import _get_conn
        conn = _get_conn()
        rows = conn.execute(
            "SELECT id, content, embedding FROM chunks WHERE file_id=? ORDER BY chunk_index", (fid,)
        ).fetchall()
        assert rows, "入库后无 chunk"
        assert any(r[2] for r in rows), "chunk 缺 embedding 向量（未真实向量化）"
        blob = next(r[2] for r in rows if r[2])
        assert len(blob) // 4 >= 384, "向量维度异常"

        # 4) 真实混合检索：用唯一标识召回
        from src.retrieval.search import search
        res = search(f"{_UNIQUE}-ALPHA 验证码", top_k=5)
        # search 可能是同步函数，也可能返回协程（视实现）
        if asyncio.iscoroutine(res):
            res = asyncio.run(res)
        hits = res if isinstance(res, list) else res.get("results", res)
        assert hits, "检索无任何召回（真实链路失败）"
        joined = " ".join(str(h.get("content", "")) for h in hits)
        assert _UNIQUE in joined, f"唯一标识 {_UNIQUE} 未被召回，检索链路未命中"

        # 5) 语义问题也能命中（验证不靠精确串匹配）
        res2 = search("连接器镀金层厚度要求", top_k=5)
        if asyncio.iscoroutine(res2):
            res2 = asyncio.run(res2)
        hits2 = res2 if isinstance(res2, list) else res2.get("results", res2)
        joined2 = " ".join(str(h.get("content", "")) for h in hits2)
        assert "镀金层" in joined2 or _UNIQUE in joined2, "语义检索未命中相关段落"
    finally:
        try:
            os.unlink(tmp.name)
        except OSError:
            pass


def test_real_retrieval_then_chat_assembly(client, monkeypatch):
    """检索命中后：对话编排能拿到参考资料并组装给 LLM（LLM 打桩）。"""
    import asyncio
    from src.pipeline import engine

    tmp = tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False, encoding="utf-8")
    tmp.write(DOC_TEXT)
    tmp.close()
    try:
        task_id = engine.enqueue(tmp.name, filename=f"e2e_chat_{_UNIQUE}.txt")

        import time
        for _ in range(120):
            st = engine.get_status(task_id)
            if st and st.get("status") in ("done", "success", "completed", "failed", "error"):
                break
            time.sleep(1)

        from src.retrieval.search import search
        res = search(f"{_UNIQUE}-ALPHA", top_k=5)
        if asyncio.iscoroutine(res):
            res = asyncio.run(res)
        hits = res if isinstance(res, list) else res.get("results", res)
        assert hits, "对话前置检索无命中"

        # 组装参考资料（真实 chunk → prompt 上下文）
        from src.chat.engine import generate
        answer, refs = asyncio.run(generate(f"{_UNIQUE}-ALPHA 是什么？", hits))
        assert answer and "打桩" in answer
        assert refs, "未生成引用映射"
        assert any("content" in r for r in refs)

        # LLM 收到的 messages 里确实带上了参考资料
        captured = client._captured_llm.get("messages")
        assert captured, "LLM 未收到消息"
        blob = " ".join(str(m.get("content", "")) for m in captured)
        assert _UNIQUE in blob, "参考资料未注入 LLM prompt"
    finally:
        try:
            os.unlink(tmp.name)
        except OSError:
            pass
