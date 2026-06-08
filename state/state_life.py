import json
import os
from typing import List

from common import g_yaml_config, GlobalFunction, GlobalNames
from .base_state import BaseState, StateOwner
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
        # 扫描领域模型
        owner.add_state(ScanDomainModelState())
        # 启动一个LLM线程 对话的意图
        self.start_intent_think(owner)
        # 去执行任务目录下，未完成的任务。
        # self.start_task_execute(owner)
        # 等待用户输入键盘
        owner.add_state(WaitInputState())
        # 扫描任务目录，查找未完成的任务
        owner.add_state(ScanTaskState())
        # 执行任务
        # owner.add_state(ExecuteTaskState())

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
            f"激活任务列表【owner.list_active_tasks】:\n {json.dumps(owner.list_active_tasks(), ensure_ascii=False, indent=2)}\n---\n")

        owner.remove_state(self)

    def Exit(self, owner):
        pass




class ScanDomainModelState(BaseState):
    """扫描领域模型，更新到memory(领域模型)"""
    stateName = 'ScanDomainModelState'

    def __init__(self, **kwargs):
        super().__init__(**kwargs)

    def Enter(self, owner):
        GlobalFunction.log("运行状态", "开始扫描领域模型")

    def _init_domain_models(self, owner: StateOwner):
        """初始化域模型"""

        """初始化提示词索引，扫描目录下所有md文件"""
        index_list = []
        if not os.path.exists(GlobalFunction.domain_model_path()):
            self.sys_breathe_log(f"警告: 领域模型工作目录不存在: {GlobalFunction.domain_model_path()}")
            return

        # 遍历目录下所有md文件
        GlobalFunction.log("运行状态", f"开始扫描目录 {GlobalFunction.domain_model_path()} 下的所有md文件，构建领域模型索引")
        total = 0
        # 递归遍历目录及所有子目录下所有md文件
        for root, dirs, files in os.walk(GlobalFunction.domain_model_path()):
            for filename in files:
                if filename.endswith('.md'):
                    file_path = os.path.join(root, filename)
                    GlobalFunction.log("运行状态", f"发现域模型文件: {file_path}")
                    # 读取文件内容
                    text_content = GlobalFunction.load_file(file_path)
                    actor_list = GlobalFunction.parse_markdown_title(text_content, "###")
                    if not actor_list:
                        continue
                    GlobalFunction.log("运行状态", f"提取到领域模型的角色: {actor_list}")
                    task_name_list = GlobalFunction.parse_markdown_title(text_content, "####")
                    if not task_name_list:
                        continue
                    GlobalFunction.log("运行状态", f"提取到领域模型的任务名称: {task_name_list}")

                    if owner.memory(GlobalNames["领域模型"]):
                        owner.memory(GlobalNames["领域模型"]).observe(
                            text_content,
                            tags=[*actor_list, *task_name_list],
                            actors=actor_list,
                            # 重要性，用于检索
                            salience=0.8
                        )
                        GlobalFunction.log("运行状态", f"--领域模型--\n{text_content}")
                        total += 1
                    else:
                        print(f"警告: 记忆库未初始化，无法存储领域模型: {file_path}")
        # 构建索引
        GlobalFunction.log("运行状态", f"系统领域模型索引构建完成，共 【{total}】 个领域模型")

    def Execute(self, owner):
        self._init_domain_models(owner)
        owner.remove_state(self)


class ObserveDomainModelState(BaseState):
    """
    观察领域模型，总结领域模型信息
    """
    stateName = 'ObserveDomainModel'
    actionName = 'observe_domain_model'

    def __init__(self,
                 model_desc: str,
                 model_actors: List[str],
                 **kwargs):
        super().__init__(**kwargs)
        self.model_desc = model_desc
        self.model_actors = model_actors

    def store_domain_model(self, owner: StateOwner, model):
        """将领域模型存入记忆库"""
        # 用于检索的内容可以包含多个维度，例如：名称、描述、关键词
        searchable_content = f"{model.name} {model.description} {' '.join(model.keywords)}"

        # mem.observe() 是存储原始事件的接口
        owner._memory.observe(
            searchable_content,
            tags=[model.category, *model.keywords],
            actors=["System"],
            salience=0.8
        )

    def Enter(self, owner):
        GlobalFunction.log("运行状态", "开始评估")

    def Execute(self, owner):
        self.eval_prompts(owner)
        owner.remove_state(self)

    def Exit(self, owner):
        GlobalFunction.log("运行状态", "评估完成，退出评估状态。")
