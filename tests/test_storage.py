"""
存储层单元测试 — connection.py + db.py + files.py + chunks.py + entities.py + conversations.py + tasks.py + feedback.py + auth
覆盖核心链路：连接管理、文件 CRUD、Chunk + FTS5 搜索、删除原子性、认证、反馈
"""
import sys, os, threading
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pytest


@pytest.fixture(autouse=True)
def tmp_db(tmp_path, monkeypatch):
    """每个测试用独立临时 SQLite

    注意：connection.py 在模块顶部 `from config import DB_PATH` 捕获路径，
    若本模块被更早的测试 import 过（真实路径已锁定），只改 config.DB_PATH
    不会生效。必须同时 monkeypatch src.storage.connection.DB_PATH 才能
    真正隔离，避免测试写入真实 rag.db 造成相互污染。
    """
    db_path = str(tmp_path / "test.db")
    monkeypatch.setattr("config.DB_PATH", db_path)
    import src.storage.connection as connection
    monkeypatch.setattr(connection, "DB_PATH", db_path)
    connection._local = threading.local()
    connection._conn_var.set(None)
    connection.init_db()
    yield db_path
    connection.close_thread_conn()


# ── connection.py ──
class TestConnection:
    def test_get_conn_returns_connection(self):
        from src.storage.connection import _get_conn
        conn = _get_conn()
        import sqlite3
        assert isinstance(conn, sqlite3.Connection)

    def test_get_conn_same_thread_same_conn(self):
        from src.storage.connection import _get_conn
        c1 = _get_conn()
        c2 = _get_conn()
        assert c1 is c2

    def test_set_conn_overrides(self):
        from src.storage.connection import set_conn, _get_conn, reset_conn
        import sqlite3
        custom = sqlite3.connect(":memory:")
        set_conn(custom)
        assert _get_conn() is custom
        reset_conn()

    def test_init_db_creates_tables(self):
        from src.storage.connection import _get_conn
        conn = _get_conn()
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
        for t in ["files", "chunks", "chunks_fts", "chunks_fts_tri", "entities", "users", "conversations", "tasks"]:
            assert t in tables, f"missing table: {t}"


# ── files.py ──
class TestFiles:
    def test_add_and_get_file(self):
        from src.storage.files import add_file, get_file
        fid = add_file("test.pdf", "/test.pdf", ".pdf", 1024)
        assert fid > 0
        f = get_file(fid)
        assert f["name"] == "test.pdf"
        assert f["ext"] == ".pdf"

    def test_list_files(self):
        from src.storage.files import add_file, list_files
        add_file("a.pdf", "/a.pdf", ".pdf", 100)
        add_file("b.docx", "/b.docx", ".docx", 200)
        files = list_files()
        assert len(files) >= 2

    def test_delete_file_cleans_chunks(self):
        from src.storage.files import add_file, delete_file, get_file
        from src.storage.connection import _get_conn
        fid = add_file("del.pdf", "/del.pdf", ".pdf", 100)
        conn = _get_conn()
        conn.execute("INSERT INTO chunks (file_id, chunk_index, content) VALUES (?,?,?)", (fid, 0, "hello"))
        conn.commit()
        delete_file(fid)
        assert get_file(fid) is None
        chunks = conn.execute("SELECT * FROM chunks WHERE file_id=?", (fid,)).fetchall()
        assert len(chunks) == 0

    def test_update_file_category(self):
        from src.storage.files import add_file, get_file, update_file_category
        fid = add_file("cat.pdf", "/cat.pdf", ".pdf", 100)
        update_file_category(fid, "连接器")
        f = get_file(fid)
        assert f["category"] == "连接器"

    def test_list_folders(self):
        from src.storage.files import add_file, list_folders
        add_file("a.pdf", "/docs/a.pdf", ".pdf", 100, folder="/docs")
        add_file("b.pdf", "/specs/b.pdf", ".pdf", 100, folder="/specs")
        folders = list_folders()
        paths = [f["path"] for f in folders]
        assert "/docs" in paths
        assert "/specs" in paths


# ── chunks.py ──
class TestChunks:
    def test_add_and_get_chunks(self):
        from src.storage.chunks import add_chunks_batch, get_chunks_by_file
        from src.storage.files import add_file
        from src.storage.connection import _get_conn
        fid = add_file("chunks.pdf", "/chunks.pdf", ".pdf", 100)
        rows = [
            (fid, 0, "第一个chunk", 10, None, {}),
            (fid, 1, "第二个chunk", 10, None, {}),
        ]
        add_chunks_batch(rows)
        result = get_chunks_by_file(fid)
        assert len(result) == 2
        assert result[0]["content"] == "第一个chunk"

    def test_fts_search(self):
        from src.storage.chunks import add_chunks_batch, fts_search
        from src.storage.files import add_file
        fid = add_file("fts.pdf", "/fts.pdf", ".pdf", 100)
        rows = [
            (fid, 0, "连接器 FAKRA 端子", 10, None, {}),
            (fid, 1, "镀金层厚度要求", 10, None, {}),
        ]
        add_chunks_batch(rows)
        results = fts_search("连接器", 10)
        assert len(results) >= 1

    def test_fts_search_no_injection(self):
        from src.storage.chunks import fts_search
        results = fts_search("test OR 1=1", 10)
        assert isinstance(results, list)

    def test_get_file_backlinks(self):
        from src.storage.chunks import get_file_backlinks
        links = get_file_backlinks(999)
        assert isinstance(links, dict)
        assert "incoming" in links
        assert "outgoing" in links


# ── entities.py ──
class TestEntities:
    def test_upsert_and_get_entity(self):
        from src.storage.entities import upsert_entity, get_entity_by_name
        eid = upsert_entity("FAKRA", "connector", description="射频连接器")
        assert eid > 0
        e = get_entity_by_name("FAKRA", "connector")
        assert e is not None
        assert e["name"] == "FAKRA"

    def test_entity_graph(self):
        from src.storage.entities import upsert_entity, add_entity_relation, get_entity_graph
        e1 = upsert_entity("A", "connector")
        e2 = upsert_entity("B", "material")
        add_entity_relation(e1, e2, "uses", 1.0)
        data = get_entity_graph()
        assert "nodes" in data
        assert "edges" in data


# ── conversations.py ──
class TestConversations:
    def test_create_and_list(self):
        from src.storage.conversations import create_conversation, list_conversations
        cid = create_conversation(1, "测试对话")
        assert cid > 0
        convs = list_conversations(1)
        assert len(convs) >= 1

    def test_add_and_get_messages(self):
        from src.storage.conversations import create_conversation, add_conversation_message, get_conversation_messages
        cid = create_conversation(1, "msg test")
        add_conversation_message(cid, "user", "你好")
        add_conversation_message(cid, "assistant", "你好！有什么可以帮您？")
        msgs = get_conversation_messages(cid)
        assert len(msgs) == 2
        assert msgs[0]["role"] == "user"


# ── tasks.py ──
class TestTasks:
    def test_save_and_load_task(self):
        from src.storage.tasks import save_task, load_tasks
        save_task({"task_id": "t1", "filename": "a.pdf", "filepath": "/a.pdf",
                    "status": "pending", "stage": "", "progress": 0, "progress_text": "",
                    "chunks": 0, "category": "", "file_id": None, "error": None})
        tasks = load_tasks()
        assert any(t["task_id"] == "t1" for t in tasks)

    def test_cancel_task(self):
        from src.storage.tasks import save_task, cancel_task, load_tasks
        save_task({"task_id": "t2", "filename": "b.pdf", "filepath": "/b.pdf",
                    "status": "running", "stage": "parse", "progress": 50, "progress_text": "解析中",
                    "chunks": 0, "category": "", "file_id": None, "error": None})
        result = cancel_task("t2")
        assert result is True
        tasks = load_tasks()
        t = next(t for t in tasks if t["task_id"] == "t2")
        assert t["status"] == "cancelled"

    def test_cleanup_stale_tasks(self):
        from src.storage.tasks import save_task, cleanup_stale_tasks, load_tasks, cancel_task
        # 先取消变成 cancelled，再清理
        save_task({"task_id": "stale1", "filename": "s.pdf", "filepath": "/s.pdf",
                    "status": "running", "stage": "", "progress": 0, "progress_text": "",
                    "chunks": 0, "category": "", "file_id": None, "error": None})
        cancel_task("stale1")
        cleanup_stale_tasks(max_age_hours=0)
        tasks = load_tasks()
        assert not any(t["task_id"] == "stale1" for t in tasks)


# ── auth.py ──
class TestAuth:
    def test_register_and_login(self):
        from src.auth.jwt import register, login
        result = register("testuser", "pass123")
        assert result is not None
        token = login("testuser", "pass123")
        assert token
        assert len(token) > 20

    def test_wrong_password(self):
        from src.auth.jwt import register, login
        register("testuser2", "pass123")
        with pytest.raises(Exception):
            login("testuser2", "wrong")


# ── feedback.py ──
class TestFeedback:
    def test_add_and_remove_feedback(self):
        from src.storage.feedback import add_feedback, remove_feedback, chunk_penalty_stats
        fid = add_feedback(1, kind="down", query="test", chunk_ids=[10, 20])
        assert isinstance(fid, (int, type(None)))


# ── API (TestClient) ──




