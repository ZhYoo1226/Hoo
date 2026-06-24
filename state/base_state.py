import asyncio
import importlib
import inspect
import json
import logging
import os
import pkgutil
import queue
import time
import uuid
from collections import deque
from datetime import datetime
from typing import List, Dict

from engram import Engram
from lunar_python import Solar

from common import ChatMessage, GlobalFunction, GlobalObject
from common.register import g_tool_registry


def _read_target_property(current: object, props: str) -> object:
    """
    递归读取属性值，支持 YAML 节点、Pandas DataFrame/Series 等。
    read_meta_predict('元认知分类') 支持参数传递
    """
    if not props:
        return None

    keys = props.split('.')

    # keys为空时，返回None
    if not keys:
        return None

    for key in keys:
        # 检查是否包含方法参数
        args = []
        kwargs = {}
        if '(' in key and key.endswith(')'):
            method_name = key.split('(')[0]
            params_str = key[len(method_name) + 1:-1]
            params = {}
            if params_str:
                for param in params_str.split(','):
                    if '=' in param:  # Named parameter
                        k, v = param.split('=', 1)
                        params[k.strip()] = v.strip().strip('"\'')
                    else:  # Positional parameter
                        params[len(params)] = param.strip().strip('"\'')
                # 初始化函数的方法参数
                for k, v in params.items():
                    if isinstance(k, int):  # Positional arg
                        args.append(v)
                    else:  # Named arg
                        kwargs[k] = v
            key = method_name

        # 处理 YAML 节点（ruamel.yaml 或 pyyaml 的 dict-like 对象）
        if hasattr(current, key):
            # 当前对象有这个属性，获取属性
            current = getattr(current, key)
            # 如果是可调用方法，则调用获取返回值
            if callable(current):
                current = current(*args, **kwargs)
        elif isinstance(current, dict) and key in current:
            # 当前是字典且包含键，直接取值
            current = current[key]
        elif hasattr(current, '__getitem__') and key in current:
            current = current[key]
        # 处理 Pandas DataFrame/Series
        elif hasattr(current, 'loc'):
            # 先尝试按列名获取 DataFrame 列
            current = current[key]
        else:
            # 找不到对应键/属性，返回None
            return None
    return current


# FSM
class BaseState:
    def __init__(self, config: Dict = None, gesture: Dict = None, **kwargs):
        self._duration = 60 * 1000  # 默认持续时间（毫秒）1分钟
        self._start_time = int(datetime.now().timestamp() * 1000)  # 当前时间戳（毫秒）
        self._uuid = str(uuid.uuid4())
        self._finished = False

    @property
    def finished(self) -> bool:
        return self._finished

    def read_property(self, props: str):
        """
        从当前状态读取属性值，支持级联属性和字典键查找
        :param props: 属性路径，使用.分隔级联，如 'state.user.name'
        :return: 找到的值，找不到返回None
        """
        # 判断props是否以state.开头，是，则去掉state.
        if props.startswith('state.'):
            props = props[6:]

        return _read_target_property(self, props)

    def Exit(self, entity):
        """状态退出时的回调函数"""
        # 默认实现（可以被子类重写）
        self._finished = True

    async def Execute(self, owner):
        '''
        默认执行状态
        '''
        pass

    def Enter(self, owner):
        '''
        默认执行状态
        '''
        pass

    def timeA2B(self, b: 'BaseState') -> int:
        """
        计算两个状态之间的时间差（毫秒）
        """
        return abs(b._start_time - self._start_time)

    def testOverTime(self, entity: 'StateOwner', s) -> bool:
        """
        判断是否超时，超时则调用 entity.delState(s)
        """
        current_time = int(datetime.now().timestamp() * 1000)
        if current_time - s._start_time >= s._duration:
            print(f"state[{s.stateName}] over time, start={s._start_time},cur={current_time}, duration={s._duration}")
            entity.remove_state(s)
            return True
        return False


class StateOwner:
    # 状态类的注册表
    _states_class = {}
    # 意图理解模型
    llm_intent_model = None
    # 提示词执行执行模型
    llm_prompt_model = None
    # 执行模型
    llm_action_model = None

    def __init__(self, config):
        # 全局配置文件
        self._config = config
        # 聊天历史最近的数据，最多最近的10条
        self.max_chat_record_in_mem = self._config["chat"]["max_chat_record_in_mem"]
        # deque,只保留默认的前20条对话记录
        GlobalFunction.log("运行日志", f"初始化StateOwner")
        #maxlen=self.max_chat_record_in_mem
        self.chat_records = deque()

        self._states: List['BaseState'] = []
        # 全局状态
        self._g_state = None
        self._remove_states: List['BaseState'] = []
        self.event = queue.Queue()

        self.FPS = 0
        self.total_time = 0
        self.breathe_frame = 0
        self.frame_start_time = time.perf_counter()
        self.tools = []
        # 一般不需要外部使用
        print(f"记忆路径:{GlobalFunction.memory_path()}")
        self._memories = None
        GlobalFunction.init_paths()

        self.workspace_path = GlobalFunction.workspace_path()

        # 1.向聊天历史记录中增加消息
        self.chat_hist_file_path = GlobalFunction.chat_hist_store_file()


    def execute_prompt(self,
                       user_prompt: str,  # user提示词
                       state: BaseState = None,  # 当前状态
                       system_prompt: str = "系统提示词",  # 系统提示词
                       conversation: list = None,  # 基于的对话内容
                       user_msg: str = None,  # 用户消息, user_prompt二选一
                       images: list[str] = None,  # VL 模型的图片路径列表
                       ) -> List[
        dict]:
        """执行提示词，返回response执行Action"""
        # prompt_file=规划任务.md
        assert self.llm_prompt_model, "提示词执行模型未设置，请设置owner.llm_prompt_model的模型。"
        self.llm_prompt_model.reset_tokens()
        
        # 转换成llm对话消息格式 
        # 返回的message
        '''
        [
            {"role": "system", "content": "系统提示词..."},
            {"role": "用户", "content": "..."},   ← 历史聊天
            {"role": "助手", "content": "..."},   ← 历史聊天
            {"role": "user", "content": "### 生成审查清单\n\n**知识树:**\n[...]\n**方案类型:** 桥梁施工方案"}
        ]
        '''
        messages = self._to_llm_conversation(owner=self, state=state, system_prompt=system_prompt,
                                             conversation=conversation, user_prompt=user_prompt)
        # 额外的用户消息
        if user_msg:
            messages.append({"role": "user", "content": user_msg})

        # apikey--webui替换
        user_key = getattr(self,"_user_api_key","")
        if user_key:
            self.llm_prompt_model.client.api_key = user_key

        # 请求模型 发送给模型
        llm_output = self.llm_prompt_model.query(messages=messages, images=images)  # prompt model
        # 日志方面
        query_log = self.llm_prompt_model.get_query_log()
        self.llm_prompt_model.reset_query_log()
        GlobalFunction.log("模型日志", f"{json.dumps(query_log, ensure_ascii=False, indent=2)}")
        # 拿到返回
        if llm_output:
            # 拿到action块
            '''
            [
                {
                    "action":"state:StoreChecklist", 
                    "params":{...}, 
                    "thought":"..."
                }
            ]
            '''
            json_list = GlobalFunction.parse_llm_output_json(llm_output)
            return json_list
        else:
            return []

    def load_memories(self):
        """加载记忆"""
        self._memories = {}
        memory_path = GlobalFunction.memory_path()
        '''遍历目录下的所有记忆文件，后缀为.memory'''
        memory_list = []
        for file in os.listdir(memory_path):
            if file.endswith(".memory"):
                '''获得文件basename'''
                memory_name = os.path.splitext(file)[0]
                self._memories[memory_name] = Engram(path=os.path.join(f"{memory_path}/{file}"), embedder_model=GlobalFunction.embedding_model())

    def list_memory(self) -> List[str]:
        """列出所有记忆"""
        if self._memories is None:
            self.load_memories()
        return list(self._memories.keys())

    def memory(self, memory_name: str) -> Engram:
        """获取记忆，如果不存在，则创建一个新的记忆"""
        if self._memories is None:
            self.load_memories()

        if memory_name not in self._memories:
            self._memories[memory_name] = Engram(path=os.path.join(GlobalFunction.memory_path(), f"{memory_name}.memory"), embedder_model=GlobalFunction.embedding_model())
        return self._memories[memory_name]

    def current_time(self):
        """
        获取当前时间
        :return: 当前时间，格式为HH:mm:ss
        """
        return datetime.now().strftime("%H:%M:%S")

    def current_date(self):
        """
        获取当前日期
        :return: 当前日期，格式为YYYY-MM-DD
        """
        return datetime.now().strftime("%Y-%m-%d")

    def current_weekday(self):
        """
        获取当前星期几的名称
        :return: 当前星期几的名称，如 '星期一'
        """
        return datetime.now().strftime("%A")

    def current_solar(self):
        """
        获取当前公历信息
        :return: 当前公历信息
        """
        return Solar.fromDate(datetime.today()).toFullString()

    def recall_memory(self,
                      memory_name: str,  # 记忆名称
                      memory_desc: str,  # 记忆信息描述
                      k: int = 3,  # 召回数量
                      mode: str = "hybrid",  # 查询模式,hybrid:结合向量语义和关键词检索;spreading:关联激活扩散检索;cosine:纯向量检索，擅长语义联想
                      ):
        """根据任务描述，查找最匹配的行为模型"""
        GlobalFunction.log("运行日志", f"搜索记忆[{memory_name}]，描述：\n{memory_desc}")
        # 使用 hybrid 模式，结合语义和关键词检索
        results = self.memory(memory_name).recall(memory_desc, k=k, mode=mode)
        if not results:
            GlobalFunction.log("运行日志", f"记忆[{memory_name}]没有找到匹配的行为模型。")
            return None

        for r in results:
            # r.episode.content 是匹配到的检索内容
            GlobalFunction.log("运行日志", f"记忆[{memory_name}] [分数: {r.score:.3f}] {r.episode.content}\n-------------------------------------")

        return results

    # 农历日期
    def current_lunar(self):
        """
        获取当前农历信息
        :return: 当前农历信息
        """
        # 获取系统当天的公历日期对象
        today_solar = Solar.fromDate(datetime.today())
        # 转换为农历并显示详细信息
        today_lunar = today_solar.getLunar()
        return today_lunar.toFullString()

    def list_tools(self,
                   category: str = None  # 按分类返回, None表示所有工具
                   ):
        """获取所有注册的工具"""
        if not self.tools:
            self.tools = g_tool_registry.list_tools()
            self.tools.extend(self._list_state_tools())
        if category:
            # 按分类返回工具
            tool_list = [tool for tool in self.tools if tool['action'].startswith(category)]
            return tool_list if tool_list else ["Not Provided"]
        else:
            # 返回全部工具
            return self.tools

    def list_meta_tools(self):
        """获取所有状态元工具"""
        tools = self.list_tools()
        meta_tools = [tool for tool in tools if
                      tool['action'].startswith('state:Meta') or tool['action'].startswith('meta:')]
        return meta_tools

    def list_file_tools(self):
        """获取所有文件工具"""
        tools = self.list_tools()
        file_tools = [tool for tool in tools if tool['action'].startswith('state:File')]
        return file_tools

    def list_web_tools(self):
        """获取所有网络工具"""
        tools = self.list_tools()
        web_tools = [tool for tool in tools if tool['action'].startswith('state:Web')]
        return web_tools

    def _list_state_tools(self):
        tool_list = []
        for _, module_name, _ in pkgutil.iter_modules(['state']):
            if module_name == '__init__' or module_name == 'base_state':
                continue
            module = importlib.import_module(f'.{module_name}', package='state')
            # 遍历模块中的所有类，找到匹配stateName的状态类
            for attr_name in dir(module):
                state_class = getattr(module, attr_name)

                if isinstance(state_class, type) and issubclass(state_class, BaseState) and state_class != BaseState:
                    # 优先匹配stateName属性，如果没有则匹配类名
                    tool = {
                        "action": f"state:{state_class.__name__}",
                        "description": state_class.__doc__.strip() if state_class.__doc__ else ""
                    }
                    # 判断actionName是否注册，如果有则使用注册的actionName
                    if hasattr(state_class, 'actionName'):
                        tool['action'] = state_class.actionName
                        GlobalFunction.log("运行日志", f"状态类{state_class.__name__}注册为{state_class.actionName}")
                    else:
                        # 没有显性声明ActionName，不能注册状态类
                        GlobalFunction.log("运行日志", f"状态类{state_class.__name__}没有显性声明actionName，不能注册状态类")
                        continue
                    # Get the __init__ method properly
                    init_func = state_class.__init__
                    # Skip if it's the BaseState's __init__ method
                    if init_func.__qualname__ == 'BaseState.__init__':
                        init_desc = {}
                    else:
                        # 解开可能的 classmethod/staticmethod 包装
                        init_desc = GlobalFunction.inspect_function_desc((init_func))
                    if init_desc:
                        if 'parameters' in init_desc:
                            del init_desc['parameters']
                        if 'action' in init_desc:  # 默认是__init__方法名称
                            del init_desc["action"]
                        if 'description' in init_desc:
                            del init_desc["description"]
                        tool.update(init_desc)
                        # 注册状态类
                        StateOwner._states_class[tool['action']] = state_class

                    tool_list.append(tool)
        return tool_list

    #######################日志相关##################################
    def record_execute_prompt(self, prompt_json: dict):
        """记录执行的提示词思考"""
        self.sys_breathe_log(f"<行动>:开始思考{prompt_json['名称']}")

    # 系统呼吸日志
    def sys_breathe_log(self, msg: str) -> logging.Logger:
        """写入日志文件，带时间戳"""
        return GlobalFunction.log(f"sys_breathe_{self.current_date()}", msg)

    def record_execute_action(self):
        pass

    def record_observation(self):
        pass

    def record_action_log(self, action_json):
        """记录Action行动日志"""

        if isinstance(action_json, dict):
            """
            think:简短的思考
            response: 回复用户,其实也是思考
            """
            if "response" in action_json:
                self.record_role_chat("观察", json.dumps(action_json, ensure_ascii=False))
        elif isinstance(action_json, str):
            self.record_role_chat("观察", action_json)

    def record_role_chat(self, role: str, text: str):
        """
            记录角色[系统，用户，助理，观察]的反馈信息
        Args:
            role: 角色，如 "用户"、"助手"、"思考"、"观察" 等
            text: 发言内容
        """
        if not text:
            return False

        dir_name = os.path.dirname(self.chat_hist_file_path)
        if dir_name:
            os.makedirs(dir_name, exist_ok=True)

        with open(self.chat_hist_file_path, 'a', encoding='utf-8') as f:
            # 如果text内容中有josn，会出错！！！！应该直接以文本的形式存储。最后多少行。
            # record = f"[{role}]：{text}\n"
            record = ChatMessage(role, text)
            # 临时记录到内存
            self.chat_records.append(record)
            # 写到每天的工作日志
            f.write(str(record) + "\n")
            # 打印消息给用户看
            record.print()

            # f.write(json.dumps(record, ensure_ascii=False) + '\n')

    #################################状态机基础方法########################################
    def read_property(self,  # 目标对象
                      props: str  # 属性路径，使用.分隔级联，如 'user.name'
                      ):
        """
        从目标对象获取值，支持级联属性和字典键查找
        :param props: 属性路径，使用.分隔级联，如 'user.name'
        :return: 找到的值，找不到返回None
        """

        # 判断props是否以owner.开头，是，则去掉owner.
        if props.startswith('owner.'):
            # 首先判断self是否包含有owner属性
            if hasattr(self, 'owner'):
                # 直接读取，这是task_owner的属性
                return _read_target_property(self, props)
            else:
                return _read_target_property(self, props[6:])

        # 有具体的任务，从任务中读取属性
        if props.startswith('task.'):
            return _read_target_property(self, props[5:])

        # 直接读取自己的属性
        return _read_target_property(self, props)

    def destroy(self):
        # 退出所有的状态
        for s in self._states:
            self.remove_state(s)

    # 添加状态方法
    def add_state(self, state):
        # 进入状态
        if hasattr(state, 'Enter'):
            state.Enter(self)
        self._states.append(state)
        self.event.put("add_state")
        return state

    # 删除状态方法
    def remove_state(self, state):
        self._remove_states.append(state)
        # self.event.put("remove_state")
        return state

    # 删除状态方法
    def del_state(self, state):
        self._remove_states.append(state)
        # self.event.put("del_state")
        return state

    # 是否为空状态
    def is_empty_state(self):
        return len(self._states) <= 0

    # 检查是否存在某个 name 的状态
    def has_state(self, name):
        # self.event.put("has_state")
        for state in self._states:
            if getattr(state, 'stateName', None) == name:
                return state
        return None

    # 查找所有 name 的状态
    def find_state(self, name):
        return [state for state in self._states if getattr(state, 'stateName', None) == name]

    # 查找前几个状态
    def pre_state(self, name, step=3):
        start = len(self._states)
        end = start - step
        for n in range(start - 1, end, -1):
            if n >= 0 and getattr(self._states[n], 'stateName', None) == name:
                return self._states[n]
        return None

    # 状态机的呼吸（状态更新、超时检测、状态删除等）
    def breathe(self, t: int = 1):
        # 如果有状态时才呼吸
        while not self.is_empty_state() and t > 0:
            t -= 1
            # 检测状态是否超时
            for state in self._states:
                if hasattr(state, 'testOverTime'):
                    if state.testOverTime(self, state):
                        # 超时了
                        pass
                else:
                    print(f"Error: no testOverTime function in state: {state}")

            # 删除状态
            for rm_state in self._remove_states:
                if isinstance(rm_state, str):
                    # 通过 name 删除
                    for i, state in enumerate(self._states):
                        if getattr(state, 'stateName', None) == rm_state:
                            self._states.remove(state)
                            if hasattr(state, 'Exit'):
                                state.Exit(self)
                                # 额外调用基类方法
                                if isinstance(state, BaseState):
                                    BaseState.Exit(state, self)
                            del self._states[i]
                            break
                else:
                    # 直接是对象
                    if rm_state in self._states:
                        self._states.remove(rm_state)
                        if hasattr(rm_state, 'Exit'):
                            rm_state.Exit(self)
                            # 额外调用基类方法
                            if isinstance(rm_state, BaseState):
                                BaseState.Exit(rm_state, self)
                            del rm_state

            # 清空待删除列表
            self._remove_states = []

            # 执行所有状态的 Execute
            for state in self._states:
                if hasattr(state, 'Execute'):
                    state.Execute(self)
            self.breathe_frame += 1

        # 每次执行都要记录时间
        # self.total_time += (time.perf_counter() - self.frame_start_time)
        # if self.total_time >= 0.04:
        #     self.total_time = 0
        #     self.frame_start_time = time.perf_counter()

        # print("breathe time:",self.breathe_frame)
        try:
            # 每次呼吸的时间间隔是0.1s
            self.event.get(block=True, timeout=0.04)  # 设置超时时间为0.04s保证一帧的时间间隔。
        except queue.Empty:
            # print("Queue is empty, timeout occurred.")
            pass

    async def async_breathe(self, t: int = 1):
        # 如果有状态时才呼吸
        while not self.is_empty_state() and t > 0:
            t -= 1
            # 检测状态是否超时
            for state in self._states:
                if hasattr(state, 'testOverTime'):
                    if state.testOverTime(self, state):
                        # 超时了
                        pass
                else:
                    print(f"Error: no testOverTime function in state: {state}")

            # 删除状态
            for rm_state in self._remove_states:
                if isinstance(rm_state, str):
                    # 通过 name 删除
                    for i, state in enumerate(self._states):
                        if getattr(state, 'stateName', None) == rm_state:
                            self._states.remove(state)
                            if hasattr(state, 'Exit'):
                                state.Exit(self)
                                # 额外调用基类方法
                                if isinstance(state, BaseState):
                                    BaseState.Exit(state, self)
                            del self._states[i]
                            break
                else:
                    # 直接是对象
                    if rm_state in self._states:
                        self._states.remove(rm_state)
                        if hasattr(rm_state, 'Exit'):
                            rm_state.Exit(self)
                            # 额外调用基类方法
                            if isinstance(rm_state, BaseState):
                                BaseState.Exit(rm_state, self)
                            del rm_state

            # 清空待删除列表
            self._remove_states = []

            # 收集所有 Execute 执行任务
            tasks = []
            for state in self._states:
                if hasattr(state, 'Execute'):
                    func = state.Execute
                    # 判断是否为 async def
                    if inspect.ismethod(func):
                        raw = func.__func__
                    else:
                        raw = func

                    if inspect.iscoroutinefunction(raw):
                        # 异步方法：创建任务
                        tasks.append(func(self))
                    else:
                        # 同步方法：直接在事件循环里用 run_in_executor 避免阻塞
                        loop = asyncio.get_running_loop()
                        tasks.append(loop.run_in_executor(None, func, self))
                        # 或者如果确认同步执行很快且不会阻塞，也可以直接调用，但不 await

            if tasks:
                await asyncio.gather(*tasks)

            self.breathe_frame += 1
        # 每次呼吸的时间间隔是0.1s
        await asyncio.sleep(0.04)

    def _find_state(self, state_name: str) -> BaseState:
        """查找状态类"""
        if state_name in StateOwner._states_class:
            return StateOwner._states_class[state_name]

        # 缓存未命中，才进行动态查找
        # 遍历state模块下所有py文件，查找对应名称的状态类
        state_class = None
        for _, module_name, _ in pkgutil.iter_modules(['state']):
            if module_name == '__init__' or module_name == 'base_state':
                continue
            module = importlib.import_module(f'.{module_name}', package='state')
            # 遍历模块中的所有类，找到匹配stateName的状态类
            for attr_name in dir(module):
                attr = getattr(module, attr_name)
                if isinstance(attr, type) and issubclass(attr, BaseState) and attr != BaseState:
                    # 优先匹配stateName属性，如果没有则匹配类名
                    match_found = False
                    if hasattr(attr, 'stateName') and attr.stateName == state_name:
                        match_found = True
                    elif attr.__name__ == state_name:
                        match_found = True
                    elif hasattr(attr, 'actionName') and attr.actionName == state_name:
                        match_found = True

                    if match_found:
                        state_class = attr
                        # 存入缓存，下次直接使用
                        StateOwner._states_class[state_name] = state_class
                        return state_class
        return None

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

    def get_chat_history_text(self,
                              limit: int = 0,  # 最多返回最近limit条记录
                              roles: list = None  # 角色列表，None表示所有角色
                              ) -> str:
        """获取历史聊天文本(纯文本),
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

    def _to_llm_conversation(self, owner, state, system_prompt, conversation, user_prompt):
        """转换成llm的对话格式"""
        # 必须加入的系统提示词
        messages = []
        if system_prompt:
            system_text, system_json = GlobalObject.Prompts.load_prompt(system_prompt, owner=owner, state=state)
            messages.append({"role": "system", "content": system_text})
        # 限制对话轮数
        if not conversation:
            # 用户历史聊天获取，再从状态获取历史聊天记录
            # 加载聊天记录,如果state有get_chat_conversation方法,否则使用self的get_chat_conversation方法
            _conversation_func = owner.get_chat_conversation
            if state and hasattr(state, "get_chat_conversation"):
                _conversation_func = getattr(state, "get_chat_conversation")
            # 历史聊天
            conversation = _conversation_func(limit=-1, roles=["用户", "思考", "助手", "行动", "观察", "系统"])
        messages.extend(conversation)
        # 最后增加对话提示词
        if user_prompt:
            prompt_text, prompt_json = GlobalObject.Prompts.load_prompt(user_prompt, owner=owner, state=state)
            if prompt_text:
                # 有提示词
                owner.record_execute_prompt(prompt_json)
                messages.append({"role": "user", "content": prompt_text})
            else:
                messages.append({"role": "user", "content": user_prompt})

        return messages

    def execute_chat(self, owner, state=None, system_prompt: str = "系统提示词"):
        """
        同步执行聊天，返回response执行Action
        """
        assert owner.llm_prompt_model, "提示词执行模型未设置，请设置owner.llm_prompt_model的模型。"
        owner.llm_prompt_model.reset_tokens()

        # 加载聊天记录,如果state有get_chat_conversation方法,否则使用self的get_chat_conversation方法
        _conversation_func = owner.get_chat_conversation
        if state and hasattr(state, "get_chat_conversation"):
            _conversation_func = getattr(state, "get_chat_conversation")
        # 历史聊天
        conversation = _conversation_func(limit=-1, roles=["用户", "助手", "观察"])
        # 转换成llm对话消息格式
        messages = owner._to_llm_conversation(self, state, system_prompt, conversation, user_prompt=None)
        
        user_key = getattr(owner, "_user_api_key", "")
        if user_key:
            owner.llm_prompt_model.client.api_key = user_key

        llm_output = owner.llm_prompt_model.query(messages=messages)  # prompt model
        # 直接聊天，不会输出action指令
        # 解析思考
        query_log = owner.llm_prompt_model.get_query_log()
        owner.llm_prompt_model.reset_query_log()
        GlobalFunction.log("模型日志", f"{json.dumps(query_log, ensure_ascii=False, indent=2)}")
        think_text = GlobalFunction.parse_llm_think(llm_output)
        if think_text:
            owner.record_role_chat("思考", think_text)

        action_json = GlobalFunction.extract_action_json(llm_output)
        if action_json:
            # 有可能返回的是action
            owner.execute_action(action_json)
        else:
            # 没有action，直接输出llm_output
            reply_text = GlobalFunction.parse_llm_reply(llm_output)
            owner.record_role_chat("助手", reply_text)

    def execute_action(self, action_json):
        """执行Action
            如果是state:XXXXState,则查找状态，创建状态，并添加add_state到owner。
        """
        # 判断action_json 是dict还是list
        if not action_json:
            self.sys_breathe_log(f"llm 输出中没有 action: {action_json}")
            return False

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
        if "action" not in action:
            self.sys_breathe_log(f"llm 输出中没有 action: {action_json}")
            return False
        action_name = action["action"]
        # 判断是否是 state 操作，格式为 state:xxxState 或者 actionName
        # FIXME 后续的可能情况，不需要用register注册，直接用actionName即可
        state_name = ""
        if action_name.startswith("state:"):
            # 提取 state 名称
            state_name = action_name.split(":", 1)[1]

        if action_name in StateOwner._states_class:
            state_name = action_name

        # 先从缓存中查找
        if state_name in StateOwner._states_class:
            state_class = StateOwner._states_class[state_name]
            state = state_class(**action)
            # 这个新增的状态，隶属于某个具体的任务。如何绑定任务？
            self.add_state(state)
            return True
        else:
            try:
                # 普通工具调用
                return g_tool_registry.execute(tool_name=action_name, owner=self, **action)
            except Exception as e:
                self.sys_breathe_log(f"执行工具{action_name}失败: {e}")
                return False
