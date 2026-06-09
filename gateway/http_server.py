import asyncio
import threading

from aiohttp import web

from .openai_api import chat_completions, list_models, health_check

GATEWAY_HOST = "0.0.0.0"
GATEWAY_PORT = 8080


class HttpGateway:
    def __init__(self, host=GATEWAY_HOST, port=GATEWAY_PORT):
        self._host = host
        self._port = port
        self._runner = None
        self._thread = None
        self._loop = None

    def start(self):
        self._thread = threading.Thread(target=self._run_server, daemon=True)
        self._thread.start()

    def _run_server(self):
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)

        app = web.Application()
        app.router.add_post("/v1/chat/completions", chat_completions)
        app.router.add_get("/v1/models", list_models)
        app.router.add_get("/health", health_check)

        runner = web.AppRunner(app)
        self._loop.run_until_complete(runner.setup())
        site = web.TCPSite(runner, self._host, self._port)
        self._loop.run_until_complete(site.start())
        self._runner = runner

        print(f"[Gateway] HTTP 服务已启动: http://{self._host}:{self._port}")
        self._loop.run_forever()

    def stop(self):
        if self._runner and self._loop:
            print("[Gateway] 正在停止 HTTP 服务...")
            self._loop.call_soon_threadsafe(lambda: self._loop.stop())
            if self._thread and self._thread.is_alive():
                self._thread.join(timeout=3)


_gateway = None


def start_gateway(host=GATEWAY_HOST, port=GATEWAY_PORT):
    global _gateway
    if _gateway is not None:
        return
    _gateway = HttpGateway(host, port)
    _gateway.start()


def stop_gateway():
    global _gateway
    if _gateway is not None:
        _gateway.stop()
        _gateway = None
