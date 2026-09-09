"""
API 路由端到端测试（打运行中的服务，需要 smoke_test 前先启动 server）
覆盖：认证、文档、检索、对话、图谱、插件

注意：每个测试用唯一用户名，避免跨次运行残留。
"""
import sys, json, uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pytest
import urllib.request

BASE = "http://127.0.0.1:8099"


def _req(method, path, data=None, token=None):
    url = BASE + path
    body = json.dumps(data).encode() if data else None
    req = urllib.request.Request(url, data=body, method=method)
    req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", "Bearer " + token)
    try:
        r = urllib.request.urlopen(req, timeout=15)
        return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        raw = e.read()
        try:
            return e.code, json.loads(raw)
        except Exception:
            return e.code, raw.decode()


def _unwrap(resp):
    """从 {status,data,...} 嵌套中提取 data 字段"""
    if isinstance(resp, dict) and "data" in resp:
        return resp["data"]
    return resp


def _unique_name(prefix="test"):
    """生成唯一用户名，避免跨次运行残留"""
    return f"{prefix}_{uuid.uuid4().hex[:8]}"


def _login(username=None, password="pass123456"):
    if username is None:
        username = _unique_name("apitest")
    _req("POST", "/api/auth/register", {"username": username, "password": password})
    code, resp = _req("POST", "/api/auth/login", {"username": username, "password": password})
    if code == 200:
        data = resp.get("data", resp) if isinstance(resp, dict) else resp
        if isinstance(data, dict):
            return data.get("token", "")
    return ""


@pytest.fixture(scope="module")
def _server_ok():
    try:
        urllib.request.urlopen(BASE + "/api/health", timeout=5)
        return True
    except Exception:
        return False


@pytest.fixture(scope="module")
def token(_server_ok):
    if not _server_ok:
        pytest.skip("server not running")
    return _login()


@pytest.fixture(autouse=True)
def _need_server(_server_ok, token):
    """每个测试都依赖 server + token"""
    if not _server_ok:
        pytest.skip("server not running")
    if not token:
        pytest.skip("login failed")


class TestHealth:
    def test_health(self):
        code, data = _req("GET", "/api/health")
        assert code == 200
        assert data["status"] == "ok"


class TestAuth:
    def test_login(self):
        t = _login()
        assert t and len(t) > 20

    def test_wrong_password(self):
        name = _unique_name("wp")
        _req("POST", "/api/auth/register", {"username": name, "password": "correct123"})
        code, _ = _req("POST", "/api/auth/login", {"username": name, "password": "wrong123"})
        assert code in (401, 429)  # 429 = rate limit

    def test_protected_no_token(self):
        code, _ = _req("GET", "/api/documents")
        assert code == 401


class TestDocuments:
    def test_list(self, token):
        code, resp = _req("GET", "/api/documents", token=token)
        assert code == 200
        data = _unwrap(resp)
        assert isinstance(data, list)

    def test_not_found(self, token):
        code, _ = _req("GET", "/api/documents/99999", token=token)
        assert code == 404


class TestSearch:
    def test_valid(self, token):
        code, resp = _req("POST", "/api/search", {"query": "test", "top_k": 5}, token=token)
        assert code == 200
        data = _unwrap(resp)
        assert isinstance(data, list)


class TestConversations:
    def test_create_and_list(self, token):
        code, _ = _req("POST", "/api/conversations", {"title": "pytest"}, token=token)
        assert code == 200
        code2, data = _req("GET", "/api/conversations", token=token)
        assert code2 == 200


class TestGraph:
    def test_graph(self, token):
        code, resp = _req("GET", "/api/graph", token=token)
        assert code == 200
        data = _unwrap(resp)
        assert "nodes" in data


class TestPlugins:
    def test_list(self, token):
        code, resp = _req("GET", "/api/plugins", token=token)
        assert code == 200
