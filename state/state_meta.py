import os
from datetime import datetime

from common import GlobalFunction
from state import BaseState, StateOwner
from state.state_doc import WordParseState
from state.state_task import ExecuteTaskState


# 元认知
class MetaBaseState(BaseState):
    def __init__(self, **kwargs):
        super().__init__()

    def Enter(self, owner):
        pass


class MetaListenState(MetaBaseState):
    stateName = "MetaListenState"

    def __init__(self, **kwargs):
        super().__init__(**kwargs)

    def Enter(self, owner):
        super().Enter(owner)

    def Execute(self, owner):
        owner.remove_state(self)


def meta_listen(response: str, owner: StateOwner, think: str, **kwargs):
    return owner.add_state(MetaListenState(response, think, **kwargs))


class MetaSpeakState(MetaBaseState):
    stateName = 'MetaSpeakState'

    def __init__(self, **kwargs):
        super().__init__(**kwargs)

    def Enter(self, owner):
        # 直接回复的内容，不需要额外思考.
        pass

    def Execute(self, owner):
        """直接问模型，返回response执行Action"""
        owner.execute_chat(state=self)
        owner.remove_state(self)


# @g_tool_registry.register("meta:speak")
# def meta_speak(owner: StateOwner, **kwargs):
#     """
#     常规问答/沟通类/设计类/方案类/知识问答/情感交流/简单指令/概述回复类（需在3秒内响应）
#     """
#     print("响应QA直接回复状态")
#     return owner.add_state(MetaSpeakState(**kwargs))


class MetaTaskState(MetaBaseState):
    """
    生成需要多步推理的动态任务，例如：查询动态信息、统计实时数据、业务办理等
    """
    stateName = "MetaTaskState"
    # 注册的动作或者工具tool的名称
    actionName = "meta:task"

    def __init__(self,
                 name: str,  # 任务名称
                 task: str,  # 任务内容描述
                 goal: str,  # 任务最终要达成的目标
                 type: str,  # 任务类型->(查询统计 | 业务办理 | 搜索问答)
                 **kwargs):
        self.name = name
        self.task = {'name': name, 'task': task, 'goal': goal, 'type': type}
        current_time = datetime.now()
        self.task_id = current_time.strftime("%Y%m%d%H%M%S")
        self.create_time = current_time.strftime("%Y-%m-%d %H:%M:%S")
        super().__init__(**kwargs)

    def Enter(self, owner):
        super().Enter(owner)
        if self.task is None:
            owner.record_role_chat("观察",
                                   "分析用户对话意图,action=meta:task,参数task为空，无法创建任务动作。重新分析。")
            owner.remove_state(self)
            return False

    def Execute(self, owner: StateOwner):
        if self.task:
            self.task['task_id'] = self.task_id
            self.task['create_time'] = self.create_time
            self.task['finished'] = False
            # 记录chat历史
            owner.record_role_chat("行动", f"创建任务：{self.task['name']}(task_id:{self.task['task_id']})")
            # 创建任务对象
            work_task = owner.create_task(self.task)
            # 更新任务记录到文件
            work_task.update_task_record()
        owner.remove_state(self)


class MetaClarifyState(MetaBaseState):
    """
    用户描述的信息过于模糊、缺失关键要素，以至于无法开展具体工作，必须通过提问来澄清。提出的问题要直接针对模糊点，且不能反复询问同一个问题。
    """
    stateName = 'MetaClarifyState'

    def __init__(self,
                 task_id: str,  # 任务id
                 information: str,  # 用户澄清的关键信息
                 **kwargs):
        self.task_id = task_id
        self.information = information
        super().__init__(**kwargs)

    def Enter(self, owner):
        super().Enter(owner)
        if self.task_id is None:
            owner.record_role_chat("观察",
                                   "分析用户对话意图,action=meta:clarify,参数task_id为空，无法重新规划动作。重新分析。")
            owner.remove_state(self)
            return False

    def Execute(self, owner):
        if self.task_id:
            owner.record_role_chat("观察", f"任务({self.task_id})的补充描述：{self.information}")
            # 更新任务规划
            executor_path = GlobalFunction.task_id_to_executor_path(self.task_id)
            with open(executor_path, 'a', encoding='utf-8') as f:
                f.write(f"<用户>: {self.information}")
            # 任务状态更新，执行任务哦
            owner.add_state(ExecuteTaskState(self.task_id))
        owner.remove_state(self)


#
# @g_tool_registry.register("meta:clarify")
# def meta_clarify(
#         task_id: str,  # 任务id
#         information: str,  # 用户澄清的关键信息
#         owner: StateOwner, **kwargs):
#     """
#
#     """
#     print("重新规划动作")
#     return owner.add_state(MetaClarifyState(task_id=task_id, information=information, **kwargs))


class MetaAskState(MetaBaseState):
    """
    向用户提问，获取用户回答。用户描述的信息过于模糊、缺失关键要素，以至于无法开展具体工作，必须通过提问来澄清。不反复询问同一个问题。
    """
    stateName = 'MetaAskState'

    def __init__(self,
                 question: str,  # 需要用户回答澄清的问题
                 **kwargs):
        super().__init__(**kwargs)
        self.question = question

    def Enter(self, owner):
        super().Enter(owner)

    def Execute(self, owner):
        # 向真实的用户发问
        owner.record_role_chat("助手", self.question)
        # 通过消息中间件向用户转发消息f
        owner.remove_state(self)


#
# @g_tool_registry.register("meta:ask")
# def meta_ask(question: str,  # 需要用户回答澄清的问题
#              owner=None, **kwargs):
#     """
#     向用户提问，获取用户回答。用户描述的信息过于模糊、缺失关键要素，以至于无法开展具体工作，必须通过提问来澄清。不反复询问同一个问题。
#     """
#     return owner.add_state(MetaAskState(question, **kwargs))


class MetaReadState(MetaBaseState):
    """
    根据文件uuid，查找文件路径并启动文档解析管线。
    """
    stateName = 'MetaReadState'

    def __init__(self, file_uuid: str,  # 文件uuid
                 **kwargs):
        super().__init__(**kwargs)
        self.file_uuid = file_uuid
        self.file_path = GlobalFunction.uuid_to_file_path(self.file_uuid)

    def Enter(self, owner):
        super().Enter(owner)

    def Execute(self, owner):
        if not self.file_path:
            owner.record_role_chat("观察", f"MetaReadState: 未找到 uuid={self.file_uuid} 对应的文件路径")
            owner.remove_state(self)
            return
        owner.add_state(WordParseState(str(self.file_path)))
        owner.remove_state(self)


#
# @g_tool_registry.register("meta:read")
# def meta_read(file_uuid: str,  # 文件uuid
#               owner: StateOwner, **kwargs):
#     """
#     根据文件uuid，分析文件(pdf,doc,docx,image)内容，生成摘要信息。
#     """
#     # 分析文件，最后得到初步摘要。尝试理解推测用户的需求和意图。
#     print("meta:read")
#     return owner.add_state(MetaReadState(file_uuid, **kwargs))


# 查看所有命令
# print(g_tool_registry.list_commands())  # 输出: ['greet', 'bye']


class ExecutePromptState(BaseState):
    stateName = 'ExecutePromptState'

    def __init__(self, prompt_file: str, **kwargs):
        super().__init__(**kwargs)

        self.prompt_file = prompt_file

    def Enter(self, owner):
        GlobalFunction.log("运行状态", f"{self.stateName} 执行提示词: {self.prompt_file}")
        pass

    def Execute(self, owner):
        action_json = owner.execute_prompt(self.prompt_file)
        GlobalFunction.log("运行状态", f"{self.stateName} 执行结果: {action_json}")
        owner.remove_state(self)


class EvalPromptState(BaseState):
    stateName = 'EvalPromptState'

    def __init__(self, prompt_full_path: str, **kwargs):
        super().__init__(**kwargs)
        self.prompt_full_path = prompt_full_path

    def prompt_to_evaluate(self):
        prompt_content = GlobalFunction.load_file(self.prompt_full_path)
        return prompt_content

    def Enter(self, owner):
        GlobalFunction.log("运行状态", f"开始评估: {self.prompt_full_path}")

    def Execute(self, owner):
        action_list = owner.execute_prompt("评估提示词设计与建议", state=self)
        GlobalFunction.log("运行状态", f"评估完成: {self.prompt_full_path}")
        if action_list:
            GlobalFunction.log("运行状态", f"评估结果: {action_list[0]}")
            if isinstance(action_list[0], dict):
                # self.prompt_full_path ，生成一个同名文件，后缀为_评估建议.txt
                eval_file_path = self.prompt_full_path.replace(".md", "_(评估建议).md")
                GlobalFunction.log("运行状态", f"评估结果已保存到: {eval_file_path}")
                if "text" in action_list[0]:
                    GlobalFunction.write_file(eval_file_path, action_list[0]['text'])
        owner.remove_state(self)


class EvalState(BaseState):
    stateName = 'EvalState'

    def __init__(self, **kwargs):
        super().__init__(**kwargs)

    def eval_prompts(self, owner: StateOwner):
        # 遍历这个路径下的所有提示词:        GlobalFunction.prompt_path()
        prompt_dir = GlobalFunction.prompt_path()
        all_prompts = []

        # 遍历提示词目录，收集所有提示词文件
        for root, dirs, files in os.walk(prompt_dir):
            for file in files:
                if file.endswith('.md') and not file.endswith("_(评估建议).md"):
                    rel_path = os.path.relpath(os.path.join(root, file), prompt_dir)
                    # meta目录中的提示词不需要评估
                    if "meta" not in rel_path and "system" not in rel_path:
                        all_prompts.append(rel_path)

        # 遍历每个提示词进行评估
        for prompt_path in all_prompts:
            full_path = os.path.join(prompt_dir, prompt_path)
            owner.add_state(EvalPromptState(full_path))
        GlobalFunction.log("运行状态", f"总共评估 {len(all_prompts)} 个提示词")

    def Enter(self, owner):
        GlobalFunction.log("运行状态", "开始评估")

    def Execute(self, owner):
        self.eval_prompts(owner)
        owner.remove_state(self)

    def Exit(self, owner):
        GlobalFunction.log("运行状态", "评估完成，退出评估状态。")
