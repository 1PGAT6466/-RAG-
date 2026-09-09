"""
MCP server 管理 — 持久化已安装的 MCP server 配置

存储：SQLite 表 mcp_servers（复用项目主库 rag.db）
字段：qualified_name(唯一) / display_name / command / args(json) / env(json) / installed_at
"""
import json
import logging
from datetime import datetime

from src.storage.db import _get_conn

logger = logging.getLogger("rag.mcp.manager")


def _ensure_table():
    conn = _get_conn()
    conn.execute("""
        CREATE TABLE IF NOT EXISTS mcp_servers (
            qualified_name TEXT PRIMARY KEY,
            display_name TEXT DEFAULT '',
            description TEXT DEFAULT '',
            command TEXT DEFAULT '',
            args TEXT DEFAULT '[]',
            env TEXT DEFAULT '{}',
            transport TEXT DEFAULT 'stdio',
            url TEXT DEFAULT '',
            headers TEXT DEFAULT '{}',
            installed_at TEXT DEFAULT ''
        )
    """)
    # 旧表迁移：补 transport/url/headers 列
    cols = [r[1] for r in conn.execute("PRAGMA table_info(mcp_servers)").fetchall()]
    if "transport" not in cols:
        conn.execute("ALTER TABLE mcp_servers ADD COLUMN transport TEXT DEFAULT 'stdio'")
    if "url" not in cols:
        conn.execute("ALTER TABLE mcp_servers ADD COLUMN url TEXT DEFAULT ''")
    if "headers" not in cols:
        conn.execute("ALTER TABLE mcp_servers ADD COLUMN headers TEXT DEFAULT '{}'")
    conn.commit()


def list_installed() -> list[dict]:
    """列出已安装的 MCP server"""
    _ensure_table()
    conn = _get_conn()
    rows = conn.execute(
        "SELECT qualified_name, display_name, description, command, args, env, transport, url, headers, installed_at "
        "FROM mcp_servers ORDER BY installed_at DESC"
    ).fetchall()
    result = []
    for r in rows:
        result.append({
            "qualifiedName": r["qualified_name"],
            "displayName": r["display_name"],
            "description": r["description"],
            "command": r["command"],
            "args": json.loads(r["args"] or "[]"),
            "env": json.loads(r["env"] or "{}"),
            "transport": r["transport"] or "stdio",
            "url": r["url"] or "",
            "headers": json.loads(r["headers"] or "{}"),
            "installedAt": r["installed_at"],
        })
    return result


def install_server(qualified_name: str, display_name: str, description: str,
                   command: str, args: list[str], env: dict,
                   transport: str = "stdio", url: str = "", headers: dict = None) -> bool:
    """安装（或更新）一个 MCP server。

    transport='stdio' 本地子进程（command=可执行文件）；
    transport='http' 远程 Streamable HTTP（url=端点）。
    """
    _ensure_table()
    conn = _get_conn()
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    conn.execute("""
        INSERT INTO mcp_servers (qualified_name, display_name, description, command, args, env, transport, url, headers, installed_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(qualified_name) DO UPDATE SET
            display_name=excluded.display_name,
            description=excluded.description,
            command=excluded.command,
            args=excluded.args,
            env=excluded.env,
            transport=excluded.transport,
            url=excluded.url,
            headers=excluded.headers,
            installed_at=excluded.installed_at
    """, (qualified_name, display_name, description, command,
          json.dumps(args), json.dumps(env), transport, url, json.dumps(headers or {}), now))
    conn.commit()
    logger.info(f"MCP server 已安装: {qualified_name} (transport={transport})")
    return True


def uninstall_server(qualified_name: str) -> bool:
    """卸载 MCP server"""
    _ensure_table()
    conn = _get_conn()
    cur = conn.execute("DELETE FROM mcp_servers WHERE qualified_name=?", (qualified_name,))
    conn.commit()
    return cur.rowcount > 0


def get_server(qualified_name: str) -> dict | None:
    """查单个已安装 server"""
    for s in list_installed():
        if s["qualifiedName"] == qualified_name:
            return s
    return None
