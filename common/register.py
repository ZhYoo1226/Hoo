from common import GlobalFunction


class ToolRegistry:
    def __init__(self):
        self._map = {}

    def register(self, name=None):
        """注册一个工具"""

        def decorator(func):
            key = name if name else func.__name__
            print(f"注册工具: {key}")
            self._map[key] = func
            return func

        return decorator

    def list_tools(self):
        """返回所有已注册工具的详细信息"""
        tools_info = []
        for name, func in self._map.items():
            # 解析参数注释
            try:
                func_json = GlobalFunction.inspect_function_desc(func)
                tools_info.append(func_json)
            except (TypeError, OSError, SyntaxError):
                # 对于内置函数/无法获取源码的函数跳过解析
                pass
        return tools_info

    def list_tool_names(self):
        """返回所有已注册的工具名称"""
        return list(self._map.keys())

    def execute(self, tool_name: str, *args, **kwargs):
        print(f"执行工具: {tool_name} {args} {kwargs}")
        # print(f"工具映射: {self._map}")
        func = self._map.get(tool_name)
        if not func:
            raise KeyError(f"Unknown tool: {tool_name}")
        return func(*args, **kwargs)


# 创建全局注册器
g_tool_registry = ToolRegistry()

# @g_tool_registry.register()
# def greet(name):
#     return f"Hello, {name}!"
#
# @g_tool_registry.register("bye")
# def farewell(name):
#     return f"Goodbye, {name}!"
#
# # 执行
# print(g_tool_registry.execute("greet", "Bob"))   # Hello, Bob!
# print(g_tool_registry.execute("bye", "Bob"))    # Goodbye, Bob!
