"""P2 插件与扩展测试：registry（注册/发现）+ hooks（钩子容错）+ rate_limit（限流）

覆盖纯逻辑、零网络、可离线稳定测试的部分。
"""
import pytest

from src.auth.rate_limit import RateLimiter
from src.plugins import registry


class TestRateLimiter:
    def test_allow_under_limit(self):
        rl = RateLimiter(max_requests=3, window_seconds=60)
        assert rl.allow("ip1") is True
        assert rl.allow("ip1") is True
        assert rl.allow("ip1") is True

    def test_deny_over_limit(self):
        rl = RateLimiter(max_requests=2, window_seconds=60)
        assert rl.allow("ip2") is True
        assert rl.allow("ip2") is True
        assert rl.allow("ip2") is False  # 超限

    def test_remaining(self):
        rl = RateLimiter(max_requests=3, window_seconds=60)
        assert rl.remaining("ip3") == 3
        rl.allow("ip3")
        assert rl.remaining("ip3") == 2

    def test_per_key_isolation(self):
        rl = RateLimiter(max_requests=1, window_seconds=60)
        assert rl.allow("a") is True
        assert rl.allow("b") is True  # 不同 key 互不影响


class TestPluginRegistrySchema:
    """插件注册表：manifest 字段校验 / 写入读取"""

    def test_register_and_get(self, tmp_path, monkeypatch):
        # 用独立临时 DB，避免污染真实 plugins.db
        monkeypatch.setattr(registry, "_DB_PATH", str(tmp_path / "plugins.db"))
        # 重置线程本地连接（指向新 DB）
        registry._local = type(registry._local)()
        registry.init_db()
        registry.register({
            "name": "test-plugin",
            "version": "1.0.0",
            "api_version": "1",
            "kind": "tool",
            "display_name": "测试插件",
            "description": "desc",
            "author": "tester",
        })
        row = registry.get("test-plugin")
        assert row is not None
        assert row["name"] == "test-plugin"
        assert row["api_version"] == "1"

    def test_get_missing_returns_none(self, tmp_path, monkeypatch):
        monkeypatch.setattr(registry, "_DB_PATH", str(tmp_path / "plugins.db"))
        registry._local = type(registry._local)()
        registry.init_db()
        assert registry.get("nonexistent") is None

    def test_list_all(self, tmp_path, monkeypatch):
        monkeypatch.setattr(registry, "_DB_PATH", str(tmp_path / "plugins.db"))
        registry._local = type(registry._local)()
        registry.init_db()
        registry.register({"name": "p1", "version": "1", "api_version": "1"})
        registry.register({"name": "p2", "version": "1", "api_version": "1"})
        assert len(registry.list_all()) == 2
