import json
import os

from common import GlobalFunction, WorkTask
from .base_state import BaseState


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
        owner.active_task_list = []
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
                        task_json_list = GlobalFunction.parse_llm_text_output_json(file_data, "#任务信息摘要")
                        # 解析JSON内容并添加到列表
                        if task_json_list:
                            owner.active_task_list.append(WorkTask(task_json_list[0], owner=owner))

    def Execute(self, owner):
        # 扫描是否存在新的任务
        if not owner.active_task_list:
            # 没有任务，扫描新任务
            self.scan_active_task(owner)
        pass

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


class ExecuteTaskState(BaseState):
    '''
    执行具体的任务: 查看用户任务列表，激活任务的下一步。
    '''
    stateName = "ExecuteTaskState"

    def __init__(self, task_id=None, **kwargs):
        super().__init__(**kwargs)
        self.task_id = task_id

    def Enter(self, owner):
        pass

    def Execute(self, owner):
        #        self.active_task_list: list[WorkTask] = []
        # print(f"当前任务列表:{owner.active_task_list}")
        for task in owner.active_task_list:
            self.task = task
            # print(f"当前任务:{self.task}")
            # 判断已有的action状态是否完成
            for action_state in self.task.action_list:
                if action_state and action_state.finished:
                    self.task.action_list.remove(action_state)

            # 如果询问用户，用户并没有回答，这个action也是执行完成了，所以也是不需要继续执行的规划的。

            if not self.task.action_list:
                action_json = owner.execute_prompt(prompt_file="规划任务下一步动作", state=self)
                action_state = owner.execute_action(action_json)
                self.task.action_list.append(action_state)

        '''推理下一步动作'''
