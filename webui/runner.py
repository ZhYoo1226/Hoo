import os,subprocess
from common import g_yaml_config

# Open WebUI 子进程
_webui_process = None
# 项目根目录
BASE_PATH = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

def start_webui(port=3000):
    """将 Open WebUI 作为子进程启动，与 OpenSolver 同生命周期"""
    global _webui_process
    env = os.environ.copy()
    env["OPENAI_API_BASE_URL"] = f"http://localhost:{g_yaml_config.get('gateway', {}).get('port', 8081)}/v1"
    env["OPENAI_API_KEY"] = "opensolver"
    env["RAG_EMBEDDING_ENGINE"] = "openai"
    # 清除镜像配置，WebUI 子进程不需要也不兼容 hf-mirror 的 SSL
    env.pop("HF_ENDPOINT", None)
    # Popen是异步的run()
    _webui_process = subprocess.Popen(
        [os.path.join(BASE_PATH, ".venv312", "Scripts", "open-webui.exe"), "serve", "--port", str(port)],
        env=env
    )
    print(f"[WebUI] Open WebUI 已启动: http://localhost:{port}")


def stop_webui():
    global _webui_process
    if _webui_process is not None:
        print("[WebUI] 正在停止 Open WebUI...")
        _webui_process.terminate()
        try:
            _webui_process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            _webui_process.kill()
        _webui_process = None
