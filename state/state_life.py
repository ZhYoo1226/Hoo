import json

from common import g_yaml_config, GlobalFunction
from .base_state import BaseState
from .state_clear import ClearChatState, ClearTaskState, ClearEvalAdviceState
from .state_keyboard import WaitInputState
from .state_task import ScanTaskState, ExecuteTaskState


# 生命状态

class LiveState(BaseState):
    stateName = "LiveState"

    def __init__(self, **kwargs):
        # 无限生命
        super().__init__(**kwargs)
        self._duration = 999999999999
        self.gpu_client = None
        # tts客户端
        self.tts_client = None
        # 大模型客户端
        self.think_intent_client = None
        # 音视频推理结果

    # def start_tts(self, avatar: SolverOwner):
    #     if self.tts_client is None:
    #         if avatar.tts_engine == "cosyvoice":
    #             from tts import CosyVoiceTTS
    #             self.tts_client = CosyVoiceTTS(avatar.params, avatar)
    #         elif avatar.tts_engine == "fishtts":
    #             from tts import FishTTS
    #             self.tts_client = FishTTS(avatar.params, avatar)
    #         elif avatar.tts_engine == "skytts" or avatar.tts_engine == "sky-tts":
    #             from tts import SkyTTS
    #             self.tts_client = SkyTTS(avatar.params, avatar)
    #         else:
    #             # 默认使用SkyTTS
    #             print(f"⚠️ 未知的TTS引擎: {avatar.tts_engine}, 使用默认的SkyTTS")
    #             from tts import SkyTTS
    #             self.tts_client = SkyTTS(avatar.params, avatar)
    #     self.tts_client.start_working()
    #
    def start_intent_think(self, owner):
        from .state_think import ThinkClient
        if self.think_intent_client is None:
            self.think_intent_client = ThinkClient(owner)
        self.think_intent_client.start_working()

    #
    #
    # def start_gpu(self, owner):
    #     if self.gpu_client is None:
    #         self.gpu_client = Thread(target=owner.process_speaking_face, args=(owner.quit_event, owner))
    #         self.gpu_client.start()
    #         t = Thread(target=owner.process_video_frames, args=(owner.quit_event, owner))
    #         t.start()

    def Enter(self, owner):
        owner.sys_breathe_log("进入生命状态")
        # 清除聊天记录
        if g_yaml_config["chat"]["launch_clear_chat"]:
            owner.add_state(ClearChatState())
        # 清除任务
        if g_yaml_config["chat"]["launch_clear_task"]:
            owner.add_state(ClearTaskState())
        # 清除评估建议
        if g_yaml_config["chat"]["launch_clear_prompt_eval"]:
            owner.add_state(ClearEvalAdviceState())

        # 加载数据到内存
        owner.load_memory()
        # 启动一个LLM线程 对话的意图
        self.start_intent_think(owner)
        # 去执行任务目录下，未完成的任务。
        # self.start_task_execute(owner)
        # 等待用户输入键盘
        owner.add_state(WaitInputState())
        # 扫描任务目录，查找未完成的任务
        owner.add_state(ScanTaskState())
        # 执行任务
        owner.add_state(ExecuteTaskState())
        # 工具
        owner.sys_breathe_log(f"工具列表: {json.dumps(owner.list_tools(), ensure_ascii=False, indent=2)}")
        # 是否debug
        if g_yaml_config["dev"]["debug"]:
            owner.add_state(DebugState())

    def Execute(self, owner):
        pass

    def Exit(self, owner):
        owner.quit_event.set()  # 设置事件，通知所有线程退出
        pass


class ExampleState(BaseState):
    stateName = "ExampleState"

    def __init__(self, text, **kwargs):
        super().__init__(**kwargs)
        self.text = text

    def Enter(self, owner):
        assert owner.current_action is not None, "当前行为状态不能为空"
        pass

    def Execute(self, owner):
        # 最好是能够切换到TTS的线程
        owner.remove_state(self)
        pass

    def Exit(self, owner):
        pass


class DebugState(BaseState):
    stateName = "DebugState"

    def log(self, msg):
        return GlobalFunction.log("DEBUG", msg)

    def Enter(self, owner):
        self.log("进入调试状态")

    def Execute(self, owner):
        # 打印工具信息
        self.log(f"工具列表【owner.list_tools】:\n {json.dumps(owner.list_tools(), ensure_ascii=False, indent=2)}\n---\n")
        self.log(
            f"meta工具列表【owner.list_meta_tools】:\n {json.dumps(owner.list_meta_tools(), ensure_ascii=False, indent=2)}\n---\n")
        self.log(
            f"文件工具列表【owner.list_file_tools】:\n {json.dumps(owner.list_file_tools(), ensure_ascii=False, indent=2)}\n---\n")
        self.log(
            f"web工具列表【owner.list_web_tools】:\n {json.dumps(owner.list_web_tools(), ensure_ascii=False, indent=2)}\n---\n")
        self.log(
            f"激活任务列表【owner.list_active_task】:\n {json.dumps(owner.list_active_task(), ensure_ascii=False, indent=2)}\n---\n")

        owner.remove_state(self)

    def Exit(self, owner):
        pass
