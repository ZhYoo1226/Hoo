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
from datetime import datetime
from typing import List, Dict

from lunar_python import Solar

from common import ChatMessage, GlobalFunction
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
    def __init__(self, config: Dict = None):
        # 全局配置文件
        self._config = config or {}
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

    def list_tools(self):
        """获取所有注册的工具"""
        if not self.tools:
            self.tools = g_tool_registry.list_tools()
            self.tools.extend(self._list_state_tools())
        return self.tools

    def list_meta_tools(self):
        """获取所有状态元工具"""
        tools = self.list_tools()
        meta_tools = [tool for tool in tools if tool['action'].startswith('state:Meta')]
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
                        if 'action' in init_desc:
                            del init_desc["action"]
                        if 'description' in init_desc:
                            del init_desc["description"]
                        tool.update(init_desc)
                    tool_list.append(tool)
        return tool_list

    #######################日志相关##################################
    def record_execute_prompt(self, prompt_json: dict):
        """记录执行的提示词思考"""
        self.sys_breathe_log(f"<行动>:开始思考{prompt_json['名称']}")

    # 系统呼吸日志
    def sys_breathe_log(self, msg: str) -> logging.Logger:
        """写入日志文件，带时间戳"""
        return self.log(f"sys_breathe_{self.current_date()}", msg)

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

        # 1.向聊天历史记录中增加消息
        file_path = GlobalFunction.chat_hist_store_file()
        dir_name = os.path.dirname(file_path)
        if dir_name:
            os.makedirs(dir_name, exist_ok=True)

        with open(file_path, 'a', encoding='utf-8') as f:
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

    def log(self, name: str, msg: str) -> logging.Logger:
        """初始化系统日志记录器"""
        logger = logging.getLogger(name)
        logger.setLevel(logging.INFO)

        # 避免重复添加handler
        if not logger.handlers:
            # 创建按日期命名的日志文件
            log_file = os.path.join(self._log_path, f"{name}.log")
            os.makedirs(os.path.dirname(log_file), exist_ok=True)

            # 创建文件handler
            file_handler = logging.FileHandler(log_file, encoding='utf-8')
            # 创建控制台handler
            console_handler = logging.StreamHandler()
            # 设置日志格式
            formatter = logging.Formatter('[%(asctime)s]: %(message)s', datefmt='%Y-%m-%d %H:%M:%S')
            file_handler.setFormatter(formatter)
            console_handler.setFormatter(formatter)
            # 添加handler到logger
            logger.addHandler(file_handler)
            # logger.addHandler(console_handler)
        logger.info(msg)
        return logger

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
            props = props[6:]
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
