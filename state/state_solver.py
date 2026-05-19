import importlib
import json
import os
import pkgutil
import re
import threading
from collections import deque
from queue import Queue
from typing import List

from common import GlobalFunction, g_yaml_config
from common import WorkTask, PromptIndex, ChatMessage
from common import g_tool_registry
from l2p import load_yaml
from .base_state import StateOwner, BaseState


class SolverOwner(StateOwner):
    def __init__(self, g_yaml_config):
        super().__init__()
        self.current_action = None
        # owner是否退出标志，使用线程信号
        self.quit_event = threading.Event()
        self.config = g_yaml_config
        print(self.config)
        # sys_error日志
        self._log_sys_error = []

        self.workspace_path = self.config["app"]["workspace_path"]
        # 将相对路径转换为绝对路径
        self._owner_props_path = os.path.abspath(
            os.path.join(self.workspace_path, self.config["app"]["owner_props_yaml"]))
        # 聊天历史路径，模型可能会读取这个路径
        self._chat_path = os.path.abspath(os.path.join(self.workspace_path, self.config["app"]["chat_path"]))
        self._log_path = os.path.abspath(os.path.join(self.workspace_path, self.config["app"]["log_path"]))
        # 任务路径，智能体可能会用到这个路径

        self._prompt_path = os.path.join(self.workspace_path, self.config["app"]["prompt_path"])

        # 聊天历史最近的数据，最多最近的10条
        self.max_chat_record_in_mem = self.config["chat"]["max_chat_record_in_mem"]
        # deque,只保留默认的前20条对话记录
        self.chat_records = deque(maxlen=self.max_chat_record_in_mem)
        # 意图队列，用户新发送的消息
        self.intent_queue = Queue()  # 真正最终的传给前端的回答，兼容流式与非流。使用队列来实现阻塞
        # 意图理解模型
        self.llm_intent_model = None
        # 提示词执行执行模型
        self.llm_prompt_model = None
        # 执行模型
        self.llm_action_model = None
        # 文件摘要 workspace/user/files/
        self.files_abstract = None
        # 文件UUID映射
        self._uuid_maps = {}
        # 活跃任务列表，指定为WorkTask类型
        self.active_task_list: list[WorkTask] = []
        self.g_store = {}
        self.agent_soul = None
        self.mine_profile = None
        self.agent_meta = None
        self.refresh_config()

    def read_prompt(self, name: str):
        """读取提示词"""
        prompt_text, prompt_json = self.prompt_index.load_prompt(name, owner=self, state=None)
        return prompt_text

    def read_meta_predict(self):
        """读取智能体的元认知行为"""
        if not self.agent_meta:
            output_text, prompt_json = self.prompt_index.load_prompt("元认知分类", owner=self, state=None)
            self.agent_meta = output_text
        return self.agent_meta

    def read_user_profile(self):
        """读取我的个人信息文件"""
        if not self.mine_profile:
            mine_profile_path = os.path.join(self.workspace_path, self.config["app"]["user_profile_path"])
            with open(mine_profile_path, "r", encoding="utf-8") as f:
                self.mine_profile = f.read()
        return self.mine_profile

    def read_agent_profile(self):
        """读取智能体的SOUL.md文件"""
        if not self.agent_soul:
            soul_path = os.path.join(self.workspace_path, self.config["app"]["agent_profile_path"])
            with open(soul_path, "r", encoding="utf-8") as f:
                self.agent_soul = f.read()
        return self.agent_soul

    def refresh_config(self):
        """刷新提示词，可能有更改，也可能有新增的提示词"""
        self.__init_prompts(self._prompt_path)

        self.__init_props()

    def __init_props(self):
        """初始化属性"""
        props = load_yaml(self._owner_props_path)

        # 将props中的属性按照层级展开，赋予给self
        def set_properties(obj, key, value):
            if isinstance(value, dict):
                if hasattr(obj, key) and isinstance(getattr(obj, key), dict):
                    # 如果属性已存在且是字典，继续递归展开
                    for sub_key, sub_value in value.items():
                        set_properties(getattr(obj, key), sub_key, sub_value)
                else:
                    # 如果属性不存在或不是字典，先创建一个空字典，再设置子属性
                    new_dict = {}
                    setattr(obj, key, new_dict)
                    for sub_key, sub_value in value.items():
                        set_properties(new_dict, sub_key, sub_value)
            else:
                # 直接设置属性
                self.sys_breathe_log(f"设置owner属性:{key}={value}")
                setattr(obj, key, value)

        # 遍历props中的顶级键值对并设置
        for key, value in props.items():
            set_properties(self, key, value)

    # def _sys_error(self, msg: str):
    #     """系统错误日志"""
    #     self._log_sys_error.append(msg)

    def __init_prompts(self, prompt_path: str):
        """初始化提示词索引，扫描目录下所有md文件"""
        index_list = []
        if not os.path.exists(prompt_path):
            self.sys_breathe_log(f"警告: 提示词工作目录不存在: {prompt_path}")
            return

        # 遍历目录下所有md文件
        self.log("运行状态", f"开始扫描目录 {prompt_path} 下的所有md文件，构建提示词索引")
        # 递归遍历目录及所有子目录下所有md文件
        for root, dirs, files in os.walk(prompt_path):
            for filename in files:
                if filename.endswith('.md') and not filename.endswith("_(评估建议).md"):
                    file_path = os.path.join(root, filename)
                    self.log("运行状态", f"发现提示词文件: {file_path}")
                    # 读取文件内容
                    content = GlobalFunction.load_file(file_path)
                    # 提取所有{xxx}格式的占位符（包含空格、不换行），支持中文和多层级xx.yy.zz(path = 'c:/test/xxx.pdf')格式
                    # 匹配 {xxx}、{yy}、{zz}、{xx.yy.bb}、{xx.yy.func(abc)} 等格式
                    pattern = re.compile(r'\{([a-zA-Z0-9_.\u4e00-\u9fa5()=\s\'"/:-]+)\}')
                    placeholders = pattern.findall(content)
                    # 去重
                    placeholders = list(set(placeholders))

                    # 从文件名获取提示词名称（去掉后缀）
                    name = os.path.splitext(filename)[0]

                    # 构建索引条目
                    item = {
                        "名称": name,
                        "文件名": filename,
                        "文件路径": os.path.relpath(file_path, prompt_path),  # 保存相对路径便于后续查找
                        "输入参数": {p: "input" for p in placeholders} if placeholders else {}
                    }
                    self.log("运行状态",
                             f"owner.prompt_index 添加提示词索引条目: \n{json.dumps(item, ensure_ascii=False, indent=2)}")
                    index_list.append(item)

        # 构建索引
        self.prompt_index = PromptIndex(index_list)
        self.log("运行状态", f"系统提示词索引构建完成，共 {len(index_list)} 个提示词")

    def execute_prompt(self, user_prompt: str, state: BaseState = None, system_prompt: str = "系统提示词") -> List[
        dict]:
        """执行提示词，返回response执行Action"""
        # prompt_file=规划任务.md
        assert self.llm_prompt_model, "提示词执行模型未设置，请设置owner.llm_prompt_model的模型。"
        self.llm_prompt_model.reset_tokens()
        # 填充提示词,返回填充后的文本和提示词的json
        prompt_text, prompt_json = self.prompt_index.load_prompt(user_prompt, owner=self, state=state)
        # 不要记录执行提示词的日志
        self.record_execute_prompt(prompt_json)
        # 必须加入的系统提示词
        system_text, system_json = self.prompt_index.load_prompt(system_prompt, owner=self, state=state)
        messages = [{"role": "system", "content": system_text}]
        # 获取历史聊天记录（支持单独的子任务)
        for item in self.get_chat_conversation(limit=-1, roles=["用户", "思考", "助手", "观察", "系统"]):
            messages.append(item)
        messages.append({"role": "user", "content": prompt_text})

        llm_output = self.llm_prompt_model.query(messages=messages)  # prompt model
        query_log = self.llm_prompt_model.get_query_log()
        self.llm_prompt_model.reset_query_log()
        self.log("模型日志", f"{json.dumps(query_log, ensure_ascii=False, indent=2)}")
        if llm_output:
            json_list = GlobalFunction.parse_llm_output_json(llm_output)
            return json_list
        else:
            return []

    def set_llm_intent_model(self, model):
        """设置意图识别模型"""
        self.llm_intent_model = model

    def set_llm_prompt_model(self, model):
        """设置提示词执行模型"""
        self.llm_prompt_model = model

    def set_llm_action_model(self, model):
        """设置Action执行模型"""
        self.llm_action_model = model

    def set_quit_event(self, event):
        self.quit_event = event

    def create_task(self, json: dict):
        """创建一个任务，加入到激活的任务队列"""
        work_task = WorkTask(json, self)
        self.active_task_list.append(work_task)
        return work_task

    # 加载历史聊天记录
    def load_memory(self):
        """加载记忆信息"""
        print(f"1. 加载历史聊天记录")
        self.load_chat_history()
        print(f"2. 加载")

    def get_chat_history_text(self, limit: int = 0, roles: list = None):
        """获取历史聊天文本,
        limit为0时，默认返回所有聊天记录，否则返回最近limit条记录
        roles为None时，默认返回所有角色的聊天记录
        """
        # 先根据角色过滤消息
        filtered_messages = []
        for msg in self.chat_records:
            if roles is None or msg.role in roles:
                filtered_messages.append(msg)
        # 处理limit，只取最近limit条
        if limit > 0 and limit >= len(filtered_messages):
            filtered_messages = filtered_messages[-limit:]
        # 将每个ChatMessage对象转换为字符串再拼接
        return '\n'.join(map(str, filtered_messages))

    def get_chat_conversation(self, limit: int = 0, roles: list = None):
        """获取历史聊天文本,
        limit为0时，默认返回所有聊天记录，否则返回最近limit条记录
        roles为None时，默认返回所有角色的聊天记录
        """
        # 先根据角色过滤消息
        filtered_messages = []
        for msg in self.chat_records:
            if roles is None or msg.role in roles:
                filtered_messages.append(msg)
        # 处理limit，只取最近limit条
        if limit > 0 and limit >= len(filtered_messages):
            filtered_messages = filtered_messages[-limit:]
        # filtered_messages 组装为json 列表
        json_messages = [msg.to_conversation() for msg in filtered_messages]
        return json_messages

    # 加载历史聊天记录
    def load_chat_history(self):
        """加载历史聊天记录"""
        # 1.向聊天历史记录中增加消息
        file_path = GlobalFunction.chat_hist_store_file()
        dir_name = os.path.dirname(file_path)
        if dir_name:
            os.makedirs(dir_name, exist_ok=True)
        if not os.path.exists(file_path):
            # 如果文件不存在，初始化一个空的deque
            return
        # 搞配置文件
        n_bytes = self.config["chat"]["reload_chat_max_bytes"]
        latest_500_text = None
        with open(file_path, 'rb') as f:
            # 获取文件总大小
            f.seek(0, 2)  # 移动到文件末尾
            file_size = f.tell()
            # 计算要读取的起始位置
            start = max(0, file_size - n_bytes)
            f.seek(start)
            # 读取从 start 到文件末尾的数据
            latest_500_text = f.read()

        if latest_500_text:
            # 解码，忽略截断导致的无效字节
            text = latest_500_text.decode('utf-8', errors='ignore')
            # 按行首的 "<xxx>: " 切分，保留分隔符本身
            # (?=...) 是正向预查，匹配位置但不消费字符，从而保留标记在结果中
            pattern = re.compile(r'(?=^<[^>]+>: )', re.MULTILINE)
            parts = pattern.split(text)
            # split 会产生一个空字符串在开头（如果文本以标记开头），过滤掉
            parts = [ChatMessage(data=p.strip().rstrip('\n')) for p in parts if p]
            # 用 deque 保留最后 maxlen 个条目，如果超过则自动丢弃最早的
            self.chat_records = deque(parts, maxlen=self.max_chat_record_in_mem)
            self.sys_breathe_log(f"加载历史聊天{file_path},数据条数:{len(self.chat_records)}")

    def recv_message(self, role: str, text: str) -> None:
        """
        接收外部的消息，是否需要进行处理。不知道，需要进行判断。
        将一条发言记录以 JSONL 格式追加到文件中。
        Args:
            role: 角色，如 "用户"、"助手"、"思考"、"观察" 等
            text: 发言内容
        """
        # 进行意图理解
        self.intent_queue.put((role, text))

    def execute_chat(self, state = None,system_prompt: str = "系统提示词"):
        """
        同步执行聊天
        """
        assert self.llm_prompt_model, "提示词执行模型未设置，请设置owner.llm_prompt_model的模型。"
        self.llm_prompt_model.reset_tokens()
        system_text, system_json = self.prompt_index.load_prompt(system_prompt, owner=self, state=state)
        messages = [{"role": "system", "content": system_text}]
        #加载聊天记录,如果state有get_chat_conversation方法,则使用state的get_chat_conversation方法,否则使用self的get_chat_conversation方法
        _conversation_func = self.get_chat_conversation
        if state and hasattr(state, "get_chat_conversation"):
            _conversation_func =getattr(state, "get_chat_conversation")
        #历史聊天
        for item in _conversation_func(limit=-1, roles=["用户", "助手", "观察"]):
            messages.append(item)

        llm_output = self.llm_prompt_model.query(messages=messages)  # prompt model
        # 直接聊天，不会输出action指令
        # 解析思考
        query_log = self.llm_prompt_model.get_query_log()
        self.llm_prompt_model.reset_query_log()
        self.log("模型日志", f"{json.dumps(query_log, ensure_ascii=False, indent=2)}")
        think_text = GlobalFunction.parse_llm_think(llm_output)
        if think_text:
            self.record_role_chat("思考", think_text)
        # 思考之后的内容
        reply_text = GlobalFunction.parse_llm_reply(llm_output)
        if reply_text:
            self.record_role_chat("助手", reply_text)
        else:
            self.record_role_chat("助手", llm_output.strip())

    def execute_action(self, action_json):
        """执行Action
            如果是state:XXXXState,则查找状态，创建状态，并添加add_state到owner。
        """
        # 判断action_json 是dict还是list
        data_list = None
        if isinstance(action_json, list):
            action = action_json[0]
            # 如果action_json的长度大于1，datas等于 action_json[1:]
            data_list = action_json[1:]
        else:
            action = action_json
        # 组装action的data_list数据集，如果有！
        if data_list:
            action["data_list"] = data_list
        action_name = action["action"]
        # 判断是否是 state 操作，格式为 state:xxx
        if action_name.startswith("state:"):
            # 提取 state 名称
            state_name = action_name.split(":", 1)[1]

            # 类级缓存，避免每次都重新扫描导入
            if not hasattr(SolverOwner, "_states_class"):
                SolverOwner._states_class = {}

            # 先从缓存中查找
            if state_name in SolverOwner._states_class:
                state_class = SolverOwner._states_class[state_name]
                state = state_class(**action)
                self.add_state(state)
                return True

            # 缓存未命中，才进行动态查找
            # 遍历state模块下所有py文件，查找对应名称的状态类
            found = False
            state_class = None
            for _, module_name, _ in pkgutil.iter_modules(['state']):
                if module_name == '__init__' or module_name == 'base_state':
                    continue
                try:
                    module = importlib.import_module(f'.{module_name}', package='state')
                    # 遍历模块中的所有类，找到匹配stateName的状态类
                    for attr_name in dir(module):
                        attr = getattr(module, attr_name)
                        if isinstance(attr, type) and issubclass(attr,
                                                                 BaseState) and attr != BaseState:
                            # 优先匹配stateName属性，如果没有则匹配类名
                            match_found = False
                            if hasattr(attr, 'stateName') and attr.stateName == state_name:
                                match_found = True
                            elif attr.__name__ == state_name:
                                match_found = True
                            if match_found:
                                state_class = attr
                                found = True
                                break
                    if found:
                        break
                except Exception as e:
                    self.sys_breathe_log(f"加载状态模块 {module_name} 出错: {e}")
                    continue
            if not found:
                raise ValueError(f"未找到名为 {state_name} 的状态类，请检查状态名称是否正确")

            # 存入缓存，下次直接使用
            SolverOwner._states_class[state_name] = state_class
            state = state_class(**action)
            self.add_state(state)
            return state
        else:
            # 普通工具调用
            return g_tool_registry.execute(tool_name=action_name, owner=self, **action)


# 全局解决器
g_owner = SolverOwner(g_yaml_config)
