"""Fixed public protocol guidance; no catalog, session or configuration access."""

from app.api.openai_compat import MODEL_ID

GUIDE_PATH = "/api-guide"


def public_api_guide() -> dict:
    """Return a fresh public document without consulting application state."""
    return {
        "endpoint": {"method": "POST", "path": "/v1/chat/completions"},
        "headers": {
            "Content-Type": "application/json",
            "Authorization": "Bearer <本项目独立访问令牌>",
            "authentication": "云网关校验令牌；不要直接公开后端端口。",
        },
        "required": {
            "body": ["model", "messages"],
            "message": ["role", "content"],
            "model": MODEL_ID,
            "messages": "恰好一条新增 role=user 纯文本，去空白后1–2000字符。",
            "identity": "user、context.user_id、X-User-ID 至少提供一个；重复位置必须一致。",
        },
        "identity": {
            "user": "已获授权且目标目录中存在的正整数ID字符串，最长20字符，不接受JSON数字。",
            "context.user_id": "JSON正整数；不接受字符串、浮点或布尔。",
            "X-User-ID": "ASCII正整数请求头，允许前导零，按整数比较。",
            "scope": "已交正式档案合同使用ID 1–50；不自动选人，不提供匿名身份。",
        },
        "optional": {
            "stream": {"default": False, "values": [False, True], "null_allowed": False},
            "stream_options": "可省略/null；非null仅用于stream=true，允许空对象或include_usage=false。",
            "context": "可省略/null，仅含user_id、session_id、client_turn_id。",
            "session_id": "首轮省略；续轮复用响应X-Session-ID。顶层/context/头必须一致。",
            "request_id": "可省略/null；业务幂等键1–128字符，每个新操作使用新值。",
            "null_semantics": "身份位置为null视为未提供；其他身份位置仍必须有合法值。省略不等于允许任意类型或空值。",
        },
        "session": {
            "format": "32位小写十六进制，必须属于当前用户。",
            "sources": ["session_id", "context.session_id", "X-Session-ID"],
            "continuation": "只提交新消息，不重发历史；省略会话表示新请求，不能冒充续轮。",
        },
        "idempotency": {
            "sources": ["request_id", "context.client_turn_id", "X-Client-Request-Id"],
            "duplicates": "重复位置必须一致；不同消息或过期版本不能复用同一键。",
            "X-Request-ID": "响应链路追踪标识，不要求请求反向提供，不是业务幂等键。",
        },
        "examples": {
            "placeholder_notice": "尖括号项必须替换，示例不是可直接执行的真实身份或令牌。",
            "first": {
                "model": MODEL_ID,
                "user": "<已授权且目标目录中存在的用户ID>",
                "messages": [{"role": "user", "content": "合成示例：1人晚餐，没有其他忌口。"}],
            },
            "continuation": {
                "headers": {"X-Session-ID": "<首轮响应返回的会话ID>"},
                "body": {
                    "model": MODEL_ID,
                    "user": "<与首轮相同的用户ID>",
                    "messages": [{"role": "user", "content": "只换第二道菜，其他要求不变。"}],
                },
            },
        },
        "responses": {
            "non_streaming": "stream省略或false；读取choices[0].message.content。",
            "streaming": "stream=true；拼接choices[0].delta.content，全部内容后才有stop和一次[DONE]。",
            "recipe_json": "正文末尾【菜谱JSON】的fenced json数组，仅含最终recipe_id/name；澄清或不可行为[]。",
            "errors": (
                "兼容入口已处理的应用错误及网关401使用四字段JSON：error.message/type/param/code。"
                "其他错误不保证该格式；先检查HTTP状态码与Content-Type，再决定如何解析正文，"
                "不能将错误当作成功菜单或SSE。"
            ),
            "interruption": "取消或中断不补造正常stop、[DONE]或成功空数组。",
        },
        "unsupported": [
            "完整历史或多条messages", "system/assistant消息", "图片/音频",
            "tools/tool_calls", "temperature等生成参数", "stream_options.include_usage=true",
        ],
        "error_help": {
            "401": "使用本项目独立访问令牌填写Authorization: Bearer；不要使用模型密钥。",
            "403": "该请求被现有权限边界拒绝；不要更改身份绕过。",
            "404": "核对已授权用户ID；不存在的会话在首轮省略，续轮核对原返回值。",
            "409": "统一身份/会话/幂等来源；核对用户与状态版本，勿覆盖他人会话。",
            "422": "按error.param修正结构或删除不支持字段；不要默认选择用户。",
            "429": "入口限流或后端容量已满；有序处理请求。",
            "500": "最终菜单数据不完整；不是成功空菜单。",
            "502": "模型输出未通过验证；不是成功答案。",
            "503": "模型/检索暂不可用；保留当前已知约束。",
        },
        "guide": {"path": GUIDE_PATH, "methods": ["GET", "HEAD"], "creates_session": False},
    }


def validation_guidance(parameter: str | None, error_type: str) -> tuple[str | None, str]:
    """Explain known constraints without serializing validation inputs or context."""
    # The unchanged request model has exactly one root validator: stream options.
    if parameter is None and error_type == "value_error":
        parameter = "stream_options"
    if error_type == "extra_forbidden":
        hint = "删除不支持的字段；当前只接受说明中的单条user文本及列出的扩展。"
    elif parameter == "model":
        hint = f"提供 model，值必须为 {MODEL_ID}。"
    elif parameter == "messages" or (parameter and parameter.startswith("messages.")):
        hint = "提供恰好一条 messages，role 必须为 user，content 为去空白后1–2000字符的纯文本。"
    elif parameter == "user":
        hint = "user 使用已获授权且目录中存在的正整数ID字符串，最长20字符，不能传JSON数字。"
    elif parameter == "context.user_id":
        hint = "context.user_id 使用JSON正整数，不能传字符串、浮点或布尔。"
    elif parameter in {"session_id", "context.session_id"}:
        hint = "首轮可省略会话；续轮复用原响应的32位小写十六进制 X-Session-ID。"
    elif parameter in {"request_id", "context.client_turn_id"}:
        hint = "幂等键应为1–128字符的字符串，新操作用新值；该字段可以省略。"
    elif parameter == "stream":
        hint = "stream 可以省略，默认false；提供时只能为JSON布尔true/false，不能为null。"
    elif parameter == "stream_options" or (
        parameter and parameter.startswith("stream_options.")
    ):
        hint = "删除 stream_options 或使用stream=true；选项仅允许空对象或include_usage=false。"
    elif parameter == "context":
        hint = "context 可以省略/null，提供对象时只含user_id、session_id、client_turn_id。"
    else:
        hint = "使用Content-Type: application/json提交合法JSON对象，并按说明检查请求字段。"
    return parameter, f"请求字段不符合当前文本接口约定。{hint} 参见 {GUIDE_PATH}。"
