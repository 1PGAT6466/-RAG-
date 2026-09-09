"""
seeddms_client.py — SeedDMS REST API 封装

基于 SeedDMS 6.0.41 的 REST API（Slim 框架，入口 /restapi/index.php）。
认证：POST /login 拿 mydms_session cookie，后续请求自动携带。

接口能力（已实测）：
  login / folder 树 / document 下载 / versions / search
"""
import logging
import httpx

from config import SEEDDMS_URL, SEEDDMS_USER, SEEDDMS_PASS

logger = logging.getLogger("rag.dms.client")

_API_BASE = SEEDDMS_URL.rstrip("/") + "/restapi/index.php"


class SeedDMSClient:
    """SeedDMS REST API 客户端（进程级复用 httpx session + cookie）"""

    def __init__(self, base_url: str = None, user: str = None, password: str = None):
        self.base_url = (base_url or SEEDDMS_URL).rstrip("/")
        self.user = user or SEEDDMS_USER
        self.password = password or SEEDDMS_PASS
        self.api = self.base_url + "/restapi/index.php"
        self._client = httpx.Client(timeout=60.0)
        self._logged_in = False

    # ---- 连接 ----

    def ping(self) -> bool:
        """探活：尝试连接 SeedDMS（不登录），返回是否可达"""
        try:
            r = self._client.get(self.api + "/version")
            return r.status_code < 500
        except Exception as e:
            logger.warning(f"SeedDMS 探活失败: {e}")
            return False

    def login(self) -> bool:
        """登录，成功返回 True 并持有 session cookie"""
        if self._logged_in:
            return True
        try:
            r = self._client.post(
                self.api + "/login",
                data={"user": self.user, "pass": self.password},
            )
            if r.status_code == 200:
                data = r.json()
                if data.get("success"):
                    self._logged_in = True
                    logger.info(f"SeedDMS 登录成功: {self.user}")
                    return True
            logger.warning(f"SeedDMS 登录失败: HTTP {r.status_code}")
            return False
        except Exception as e:
            logger.error(f"SeedDMS 登录异常: {e}")
            return False

    # ---- 查询 ----

    def _get_json(self, path: str, params: dict = None) -> dict:
        r = self._client.get(self.api + path, params=params)
        r.raise_for_status()
        return r.json()

    def get_root_folders(self) -> dict:
        """获取根文件夹下的直接子项（SeedDMS 根目录 id = 1）"""
        return self._get_json("/folder/1/children")

    def get_folder_children(self, folder_id) -> dict:
        """获取指定文件夹的子文件夹 + 文档"""
        return self._get_json(f"/folder/{folder_id}/children")

    def get_folder_tree(self) -> dict:
        """递归拉取完整文件夹树。

        返回 {success, data: [{id, name, type:'folder', children:[...]}, ...]}
        type: folder / document
        """
        root = self.get_root_folders()
        if not root.get("success"):
            return root

        tree = []
        for item in root.get("data", []):
            node = self._build_tree_node(item)
            if node:
                tree.append(node)
        return {"success": True, "message": "", "data": tree}

    def _build_tree_node(self, item: dict, depth: int = 0) -> dict | None:
        """递归构建树节点，深度上限防止死循环"""
        if depth > 20:
            return None
        node_type = item.get("type", "folder")
        node = {
            "id": item.get("id"),
            "name": item.get("name", ""),
            "type": node_type,
        }
        if node_type == "folder":
            node["children"] = []
            try:
                children = self.get_folder_children(item["id"])
                for c in children.get("data", []):
                    child = self._build_tree_node(c, depth + 1)
                    if child:
                        node["children"].append(child)
            except Exception as e:
                logger.warning(f"拉取文件夹 {item['id']} 子项失败: {e}")
        else:
            # 文档：附带版本信息
            node["version"] = item.get("version", 0)
        return node

    # ---- 文档 ----

    def get_documents_flat(self) -> list[dict]:
        """递归拉取全部文档（扁平列表，不含文件夹层级），供分页查阅。

        返回 [{id, name, type:'document', version, folder_path}]
        folder_path 为该文档所在文件夹路径快照（如 /RAG测试文件）。
        """
        docs = []

        def _walk(folder_id: int, path_prefix: str):
            resp = self.get_folder_children(folder_id)
            if not resp.get("success"):
                return
            for item in resp.get("data", []):
                name = item.get("name", "")
                if item.get("type") == "folder":
                    sub = f"{path_prefix}/{name}" if path_prefix else f"/{name}"
                    _walk(item["id"], sub)
                else:
                    docs.append({
                        "id": item.get("id"),
                        "name": name,
                        "type": "document",
                        "version": item.get("version", 0),
                        "folder_path": path_prefix or "/",
                    })

        root = self.get_root_folders()
        if not root.get("success"):
            return docs
        for item in root.get("data", []):
            name = item.get("name", "")
            if item.get("type") == "folder":
                _walk(item["id"], f"/{name}")
            else:
                docs.append({
                    "id": item.get("id"),
                    "name": name,
                    "type": "document",
                    "version": item.get("version", 0),
                    "folder_path": "/",
                })
        return docs

    def get_document_meta(self, doc_id: int) -> dict:
        """获取文档元数据"""
        return self._get_json(f"/document/{doc_id}")

    def get_document_versions(self, doc_id: int) -> list[dict]:
        """获取文档版本列表"""
        r = self._get_json(f"/document/{doc_id}/versions")
        return r.get("data", []) if r.get("success") else []

    def download_document(self, doc_id: int) -> tuple[bytes, str, str]:
        """下载文档最新内容，返回 (content_bytes, filename, mime_type)"""
        r = self._client.get(self.api + f"/document/{doc_id}/content")
        r.raise_for_status()
        filename = ""
        cd = r.headers.get("Content-Disposition", "")
        if "filename=" in cd:
            import re
            m = re.search(r'filename="?([^";]+)"?', cd)
            if m:
                filename = m.group(1)
        mime = r.headers.get("Content-Type", "")
        return r.content, filename, mime

    def search(self, query: str, limit: int = 20) -> list[dict]:
        """搜索 SeedDMS 文档"""
        r = self._get_json("/search", {"query": query, "limit": limit})
        return r.get("data", []) if r.get("success") else []

    def close(self):
        try:
            self._client.close()
        except Exception:
            pass


# 全局客户端（进程级复用，避免每次请求重新登录）
_client: SeedDMSClient | None = None


def get_client() -> SeedDMSClient:
    """获取全局 SeedDMS 客户端（懒加载 + 登录缓存）"""
    global _client
    if _client is None:
        _client = SeedDMSClient()
    return _client


def reset_client():
    """重置客户端（配置变更后调用，重新登录）"""
    global _client
    if _client is not None:
        _client.close()
        _client = None
