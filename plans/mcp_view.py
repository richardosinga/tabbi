import json

from django.http import HttpResponse, JsonResponse
from django.views.decorators.csrf import csrf_exempt

from tools.mcp_server import _handle


@csrf_exempt
def mcp_endpoint(request):
    if request.method != "POST":
        return JsonResponse({"error": "Method not allowed"}, status=405)
    try:
        message = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({
            "jsonrpc": "2.0", "id": None,
            "error": {"code": -32700, "message": "Parse error"},
        })
    response = _handle(message)
    if response is None:
        return HttpResponse(status=204)
    return JsonResponse(response)
