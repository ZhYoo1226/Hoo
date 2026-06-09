import json
import os
import queue
import re
import threading
from collections import deque
from queue import Queue
from typing import Dict

from common import GlobalFunction, g_yaml_config, GlobalNames, GlobalObject
from common import WorkTask, PromptIndex, ChatMessage
from l2p import load_yaml
from .base_state import StateOwner


class SolverOwner(StateOwner):
    def __init__(self, g_yaml_config):
        super().__init__(g_yaml_config)
        print(self._config)
        self.current_action = None
        # owner是否退出标志，使用线程信号
        self.quit_event = threading.Event()
        # sys_error日志
        self._log_sys_error = []

        # 将相对路径转换为绝对路径
        # self._owner_props_path = GlobalFunction.owner_props_yaml()
        # 聊天历史路径，模型可能会读取这个路径
        # self._chat_path = GlobalFunction.chat_path()
        # self._log_path = GlobalFunction.log_path()
        # 任务路径，智能体可能会用到这个路径
        # self._prompt_path = GlobalFunction.prompt_path()

        # 意图队列，用户新发送的消息
        self.intent_queue = Queue()  # 真正最终的传给前端的回答，兼容流式与非流。使用队列来实现阻塞
        # 文件摘要 workspace/user/files/
        self.files_abstract = None
        # 加载 files_abstract.parquet 并注册 WorkFile 对象
        abstract_path = GlobalFunction.files_abstract_path()
        if os.path.exists(abstract_path):
            import pandas as pd
            self.files_abstract = pd.read_parquet(abstract_path)
            GlobalFunction.register_work_files(self.files_abstract)
        # 文件UUID映射
        self._uuid_maps = {}
        # 活跃任务列表，指定为WorkTask类型
        self.active_tasks_dict: Dict[str, WorkTask] = {}
        # 所有的任务，已完成和未完成的。
        self.tasks_dict: Dict[str, WorkTask] = {}
        self.g_store = {}
        self.agent_soul = None
        self.mine_profile = None
        self.agent_meta = None
        # gateway 响应队列: request_id → queue.Queue，供 API 端点等待 LLM 回复
        self._response_queues: dict = {}
        # 刷新用户的配置,应该在呼吸的时候刷新
        self.refresh_config()

    def read_prompt(self, name: str):
        """读取提示词"""
        prompt_text, prompt_json = GlobalObject.Prompts.load_prompt(name, owner=self, state=None)
        return prompt_text

    def read_meta_predict(self):
        """读取智能体的元认知行为"""
        if not self.agent_meta:
            output_text, prompt_json = GlobalObject.Prompts.load_prompt(GlobalNames["元认知分类"], owner=self, state=None)
            self.agent_meta = output_text
        return self.agent_meta

    def read_user_profile(self):
        """读取我的个人信息文件"""
        if not self.mine_profile:
            with open(GlobalFunction.user_profile_md(), "r", encoding="utf-8") as f:
                self.mine_profile = f.read()
        return self.mine_profile

    def read_agent_profile(self):
        """读取智能体的SOUL.md文件"""
        if not self.agent_soul:
            with open(GlobalFunction.agent_profile_md(), "r", encoding="utf-8") as f:
                self.agent_soul = f.read()
        return self.agent_soul

    def refresh_config(self):
        """刷新提示词，可能有更改，也可能有新增的提示词"""
        # 加载所有的记忆体信息
        self.load_memories()
        # 加载所有的提示词信息
        self.__init_prompts(GlobalFunction.prompt_path())
        # 加载所有的个人属性
        self.__init_props()

    def __init_props(self):
        """初始化属性"""
        props = load_yaml(GlobalFunction.owner_props_yaml())

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
        GlobalFunction.log("运行状态", f"开始扫描目录 {prompt_path} 下的所有md文件，构建提示词索引")
        # 递归遍历目录及所有子目录下所有md文件
        for root, dirs, files in os.walk(prompt_path):
            for filename in files:
                if filename.endswith('.md') and not filename.endswith("_(评估建议).md"):
                    file_path = os.path.join(root, filename)
                    GlobalFunction.log("运行状态", f"发现提示词文件: {file_path}")
                    # 读取文件内容
                    content = GlobalFunction.load_file(file_path)
                    # 提取所有{{xxx}}格式的占位符（包含空格、不换行），支持中文和多层级xx.yy.zz(path = 'c:/test/xxx.pdf')格式
                    pattern = re.compile(r'\{\{([^\n\r]+?.*?)\}\}')
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
                    GlobalFunction.log("运行状态",
                                       f"Prompts 添加提示词索引条目: \n{json.dumps(item, ensure_ascii=False, indent=2)}")
                    index_list.append(item)

        # 构建索引
        GlobalObject.Prompts = PromptIndex(index_list)
        GlobalFunction.log("运行状态", f"系统提示词索引构建完成，共 {len(index_list)} 个提示词")

    def set_quit_event(self, event):
        self.quit_event = event

    def create_task(self, json: dict):
        """创建一个任务，加入到激活的任务队列"""
        work_task = WorkTask(json, self)
        self.active_task_list.append(work_task)
        return work_task

    def list_active_tasks(self):
        """列出当前激活的任务"""
        return "\n".join([task.to_markdown() for task in self.active_tasks_dict.values()])

    # 加载历史聊天记录
    def load_memory(self):
        """加载记忆信息"""
        print(f"1. 加载历史聊天记录")
        self.load_chat_history()
        print(f"2. 加载领域模型")


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
        n_bytes = self._config["chat"]["reload_chat_max_bytes"]
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
            # split 会产生一个空字符串在开头（如果文本以标记开头），过滤掉
            chat_message_list = GlobalFunction.parse_chat_text_to_messages(text)
            # 用 deque 保留最后 maxlen 个条目，如果超过则自动丢弃最早的
            self.chat_records.extend(chat_message_list)
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

    def record_role_chat(self, role: str, text: str):
        super().record_role_chat(role, text)
        try:
            for q in list(self._response_queues.values()):
                try:
                    q.put_nowait({"role": role, "content": text})
                except queue.Full:
                    pass
        except Exception:
            pass  # FIXME: gateway 广播失败 — 记录认知盲区


# 全局解决器
g_owner = SolverOwner(g_yaml_config)
