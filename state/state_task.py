import json
import os
from typing import Union, List

from common import GlobalFunction, WorkTask, GlobalNames, ChatMessage
from .base_state import BaseState, StateOwner


# 自动规划任务
class AutoPlanTaskState(BaseState):
    stateName = "AutoPlanTaskState"

    def __init__(self, text, **kwargs):
        super().__init__(**kwargs)

    def Enter(self, owner):
        pass

    def build_prompt(self, task_desc, similar_template=False):
        if similar_template:
            example = json.dumps(similar_template, ensure_ascii=False, indent=2)
            prompt = f"""
            下面是一个已完成的任务模板示例，你可以参考其结构和风格：
            ```json
            {example}
            ```
            现在，请为以下新任务生成信息需求依赖：
            任务描述：{task_desc}
            """
        else:
            prompt = f"""
            现在，请为以下任务生成信息需求依赖：
            任务描述：{task_desc}
            """
            return prompt

    def generate_template(self, owner):
        owner.exist_task_list = []
        topic_list = owner.execute_prompt_task("LLM自动生成领域")
        for topic in topic_list:
            owner.task_topic = topic
            task_list = owner.execute_prompt_task("LLM自动生成领域任务")
            print(f"LLM自动生成领域任务:{task_list}")
            for task_desc in task_list:
                plan_task_list = owner.execute_prompt_task("规划任务")
                # 规划任务列表,将任务列表存储起来。再二次召回继续构建模板

            owner.exist_task_list = list(set(owner.exist_task_list + task_list))
        # info_parsed = ast.literal_eval(info_raw)
        # return info_parsed

    def search_task_plan(self, owner):
        return None

    def fetch_task_plan(self, owner):
        # engram召回任务，如果没有任务，则创建任务。

        pass

    # 验证任务规划
    def verify_task_plan(self, owner):
        if not self.search_task_plan(owner):
            # 创建任务规划
            # 初始化已知信息，已有的技能。
            # 构建任务规划
            self.create_task_plan(owner)
            pass

    # 执行任务规划
    def execute_task_plan(self, owner):
        pass

    def Enter(self, owner):
        # 这肯定是一个树状的目录结构

        pass

    def Execute(self, owner):
        # 1.获取任务规划
        task_plan = self.fetch_task_plan(owner)
        # 2.验证任务规划
        if task_plan:
            if self.verify_task_plan(task_plan):
                # 3.执行任务规划
                self.execute_task_plan(task_plan)

    def Exit(self, owner):
        pass


# 扫描是否存在新的任务
class ScanTaskState(BaseState):
    stateName = "ScanTaskState"

    def __init__(self, **kwargs):
        # 无限生命
        super().__init__(**kwargs)
        self._duration = 999999999999

    def Enter(self, owner):
        # 加载数据到内存
        pass

    def scan_active_task(self, owner):
        # 清空原有的任务列表
        owner.active_tasks_dict = {}
        # 清空所有的历史任务
        owner.tasks_dict = {}
        # 如果是扫描就是清空
        task_path = GlobalFunction.task_path()
        # 枚举这个目录下的下一级目录中的"任务执行.md"文件
        for root, dirs, files in os.walk(task_path):
            for dir_name in dirs:
                task_executor_path = os.path.join(root, dir_name, "任务执行.md")
                if os.path.exists(task_executor_path):
                    # 读取文件内容
                    with open(task_executor_path, 'r', encoding='utf-8') as f:
                        file_data = f.read()
                        task_json_list = GlobalFunction.parse_llm_output_json(file_data, "json")
                        # 解析JSON内容并添加到列表
                        if task_json_list:
                            wtask = WorkTask(task_json_list[0], owner=owner)
                            owner.tasks_dict[wtask.task_id] = wtask
                            if wtask.finished == False:
                                # 任务未完成，添加到活跃任务列表
                                owner.active_tasks_dict[wtask.task_id] = wtask

    def Execute(self, owner):
        # 扫描是否存在新的任务
        if not owner.active_tasks_dict or not owner.tasks_dict:
            # 没有任务，扫描新任务
            self.scan_active_task(owner)
        # 任务扫描完成，移除扫描状态
        for task in owner.active_tasks_dict.values():
            owner.add_state(ExecuteTaskState(task_id=task.task_id))
        owner.sys_breathe_log("任务扫描完成，移除扫描状态")
        owner.list_active_tasks()

        owner.remove_state(self)

    def Exit(self, owner):
        pass


# 暂时作为任务加载器
class LoadTaskState(BaseState):
    stateName = "LoadTaskState"

    def __init__(self, text, **kwargs):
        super().__init__(**kwargs)

    def Enter(self, owner):
        pass

    def Execute(self, owner):
        # 最好是能够切换到TTS的线程
        owner.remove_state(self)
        pass

    def Exit(self, owner):
        pass


class TaskLiveState(BaseState):
    """任务状态"""
    stateName = 'TaskLiveState'

    def __init__(self, work_task: WorkTask):
        super().__init__()
        self.work_task = work_task

    def task_execute_history(self):
        # 返回这个状态正在处理的任务执行历史记录
        return self.work_task.task_execute_history()

    def Enter(self, owner):
        super().Enter(owner)

    def Execute(self, task_owner):
        """执行任务"""
        try:
            if self.work_task.finished != True:
                action_json = task_owner.execute_prompt(user_prompt=GlobalNames["规划任务下一步动作"], state=self, system_prompt=GlobalNames["任务提示词"])
                # 执行action
                execute_res = task_owner.owner.execute_action(action_json)
                # 任务状态更新，执行任务哦
            elif self.work_task.finished == True:
                task_owner.remove_state(self)
        except Exception as e:
            task_owner.sys_breathe_log(f"任务执行异常: {e}")

    def Exit(self, owner):
        super().Exit(owner)
        # 任务完全执行完成，进行反思和思考


class TaskOwner(StateOwner):
    """任务所有者"""

    def __init__(self, work_task: WorkTask,
                 owner: StateOwner
                 ):
        super().__init__(owner._config)
        self.owner = owner
        self.work_task = work_task
        self.chat_hist_file_path = self.work_task.chat_hist_file_path()
        self.refresh_config()
        # 必需品
        self.add_state(TaskLiveState(work_task))

    def task_execute_history(self):
        return self.work_task.task_execute_history()

    # def record_role_chat(self, role, text):
    #     # 每次都去读取文件
    #     record = ChatMessage(role, text)
    #     self.chat_records.append(record)
    #     # 用户和助手信息需要同步到主线
    #     if role in ["用户", "助手"]:
    #         self.owner.record_role_chat(role, text)

    def refresh_config(self):
        '''初始化chat_records,可以得到聊天记录'''
        chat_history = self.work_task.task_execute_history()
        # 分解任务执行历史记录对话
        chat_message_list: List[ChatMessage] = GlobalFunction.parse_chat_text_to_messages(chat_history)
        # 聊天历史最近的数据，最多最近的10条
        # deque,只保留默认的前20条对话记录
        self.chat_records.extend(chat_message_list)


class ExecuteTaskState(BaseState):
    '''
    执行具体的任务: 查看用户任务列表，激活任务的下一步。
    '''
    stateName = "ExecuteTaskState"

    def __init__(self, task_id=None, **kwargs):
        super().__init__(**kwargs)
        self.task_id = task_id

    def Enter(self, owner):
        self.work_task = owner.tasks_dict.get(self.task_id)
        self.task_owner = TaskOwner(self.work_task, owner)

    def Execute(self, owner):
        if self.work_task and self.work_task.finished == False:
            # 呼吸一次
            self.task_owner.breathe()


# 暂时作为任务加载器
class ListToolState(BaseState):
    """列出工具状态，category为list[str]工具分类标签：task,meta,file"""
    stateName = "ListToolState"
    actionName = "task:list_tool"

    def __init__(self,
                 category: list[str] = None, # 分类标签, None表示所有工具
                 **kwargs):
        super().__init__(**kwargs)
        self.category = category

    def Enter(self, owner):
        tools = owner.list_tools(self.category)
        self.text = f"**列出工具**({self.category if self.category else '所有工具'}): \n{json.dumps(tools, ensure_ascii=False, indent=2)}\n"
        owner.sys_breathe_log(self.text)
        owner.record_role_chat("行动", self.text)

    def Execute(self, owner):
        # 最好是能够切换到TTS的线程
        owner.remove_state(self)
        pass

    def Exit(self, owner):
        pass
