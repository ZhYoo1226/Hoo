import queue
import sys
import threading

from .base_state import BaseState


# ---------- 输入监听线程 ----------
class InputListener:
    def __init__(self):
        self._queue = queue.Queue()
        self._running = False
        self._thread = None

    def start(self):
        """启动输入监听线程"""
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._listen, daemon=True)
        self._thread.start()

    def stop(self):
        """停止输入监听线程"""
        self._running = False
        if self._thread:
            self._thread.join(timeout=1)

    def _listen(self):
        while self._running:
            try:
                # print("请你可以在这儿模拟输入消息:")
                line = sys.stdin.readline()
                if line:
                    self._queue.put(line.strip('\n\r'))
                else:
                    # EOF 时退出
                    break
            except Exception as e:
                print(f"键盘输入错误: {e}")
                break

    def get_input(self, block=False, timeout=None):
        """非阻塞获取输入，返回 (是否有输入, 输入内容)"""
        try:
            data = self._queue.get(block=block, timeout=timeout)
            return True, data
        except queue.Empty:
            return False, None


# ---------- 等待输入的状态 ----------
class WaitInputState(BaseState):
    stateName = "WaitInputState"

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.listener = InputListener()
        self._duration = 9999999999999

    def Enter(self, owner):
        """进入状态时启动输入监听"""
        owner.sys_breathe_log("等待用户输入... (输入任意内容后继续)")
        self.listener.start()

    def Execute(self, owner):
        """每次呼吸时检查是否有输入"""
        got, new_msg = self.listener.get_input(block=False, timeout=0.1)
        if got and new_msg:
            # owner.sys_breathe_log(f"收到输入消息，[用户]: {new_msg}")
            # owner.add_state(NewMessageState("用户",new_msg))
            # 推送一条意图未理解的意图消息
            owner.recv_message("用户", new_msg)
        if owner.quit_event.is_set():
            owner.remove_state(self)
            self.listener.stop()

    def Exit(self, owner):
        """离开状态时停止监听线程"""
        self.listener.stop()
        owner.quit_event.set()  # 设置事件，通知所有线程退出
        print("退出等待输入状态")
