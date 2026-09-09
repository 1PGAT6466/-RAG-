"""
统一错误响应格式
===============
伏羲后端原有 56 个 HTTPException，detail 全是裸字符串，前端无法区分「用户可操作」
vs「系统错误」，也不知道是否可重试。本模块建立统一错误格式：

{
    "status": "error",
    "error": {
        "code": "FILE_NOT_FOUND",
        "message": "文件不存在",
        "retryable": false
    }
}

用法：
    from src.api.errors import BizError, ErrorCode
    raise BizError(ErrorCode.FILE_NOT_FOUND)
    raise BizError(ErrorCode.SERVICE_BUSY, "服务繁忙，正在处理大文件", retryable=True)
"""
from fastapi import HTTPException
from fastapi.responses import JSONResponse


class ErrorCode:
    """业务错误码常量（分类：4xx 用户错误 / 5xx 系统错误）"""
    # 4xx — 用户可操作
    FILE_NOT_FOUND = "FILE_NOT_FOUND"
    TASK_NOT_FOUND = "TASK_NOT_FOUND"
    CONVERSATION_NOT_FOUND = "CONVERSATION_NOT_FOUND"
    ENTITY_NOT_FOUND = "ENTITY_NOT_FOUND"
    MCP_NOT_INSTALLED = "MCP_NOT_INSTALLED"
    INVALID_INPUT = "INVALID_INPUT"
    UPLOAD_TOO_LARGE = "UPLOAD_TOO_LARGE"
    EMPTY_FILE = "EMPTY_FILE"
    UNSUPPORTED_FORMAT = "UNSUPPORTED_FORMAT"
    LOGIN_FAILED = "LOGIN_FAILED"
    PERMISSION_DENIED = "PERMISSION_DENIED"
    REGISTER_DISABLED = "REGISTER_DISABLED"
    RATE_LIMITED = "RATE_LIMITED"
    # 5xx — 系统错误（可重试）
    SERVICE_BUSY = "SERVICE_BUSY"
    DMS_WRITE_FAILED = "DMS_WRITE_FAILED"
    DMS_CONNECT_FAILED = "DMS_CONNECT_FAILED"
    LLM_FAILED = "LLM_FAILED"
    EMBED_FAILED = "EMBED_FAILED"
    PARSE_FAILED = "PARSE_FAILED"
    MCP_CALL_FAILED = "MCP_CALL_FAILED"
    MCP_TIMEOUT = "MCP_TIMEOUT"
    INTERNAL_ERROR = "INTERNAL_ERROR"


# 错误码 → 默认 HTTP 状态码 + 默认消息
_ERROR_MAP: dict[str, tuple[int, str, bool]] = {
    ErrorCode.FILE_NOT_FOUND:        (404, "文件不存在", False),
    ErrorCode.TASK_NOT_FOUND:        (404, "任务不存在或已完成", False),
    ErrorCode.CONVERSATION_NOT_FOUND:(404, "会话不存在", False),
    ErrorCode.ENTITY_NOT_FOUND:      (404, "实体不存在", False),
    ErrorCode.MCP_NOT_INSTALLED:     (404, "MCP server 未安装", False),
    ErrorCode.INVALID_INPUT:         (422, "输入参数无效", False),
    ErrorCode.UPLOAD_TOO_LARGE:      (413, "文件过大", False),
    ErrorCode.EMPTY_FILE:            (400, "文件为空", False),
    ErrorCode.UNSUPPORTED_FORMAT:    (400, "不支持的文件格式", False),
    ErrorCode.LOGIN_FAILED:          (401, "用户名或密码错误", False),
    ErrorCode.PERMISSION_DENIED:     (403, "无权执行此操作", False),
    ErrorCode.REGISTER_DISABLED:     (403, "注册已关闭", False),
    ErrorCode.RATE_LIMITED:          (429, "操作过于频繁，请稍后重试", True),
    ErrorCode.SERVICE_BUSY:          (503, "服务繁忙，请稍后重试", True),
    ErrorCode.DMS_WRITE_FAILED:      (502, "写入 SeedDMS 失败", True),
    ErrorCode.DMS_CONNECT_FAILED:    (502, "SeedDMS 连接失败", True),
    ErrorCode.LLM_FAILED:            (503, "AI 服务暂时不可用，请稍后重试", True),
    ErrorCode.EMBED_FAILED:          (503, "向量化服务暂时不可用", True),
    ErrorCode.PARSE_FAILED:          (422, "文档解析失败，请检查文件格式", False),
    ErrorCode.MCP_CALL_FAILED:       (502, "MCP 工具调用失败", True),
    ErrorCode.MCP_TIMEOUT:           (504, "MCP 工具调用超时", True),
    ErrorCode.INTERNAL_ERROR:        (500, "内部错误，请稍后重试", True),
}


class BizError(HTTPException):
    """业务异常：统一格式 + 错误码 + 可重试标记"""

    def __init__(self, code: str, message: str = None, retryable: bool = None, status_code: int = None):
        default_status, default_msg, default_retry = _ERROR_MAP.get(
            code, (500, "内部错误", True)
        )
        self.code = code
        self.biz_message = message or default_msg
        self.retryable = default_retry if retryable is None else retryable
        super().__init__(
            status_code=status_code or default_status,
            detail={
                "status": "error",
                "error": {
                    "code": self.code,
                    "message": self.biz_message,
                    "retryable": self.retryable,
                }
            }
        )


def error_response(code: str, message: str = None, retryable: bool = None) -> JSONResponse:
    """直接返回 JSONResponse（用于全局异常处理器等非 raise 场景）"""
    default_status, default_msg, default_retry = _ERROR_MAP.get(code, (500, "内部错误", True))
    return JSONResponse(
        status_code=_ERROR_MAP.get(code, (500,))[0],
        content={
            "status": "error",
            "error": {
                "code": code,
                "message": message or default_msg,
                "retryable": default_retry if retryable is None else retryable,
            }
        }
    )
