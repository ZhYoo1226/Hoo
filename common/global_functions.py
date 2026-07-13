import ast
import pandas as pd
# 检查活的对象
import inspect
# 当action/fuction.py里注册一个工具函数，框架用inspect自动提取参数名、类型和注释描述
# 生成tool schema暴露给LLM

import io
import json
import logging
import os
import re
import textwrap
import tokenize
from datetime import datetime
from typing import List, Union
from .config import g_yaml_config
from .entities import WorkFile,PromptIndex, ChatMessage

"""存在的意义就是统一全局的变量名，哪些用了，哪些没用"""

GlobalNames = {
    "元认知分类": "元认知分类",
    "领域模型": "领域模型",
    "规划任务下一步动作": "规划任务下一步动作",
    "任务提示词": "任务提示词",
}

class GlobalObject:
    """
    全局对象，用于存储全局变量
    全局变量以首字母大写
    下划线开头，不对外。
    """
    _WorkFileMap: dict = {}  # uuid → WorkFile
    Prompts: PromptIndex = None

class GlobalFunction:

    @staticmethod
    def parse_chat_text_to_messages(md_chat_text: str) -> List[ChatMessage]:
        # 按行首的 "[xxx]: " 切分，保留分隔符本身
        # (?=...) 是正向预查，匹配位置但不消费字符，从而保留标记在结果中
        pattern = re.compile(r'(?=^\[[^\]]+]: )', re.MULTILINE)
        parts = pattern.split(md_chat_text)
        # split 会产生一个空字符串在开头（如果文本以标记开头），过滤掉
        # 判断role是否为None，删除None
        chat_messsages = [m for m in [ChatMessage(data=p.strip().rstrip('\n')) for p in parts if p] if m.role is not None]
        return chat_messsages

    @staticmethod
    def register_work_files(df: "pd.DataFrame"):
        """从 files_abstract.parquet 的 DataFrame 注册 WorkFile 对象"""
        from .entities import WorkFile
        # 需要清空之前的注册吗？
        GlobalObject._WorkFileMap.clear()
        for _, row in df.iterrows():
            wf = WorkFile(row.to_dict())
            GlobalObject._WorkFileMap[wf.uuid] = wf

    @staticmethod
    def show_file_structure():
        """
        显示文件的目录结构
        """
        workspace = "./workspace"
        file_path = "./workspace/user/files/test/test.xlsx"
        base_path = "./workspace/user/files/test"
        log_text = ["\n"]
        log_text.append(f"workspace: {workspace}")
        log_text.append(f"file_path: {file_path}")
        log_text.append(f"base_path: {base_path}")
        log_text.append(f"|--{os.path.basename(file_path)}")
        log_text.append(f"|--{GlobalFunction.file_to_parse_path(file_path)[len(base_path) + 1:]}")
        log_text.append(f"|----{GlobalFunction.file_to_full_text_path(file_path)[len(base_path) + 1:]}")
        log_text.append(f"|----{GlobalFunction.file_to_preview_text_path(file_path)[len(base_path) + 1:]}")
        log_text.append(f"|----{GlobalFunction.file_to_doc_text_path(file_path)[len(base_path) + 1:]}")
        log_text.append(f"|----{GlobalFunction.file_to_doc_table_path(file_path)[len(base_path) + 1:]}")
        log_text.append(f"|----{GlobalFunction.file_to_doc_image_path(file_path)[len(base_path) + 1:]}")
        log_text.append(f"|----{GlobalFunction.memory_path()}")
        return "\n".join(log_text)

    @staticmethod
    def init_paths():
        '''初始化系统所有的文件目录'''
        for p in g_yaml_config["app"]["paths"].values():
            p = os.path.abspath(os.path.join(GlobalFunction.workspace_path(), p))
            if p and not os.path.exists(p):
                os.makedirs(p, exist_ok=True)  # 递归创建，父目录不存在也会创建

    ################目录创建##################
    @staticmethod
    def workspace_path()->str:
        return g_yaml_config["app"]["workspace_path"]

    @staticmethod
    def task_path():
        """任务路径目录"""
        return os.path.abspath(os.path.join(GlobalFunction.workspace_path(), g_yaml_config["app"]["paths"]["task_path"]))

    @staticmethod
    def chat_path():
        """聊天记录存储路径目录"""
        return os.path.abspath(os.path.join(GlobalFunction.workspace_path(), g_yaml_config["app"]["paths"]["chat_path"]))

    @staticmethod
    def domain_model_path():
        """重要：用户的工作或生活的行为模型，由AI总结生成。"""
        return os.path.abspath(os.path.join(GlobalFunction.workspace_path(), g_yaml_config["app"]["paths"]["domain_model_path"]))

    @staticmethod
    def log_path():
        """日志存储路径目录"""
        return os.path.abspath(os.path.join(GlobalFunction.workspace_path(), g_yaml_config["app"]["paths"]["log_path"]))

    @staticmethod
    def prompt_path():
        """提示词存储路径目录"""
        return os.path.abspath(os.path.join(GlobalFunction.workspace_path(), g_yaml_config["app"]["paths"]["prompt_path"]))

    @staticmethod
    def memory_path():
        """重要：记忆存储路径，不可删除。需要备份。"""
        return os.path.abspath(os.path.join(GlobalFunction.workspace_path(), g_yaml_config["app"]["paths"]["memory_path"]))

    @staticmethod
    def knowledge_path(name: str) -> str:
        """知识树产物目录：workspace/knowledge/{name}/"""
        return os.path.join(GlobalFunction.workspace_path(), "knowledge", name)

    @staticmethod
    def review_path(plan_type: str) -> str:
        """审查产物目录：workspace/review/{plan_type}/"""
        return os.path.join(GlobalFunction.workspace_path(), "review", plan_type)

    ################文件##################

    @classmethod
    def files_abstract_path(cls):
        return os.path.join(GlobalFunction.workspace_path(), "user", "files", "files_abstract.parquet")

    @staticmethod
    def user_profile_md():
        """重要：记忆存储路径，不可删除。需要备份。"""
        return os.path.abspath(os.path.join(GlobalFunction.workspace_path(), g_yaml_config["app"]["files"]["user_profile_path"]))

    @staticmethod
    def agent_profile_md():
        """重要：智能体的SOUL.md文件路径"""
        return os.path.abspath(os.path.join(GlobalFunction.workspace_path(), g_yaml_config["app"]["files"]["agent_profile_path"]))

    ########################################################
    @staticmethod
    def file_to_dir_name(file_path: str):
        """
        获取文件名字,后缀符号[点]替换为下划线.
        path= ./workspace/user/files/test/test.xlsx
        return= test_xlsx
        """
        return os.path.basename(file_path).replace(".", "_")

    @staticmethod
    def file_to_name_without_suffix(file_path: str):
        """获取文件名字,不包含后缀
        path= ./workspace/user/files/test/test.xlsx
        return= test
        """
        return os.path.basename(file_path).split(".")[0]

    @staticmethod
    def uuid_to_file_path(file_uuid: str):
        """根据文件uuid，返回文件路径"""
        wf = GlobalFunction.uuid_to_work_file(file_uuid)
        return wf.file_path if wf else None

    @staticmethod
    def uuid_to_work_file(uuid: str,  # 文件uuid
                          ) -> "WorkFile":
        """根据文件uuid，返回 WorkFile 对象"""
        return GlobalObject._WorkFileMap.get(uuid)

    @staticmethod
    def file_to_work_file(file_path: str,  # 文件路径
                          ) -> "WorkFile":
        """根据文件路径，返回 WorkFile 对象"""
        for wf in GlobalObject._WorkFileMap.values():
            if wf.file_path == file_path:
                return wf
        return None

    @staticmethod
    def uuid_to_file_abstract(uuid: str):
        """根据文件uuid，返回文件摘要"""
        # FIXME mim，通过文件uuid，转换为文件摘要信息dict对象。
        # 假设是这个对象
        return {
            "uuid": uuid,
            "文件路径": os.path.join("./workspace/user/files/test", uuid),
            "文件名称": uuid,
            "hash值": "",
            "更新时间": "",
            "文件预览": "",
            "文件摘要": "",
            "关键实体": "",
        }

    @staticmethod
    def file_to_parse_path(file_path: str):
        """返回文件的解析路径
        解析路径为文件所在目录下的解析目录,解析目录的名称为文件名_解析
        path= ./workspace/user/files/test/test.xlsx
        return= ./workspace/user/files/test/test_xlsx_解析
        """
        return os.path.join(os.path.dirname(file_path), f"{GlobalFunction.file_to_dir_name(file_path)}_解析")

    @staticmethod
    def file_to_full_text_path(file_path: str):
        """txt文本内容文件路径
        path= ./workspace/user/files/test/test.xlsx
        return= ./workspace/user/files/test/test_xlsx_解析/test_全文.md
        """
        return os.path.join(
            GlobalFunction.file_to_parse_path(file_path),
            f"{GlobalFunction.file_to_name_without_suffix(file_path)}_全文.md"
        )

    @staticmethod
    def file_to_preview_text_path(file_path: str):
        """txt文本内容文件路径预览"""
        return os.path.join(
            GlobalFunction.file_to_parse_path(file_path),
            f"{GlobalFunction.file_to_name_without_suffix(file_path)}_预览.md"
        )

    @staticmethod
    def file_to_doc_text_path(file_path: str):
        """文档解析的文本目录
        path= ./workspace/user/files/test/test.xlsx
        return= ./workspace/user/files/test/test_xlsx_解析/文本
        """
        return os.path.join(
            GlobalFunction.file_to_parse_path(file_path),
            "文本"
        )

    @staticmethod
    def file_to_doc_table_path(file_path: str):
        """文档解析的表格目录
        path= ./workspace/user/files/test/test.xlsx
        return= ./workspace/user/files/test/test_xlsx_解析/表格
        """
        return os.path.join(
            GlobalFunction.file_to_parse_path(file_path),
            "表格"
        )

    @staticmethod
    def file_to_doc_image_path(file_path: str):
        """文档解析的图片目录
        path= ./workspace/user/files/test/test.xlsx
        return= ./workspace/user/files/test/test_xlsx_解析/图片
        """
        return os.path.join(
            GlobalFunction.file_to_parse_path(file_path),
            "图片"
        )

    @staticmethod
    def task_id_to_executor_path(task_id: str):
        """任务id转换为任务执行文件路径"""
        return os.path.join(GlobalFunction.task_path(), f"任务_{task_id}/任务执行.md")

    @staticmethod
    def chat_hist_store_file() -> str:
        """生成默认文件路径：chat_年月日.md"""
        date_str = datetime.now().strftime("%Y%m%d")
        return os.path.join(GlobalFunction.chat_path(), f"chat_{date_str}.md")

    '''
        LLM 原始输出:
        ┌────────────────────────────────────────────
        │ <think>我需要先理解用户的需求...</think>         ← parse_llm_think 拿这部分
        │                                             
        │ 好的，我理解您的需求。我来帮您处理。              ← parse_llm_reply 拿这部分
        │                                            
        │ ```action                                  
        │ {"action": "meta:task", ...}                   <- extract_action_json 拿这里
        │ ```                                        
        └────────────────────────────────────────────

    '''

    # 针对推理模型的 --> DeepSeek-R1
    # 如果没用<think>xxx</think>，则直接返回原文
    @staticmethod
    def parse_llm_think(response: str):
        """提取think标签之间的内容"""
        think_match = re.search(r'<think>(.*?)</think>', response, re.DOTALL)
        if think_match:
            # 
            think_text = think_match.group(1) if think_match else ""
            return think_text
        else:
            return None

    @staticmethod
    def parse_llm_reply(response: str):
        """解析LLM输出文本中的reply内容:提取</think>之后的内容。否则返回原文"""
        if "</think>" in response:
            # 第三个参数让.能匹配换行符，否则换行截停
            # f''-- f-string，花括号内可以插入变量
            # r'' -- raw string，避免转义\，不然\-->\\
            '''
                作为一个匹配单元-->套括号
                只作为标记，后续不用，则是非捕获(?:)
                需要正则匹配并取出来的，则是捕获,()
                --------------------------------
                量词 * ? +
                --------------------------------
                字符开头^
                字符结尾$
                --------------------------------
                .匹配任意字符
                贪婪和懒惰，a123b456b，匹配到第一个b懒惰，匹配到第2个b贪婪
                .* 贪婪捕获 从开头匹配到尽量多的匹配结尾
                .*? 懒惰捕获 从开头匹配到尽量少的匹配结尾，
            '''
            reply_match = re.search(r'(?:.*?</think>)?(.*)', response, re.DOTALL)
            if reply_match:
                # 
                reply_text = reply_match.group(1) if reply_match else ""
                return reply_text
            else:
                return response
        else:
            return response

    @staticmethod
    def extract_action_json(response: str):
        """
        解析LLM输出文本中的操作指令 '''action xxx ''' 变为json形式的dict
        """
        if "```action" in response:
            # 切了之后，被切的string会消失
            predict_action = response.split("```action")[1]
            if predict_action:
                predict_action = predict_action.split("```")[0]
                # 从str-->json，从str变成dict
                predict_json = json.loads(predict_action.strip())
                return predict_json
        return None

    @staticmethod
    def extract_data_block(response: str, block_name: str = "action") -> List[dict]:
        """
        提取 response 中的所有最外层代码块。

        - 若块名等于 block_name，则将其内容尝试解析为 JSON 存入返回的"parsed" 列表。
        - 若块名不等于 block_name，则将其内容原样（字符串）构建{"outer_name": content}存入返回 "parsed" 列表。
        - 嵌套在其他块内部的代码块不会被单独提取，而是作为外层块内容的一部分保留。

        参数：
            response (str): 原始文本。
            block_name (str): 需要解析为 JSON 的代码块名称，默认为 "action"。

        返回：
            Dict[str, List[Any]]: 包含两个键：
                - "parsed": list，解析成功的 JSON 对象（如果解析失败，也会以字符串形式存入 raw）。
        """
        lines = response.splitlines(keepends=False)
        stack = []  # 栈元素: (开始行索引, 块名称)
        parsed_results = []  # 存放解析后的 JSON 对象
        start_pattern = re.compile(r'^```\s*(\S+)')  # 匹配开始标记，捕获块名
        i = 0
        while i < len(lines):
            line = lines[i]
            stripped = line.strip()

            # 1. 检查是否为开始标记
            if stripped.startswith("```") and len(stripped) > 3:
                match = start_pattern.match(stripped)
                if match:
                    name = match.group(1)
                    # 若当前栈为空，说明这是一个最外层块的开始
                    if not stack:
                        outer_start = i
                        outer_name = name
                    stack.append((i, name))
                i += 1
                continue

            # 2. 检查是否为结束标记
            if stripped == "```":
                if stack:
                    start_idx, name = stack.pop()
                    # 若弹出后栈为空，说明一个最外层块已完整结束
                    if not stack:
                        # 提取块内容（不包含开始和结束行）
                        content_lines = lines[outer_start + 1:i]
                        content = "\n".join(content_lines).strip()
                        if outer_name == block_name:
                            if content.startswith('{') or content.startswith('['):
                                try:
                                    parsed_results.append(json.loads(content))
                                except json.JSONDecodeError as e:
                                    GlobalFunction.log("JSON解析失败", f"错误: {e}\n内容前200字符: {content[:200]}")
                                    parsed_results.append({outer_name: content})
                            else:
                                parsed_results.append({outer_name: content})
                        else:
                            parsed_results.append({outer_name: content})
                i += 1
                continue
            i += 1
        return parsed_results

    @staticmethod
    def parse_llm_output_json(response: str, block_name: str = "action") -> List[dict]:
        think_text = GlobalFunction.parse_llm_think(response)
        # 抽取action行为数据
        json_list = GlobalFunction.extract_data_block(response, block_name)
        if json_list:
            json_list[0]['think'] = think_text
        return json_list

    @staticmethod
    def parse_markdown_title(text: str, block_level: str = "###") -> List[str]:
        """按读取text文本，提取以block_level开头的行后面的标题"""
        lines = text.splitlines(keepends=False)
        title_list = []
        for line in lines:
            if line and line.strip().startswith(block_level + " "):
                title_list.append(line.strip(block_level + " ").strip())
        return title_list

    @staticmethod
    def load_file(file_path: str):
        """将文件内容加载为string，json格式使用json.load加载"""
        _, ext = os.path.splitext(file_path)
        with open(file_path, "r", encoding='utf-8') as file:
            if ext == ".json":
                return json.load(file)
            else:
                return file.read().strip()

    @staticmethod
    def write_file(file_path, content: str):
        with open(file_path, "w", encoding='utf-8') as file:
            file.write(content)

    # action/function里注册工具
    @staticmethod
    def inspect_function_desc(func_cls):
        """检查函数名称，描述，参数信息"""
        parameters = {}
        sig = inspect.signature(func_cls)  # 拿到的参数名字、类型和默认值
        source = inspect.getsource(func_cls)  # 函数的python源码字符串，完整def+装饰器+最近的注释行
        doc = inspect.getdoc(func_cls) or ""  # 只拿docstring，自动处理缩进
        # dedent去除def整体每行前面的的公共缩进，使代码在顶层合法
        source = textwrap.dedent(source)
        '''
            ast用法：源码字符串解析成语法树
            Module                                    ← 模块根节点
                └─ body[0]: FunctionDef               ← body列表是所有顶层语句，此处只输入了函数定义
                        ├─ name: "meta_read"                   ← 函数名，只能是字符串字面量
                        ├─ decorator_list[0]: Call             ← 装饰器
                        │  └─ func: Attribute                  此处是Attribute的原因是，装饰器使用的是@g_tool_registry.register对象属性
                        │     └─ value: Name(id="g_tool_registry")  value是被访问属性的对象表达式
                        │     └─ attr: "register"                   attr是对象的属性
                        │  └─ args[0]: Constant(value="meta:read")  参数
                        ├─ args                                ← 参数列表
                        │  ├─ arg(file_uuid, annotation=Name(id="str"))
                        │  ├─ arg(owner, annotation=Name(id="StateOwner"))
                        │  ├─ kwarg(arg="kwargs")
                        ├─ body[0]: Expr                       ← 函数体第一内部语句，每一行（docstring）
                        │  └─ value: Constant(value="根据文件uuid，分析文件内容")
                        └─ body[1]: Expr                       ← 函数体第二条
                            └─ value: Call(func=Name(id="print"))
        '''
        tree = ast.parse(source)
        if tree.body:
            func_def = tree.body[0]
            if isinstance(func_def, (ast.FunctionDef, ast.AsyncFunctionDef)):
                # Get all comments from the source code
                '''
                函数这样写--在检查的时候能拿到参数信息comments
                def meta_read(
                            file_uuid: str,  # 文件uuid
                            owner: StateOwner  # 状态所有者
                            ):  
                    """根据uuid分析文件"""      ← 这是 docstring，不是注释
                    print("meta:read")          ← 这是代码

                comments = {
                            "file_uuid": "文件uuid",
                            "owner": "状态所有者",
                        }

                ''' 
                comments = {}  #参数名：注释
                latest_name = None  #暂存上一次遇到的的参数名
                for token in tokenize.generate_tokens(io.StringIO(source).readline):
                    if token.type == tokenize.COMMENT and latest_name is not None:
                        line = token.line
                        comment = token.string.strip()
                        comments[latest_name] = comment
                        latest_name = None
                    elif token.type == tokenize.NAME and token.string not in ["int", "str", "list", "dict", "None"]:
                        if token.string:
                            latest_name = token.string

                # Create parameter comment map
                param_comment_map = {}
                for param in func_def.args.args:
                    if param.arg in comments:
                        # Get the comment on the same line as the parameter
                        param_comment_map[param.arg] = comments[param.arg].lstrip('#').strip()

                # 组装参数信息
                for param_name, param in sig.parameters.items():
                    if param_name in ["kwargs", "owner", "state", "self"]:
                        continue
                    p_type = str(param.annotation.__name__) if param.annotation != inspect.Parameter.empty else ""
                    p_comment = param_comment_map.get(param_name, "")
                    if not p_comment and g_yaml_config["dev"]["check_annotation"]:
                        p_comment = f"{func_cls.__name__}，参数 {param_name} 没有写注释"
                        raise ValueError(p_comment)
                    # 组装参数格式
                    parameters[param_name] = f"{p_type},{p_comment}"
                '''给llm的tool schema
                {
                    "file_uuid": "str,文件uuid"
                }
                '''
                
        # 组装返回值
        tool = {
            "action": func_cls.__name__,
            "description": doc.strip()
        }
        # 重复的属性--覆盖，不存在的属性--新增
        tool.update(parameters)
        return tool

    @staticmethod
    def log(name: str, msg: Union[str, list[str]]) -> logging.Logger:
        """初始化系统日志记录器"""
        logger = logging.getLogger(name)
        logger.setLevel(logging.INFO)

        # 避免重复添加handler
        if not logger.handlers:
            # 创建按日期命名的日志文件
            log_file = os.path.join(GlobalFunction.log_path(), f"{name}.log")
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
        # Handle both single message and list of messages
        if isinstance(msg, list):
            for message in msg:
                logger.info(message)
        else:
            logger.info(msg)
        return logger

    @staticmethod
    def owner_props_yaml():
        """owner的props文件路径"""
        return os.path.join(GlobalFunction.workspace_path(), "owner_props.yaml")

    @staticmethod
    def embedding_model():
        """获取嵌入模型"""
        return g_yaml_config["model"]["embedding_model"]

    # 图片元信息统一字段模板
    IMAGE_META_COMMON = {
        "图片路径": "",
        "图片格式": "",
        "图片类型": "",
        "内容概述": "",
        "文字识别": ""
    }

    @staticmethod
    def load_image_meta(md_path: str) -> dict:
        """读取图片元信息md文件中的JSON块，模板兜底保证统一字段不缺失"""
        result = dict(GlobalFunction.IMAGE_META_COMMON)
        if os.path.exists(md_path):
            content = GlobalFunction.load_file(md_path)
            match = re.search(r"```json\s*\n([\s\S]*?)\n```", content)
            if match:
                try:
                    result.update(json.loads(match.group(1)))
                except json.JSONDecodeError:
                    pass
        return result

    @staticmethod
    def write_image_meta(output_md_path: str, fields: dict):
        """落盘图片元信息md文件。新建时从统一模板初始化，已存在时增量合并字段"""
        stem = os.path.basename(output_md_path).replace("_图片解析.md", "")
        result = GlobalFunction.load_image_meta(output_md_path)
        result.update(fields)
        with open(output_md_path, 'w', encoding='utf-8') as f:
            f.write(f"# {stem} 图片解析\n\n")
            f.write(f"## 图片信息摘要\n\n")
            f.write(f"```json\n")
            f.write(f"{json.dumps(result, ensure_ascii=False, indent=2)}\n")
            f.write(f"```\n\n")
            f.write(f"## 图片处理\n")