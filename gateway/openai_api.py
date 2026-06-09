import json
import queue
import time
import uuid

from aiohttp import web

from common import g_yaml_config


async def health_check(request):
    return web.json_response({"status": "ok"})


async def list_models(request):
    model_name = g_yaml_config["openai"]["model"]
    return web.json_response({
        "object": "list",
        "data": [{"id": model_name, "object": "model"}]
    })


async def chat_completions(request):
    from state import g_owner

    try:
        body = await request.json()
    except Exception:
        return web.json_response({"error": "invalid JSON body"}, status=400)

    messages = body.get("messages", [])
    stream = body.get("stream", False)
    model = body.get("model", g_yaml_config["openai"]["model"])

    if not messages:
        return web.json_response({"error": "messages is required"}, status=400)

    user_text = _extract_user_text(messages)
    if not user_text:
        return web.json_response({"error": "no user message found"}, status=400)

    request_id = str(uuid.uuid4())
    response_queue = queue.Queue()
    g_owner._response_queues[request_id] = response_queue

    g_owner.recv_message("用户", user_text)

    if stream:
        return await _stream_response(request, request_id, response_queue, model)
    else:
        return await _sync_response(request_id, response_queue, model)


def _extract_user_text(messages):
    for msg in reversed(messages):
        if msg.get("role") == "user":
            content = msg.get("content", "")
            if isinstance(content, list):
                parts = []
                for part in content:
                    if isinstance(part, dict) and part.get("type") == "text":
                        parts.append(part.get("text", ""))
                return "".join(parts)
            return content
    return None


async def _sync_response(request_id, response_queue, model):
    from state import g_owner

    timeout = 120
    collected = []
    start = time.time()

    while time.time() - start < timeout:
        try:
            msg = response_queue.get(timeout=1)
            collected.append(msg)
            role = msg.get("role", "")
            if role in ("助手", "assistant"):
                break
        except queue.Empty:
            continue

    g_owner._response_queues.pop(request_id, None)

    text = "".join(m.get("content", "") for m in collected)
    if not text:
        text = "（未收到回复，请重试）"

    return web.json_response({
        "id": f"chatcmpl-{request_id[:8]}",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": model,
        "choices": [{
            "index": 0,
            "message": {"role": "assistant", "content": text},
            "finish_reason": "stop"
        }]
    })


async def _stream_response(request, request_id, response_queue, model):
    from state import g_owner

    resp = web.StreamResponse()
    resp.headers["Content-Type"] = "text/event-stream"
    resp.headers["Cache-Control"] = "no-cache"
    resp.headers["Connection"] = "keep-alive"
    await resp.prepare(request)

    timeout = 120
    start = time.time()
    finished = False

    try:
        while time.time() - start < timeout:
            try:
                msg = response_queue.get(timeout=0.5)
            except queue.Empty:
                if finished:
                    break
                continue

            role = msg.get("role", "")
            content = msg.get("content", "")

            chunk = {
                "id": f"chatcmpl-{request_id[:8]}",
                "object": "chat.completion.chunk",
                "created": int(time.time()),
                "model": model,
                "choices": [{
                    "index": 0,
                    "delta": {"content": content},
                    "finish_reason": None
                }]
            }
            await resp.write(f"data: {json.dumps(chunk, ensure_ascii=False)}\n\n".encode())

            if role in ("助手", "assistant"):
                finished = True

        chunk = {
            "id": f"chatcmpl-{request_id[:8]}",
            "object": "chat.completion.chunk",
            "created": int(time.time()),
            "model": model,
            "choices": [{
                "index": 0,
                "delta": {},
                "finish_reason": "stop"
            }]
        }
        await resp.write(f"data: {json.dumps(chunk, ensure_ascii=False)}\n\n".encode())
        await resp.write(b"data: [DONE]\n\n")
    except Exception:
        pass
    finally:
        g_owner._response_queues.pop(request_id, None)
        await resp.write_eof()

    return resp
