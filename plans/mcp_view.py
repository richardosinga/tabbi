import json
import threading
import time
import uuid

from django.http import HttpResponse, JsonResponse, StreamingHttpResponse
from django.views.decorators.csrf import csrf_exempt

from tools.mcp_server import _handle

_sessions: dict = {}
_sessions_lock = threading.Lock()

_CORS_HEADERS = {
    "Access-Control-Allow-Origin": "*",
    "Access-Control-Allow-Methods": "GET, POST, DELETE, OPTIONS",
    "Access-Control-Allow-Headers": "Content-Type, Mcp-Session-Id, Authorization",
    "Access-Control-Expose-Headers": "Mcp-Session-Id",
}


def _add_cors(response):
    for k, v in _CORS_HEADERS.items():
        response[k] = v
    return response



@csrf_exempt
def mcp_endpoint(request):
    if request.method == "OPTIONS":
        r = HttpResponse(status=204)
        return _add_cors(r)
    if request.method == "GET":
        return _add_cors(_mcp_get(request))
    if request.method == "POST":
        return _add_cors(_mcp_post(request))
    if request.method == "DELETE":
        return _add_cors(_mcp_delete(request))
    return _add_cors(HttpResponse(status=405))


def _mcp_get(request):
    if not _wants_sse(request):
        return HttpResponse(status=406)

    def heartbeat():
        for _ in range(3600):  # up to 1 hour
            yield ": ping\n\n"
            time.sleep(15)

    r = StreamingHttpResponse(heartbeat(), content_type="text/event-stream")
    r["Cache-Control"] = "no-cache"
    r["X-Accel-Buffering"] = "no"
    session_id = request.META.get("HTTP_MCP_SESSION_ID", "")
    if session_id:
        r["Mcp-Session-Id"] = session_id
    return r


def _mcp_post(request):
    try:
        message = json.loads(request.body)
    except json.JSONDecodeError:
        err = {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "Parse error"}}
        return _sse_response([err]) if _wants_sse(request) else JsonResponse(err)

    session_id = request.META.get("HTTP_MCP_SESSION_ID", "")

    messages = message if isinstance(message, list) else [message]
    is_init = any(m.get("method") == "initialize" for m in messages if isinstance(m, dict))

    if is_init and not session_id:
        session_id = str(uuid.uuid4())
        with _sessions_lock:
            _sessions[session_id] = {}

    responses = [r for m in messages if isinstance(m, dict) for r in [_handle(m)] if r is not None]

    if not responses:
        r = HttpResponse(status=202)
    else:
        data = responses[0] if len(responses) == 1 else responses
        r = JsonResponse(data, safe=False)

    if session_id:
        r["Mcp-Session-Id"] = session_id
    return r


def _mcp_delete(request):
    session_id = request.META.get("HTTP_MCP_SESSION_ID", "")
    if session_id:
        with _sessions_lock:
            _sessions.pop(session_id, None)
    return HttpResponse(status=200)
