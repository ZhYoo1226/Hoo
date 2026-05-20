import signal

from l2p.llm.openai import OPENAI
from state import *

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

# 设置意图理解的模型
g_owner.set_llm_intent_model(llm)
g_owner.set_llm_prompt_model(llm)

# 用于优雅退出的标志
running = True


def signal_handler(sig, frame):
    global running
    print("\n正在停止 FSM 线程...")
    g_owner.destroy()
    # 呼吸3次，清除所有的state
    g_owner.breathe(1)  # 执行一次状态机更新
    running = False


def run_server():
    """在独立线程中运行 FSM 的呼吸循环"""
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
    input_text = GlobalFunction.load_file("tests/文件_银行回单.md")
    g_owner.recv_message("用户", input_text)

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
