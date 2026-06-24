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
        # 创建一个新的事件循环
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)

        app = web.Application() #存放路由的容器
        # 现在只有三个路由--/v1/chat/completion前端进入后端
        # /v1/models返回前端都有哪些模型
        # 健康检查
        app.router.add_post("/v1/chat/completions", chat_completions)
        app.router.add_get("/v1/models", list_models)
        app.router.add_get("/health", health_check)

        # aiohttp启动服务
        runner = web.AppRunner(app) #包装app-->运行器
        self._loop.run_until_complete(runner.setup()) #在事件循环里完成初始化（绑端口前准备工作）
        site = web.TCPSite(runner, self._host, self._port) #创建一个 TCP 监听器（绑定地址+端口）
        self._loop.run_until_complete(site.start()) #在事件循环里启动监听（开始接收请求）
        self._runner = runner #存下运行器，方便stop

        print(f"[Gateway] HTTP 服务已启动: http://{self._host}:{self._port}")
        self._loop.run_forever()

    def stop(self):
        if self._runner and self._loop:
            print("[Gateway] 正在停止 HTTP 服务...")
            self._loop.call_soon_threadsafe(lambda: self._loop.stop())
            if self._thread and self._thread.is_alive():
                self._thread.join(timeout=3)

# 写在文件里，模块级变量。任何函数都可以读它。
_gateway = None

# 只有赋值的时候才要声明，仅读取不用global

def start_gateway(host=GATEWAY_HOST, port=GATEWAY_PORT):
    # 如果不声明global-->默认认为新的、函数局部变量，外面的gateway没改
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
