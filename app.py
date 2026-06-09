import os
import signal
import subprocess
import time

# 当前路径
# 当前工作目录和common模块的工作目录，不知道是什么，训练时会变化
BASE_URL = os.path.dirname(os.path.abspath(__file__)) + "/"
print(f"BASE_URL -> runtime pydata base path: {BASE_URL}")

os.environ["TIKTOKEN_CACHE_DIR"] = BASE_URL + "resources/models"
os.environ['FASTEMBED_CACHE_PATH'] = BASE_URL + "resources/models"
os.environ['HUGGINGFACE_HUB_CACHE'] = BASE_URL + "resources/models"
os.environ['HF_HOME'] = BASE_URL + "resources/models"
os.environ["HF_ENDPOINT"] = "https://hf-mirror.com"  # 国内镜像

print(f"os.environ['TIKTOKEN_CACHE_DIR'] -> {os.environ['TIKTOKEN_CACHE_DIR']}")
print(f"os.environ['FASTEMBED_CACHE_PATH'] -> {os.environ['FASTEMBED_CACHE_PATH']}")
print(f"os.environ['HF_HOME'] -> {os.environ['HF_HOME']}")
print(f"os.environ['HF_ENDPOINT'] -> {os.environ['HF_ENDPOINT']}")

from common import GlobalFunction
from state import g_yaml_config, g_owner, LiveState, StateOwner
from l2p.llm.openai import OPENAI
from gateway import start_gateway, stop_gateway

'''
统一管理大模型
'''
llm = OPENAI(
    provider="qwen",
    model=g_yaml_config["openai"]["model"],  # gpt-4o-mini
    api_key=os.getenv("API_KEY") or g_yaml_config["openai"]["api_key"],
    base_url=g_yaml_config["openai"]["base_url"],
    enable_thinking=g_yaml_config["openai"]["enable_thinking"]
)

# 设置意图理解的模型,静态变量
StateOwner.llm_intent_model = llm
StateOwner.llm_prompt_model = llm
StateOwner.llm_action_model = llm

# 用于优雅退出的标志
running = True

# Open WebUI 子进程
_webui_process = None


def start_webui(port=3000):
    """将 Open WebUI 作为子进程启动，与 OpenSolver 同生命周期"""
    global _webui_process
    env = os.environ.copy()
    env["OPENAI_API_BASE_URL"] = f"http://localhost:{g_yaml_config.get('gateway', {}).get('port', 8081)}/v1"
    env["OPENAI_API_KEY"] = "opensolver"
    env["RAG_EMBEDDING_ENGINE"] = "openai"
    # 清除镜像配置，WebUI 子进程不需要也不兼容 hf-mirror 的 SSL
    env.pop("HF_ENDPOINT", None)
    _webui_process = subprocess.Popen(
        [os.path.join(BASE_URL, ".venv312", "Scripts", "open-webui.exe"), "serve", "--port", str(port)],
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


def signal_handler(sig, frame):
    global running
    print("\n正在停止 FSM 线程...")
    stop_gateway()
    stop_webui()
    g_owner.destroy()
    # 呼吸3次，清除所有的state
    g_owner.breathe(1)  # 执行一次状态机更新
    running = False


def run_server():
    """在独立线程中运行 FSM 的呼吸循环"""
    gw_config = g_yaml_config.get("gateway", {})
    if gw_config.get("enabled", False):
        start_gateway(
            host=gw_config.get("host", "0.0.0.0"),
            port=gw_config.get("port", 8080)
        )
    wui_config = g_yaml_config.get("webui", {})
    if wui_config.get("enabled", False):
        start_webui(port=wui_config.get("port", 3000))
    while running:
        g_owner.breathe(1)  # 执行一次状态机更新
        time.sleep(0.1)  # 间隔 0.1 秒
    g_owner.breathe(1)  # 执行最后一次状态机更新
    print("run_server exit")


# 使用示例
if __name__ == "__main__":
    # 注册退出信号（Ctrl+C 时优雅停止）
    signal.signal(signal.SIGINT, signal_handler)
    g_owner.add_state(LiveState())

    # input_text = GlobalFunction.load_file("tests/任务_设计提示词.md")
    # input_text = GlobalFunction.load_file("tests/对话_静态信息_日期.md")
    # input_text = GlobalFunction.load_file("tests/对话_静态信息_自我.md")
    # input_text = GlobalFunction.load_file("tests/任务_银行回单_不清晰.md")
    # input_text = GlobalFunction.load_file("tests/文件_银行回单.md")
    # input_text = GlobalFunction.load_file("tests/项目经理工作任务.md")
    # g_owner.recv_message("用户", input_text)





    # 在独立线程中运行 LLM 测试
    # def test_llm():
    #     response = llm.query(prompt="你好")
    #     print(f"LLM 响应: {response}")
    #
    #
    # test_thread = threading.Thread(target=test_llm)
    # test_thread.start()

    # 创建并启动 FSM 线程（设置为守护线程，主线程退出时自动终止）
    # fsm_thread = threading.Thread(target=run_server, daemon=True)
    # fsm_thread.start()
    # 等待线程结束（可选）
    # fsm_thread.join(timeout=1)

    # 主线程保持运行，等待用户中断
    try:
        run_server()
    except KeyboardInterrupt:
        signal_handler(None, None)

    print("程序已退出。")
