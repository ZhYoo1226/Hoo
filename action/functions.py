import os

import yaml  # 添加yaml导入

from common.register import g_tool_registry
from state import StateOwner


# solver的基本能力
# 1.个人信息
# 2.人物关系
# 3.读取隐私信息


@g_tool_registry.register("meta:read_file")
def read_file(file_path: str,  # 文件路径
              max_read_size: int = 5 * 1024,  # 最大读取字节数
              **kwargs):
    """
    读取文件内容
    """
    # 也必须是一个状态，进入的时候，文件正在被读取，退出的时候，文件没有被读取。
    # 读取文件内容，不管他是什么文件。包括pdf,doc,docx,image,excel等。
    # 设置文件大小阈值（5KB），超过这个大小就返回摘要
    # 不管是什么文件，都应该被读取。
    try:
        file_size = os.path.getsize(file_path)
        with open(file_path, 'r', encoding='utf-8') as f:
            if file_size > max_read_size:
                content = f.read(max_read_size)
                return f"文件较大（{file_size} 字节），以下是前 {max_read_size} 字节内容摘要：\n\n{content}\n\n...（内容已截断）"
            else:
                content = f.read()
                return content
    except FileNotFoundError:
        return f"错误：文件 {file_path} 不存在"
    except Exception as e:
        return f"读取文件出错：{str(e)}"


@g_tool_registry.register("meta:read_mine_profile")
def read_mine_profile(profile_path: str = "profile.yaml",  # 个人资料文件路径
                      owner: StateOwner = None,  # 状态所有者
                      **kwargs):
    """
    读取个人资料
    :param profile_path: 个人资料文件路径
    :param owner: 状态所有者
    :param kwargs: 其他参数
    :return: 个人资料信息字符串
    """
    try:
        if not os.path.exists(profile_path):
            return f"错误：个人资料文件 {profile_path} 不存在"

        with open(profile_path, 'r', encoding='utf-8') as f:
            profile_data = yaml.safe_load(f)

        # 格式化输出个人资料信息
        result = "个人资料信息：\n\n"
        for key, value in profile_data.items():
            result += f"{key}: {value}\n"

        return result
    except Exception as e:
        return f"读取个人资料出错：{str(e)}"


@g_tool_registry.register()
def read_memory():
    """
    读取个人信息
    :return: 个人信息字符串
    """
    pass


@g_tool_registry.register()
def web_search(keyword: str = None,  # 信息关键词
               owner: StateOwner = None, **kwargs):
    """
    搜索关键描述相关的信息
    :return: 搜索结果
    """
    print(f"搜索关键词:{keyword}")
    # 搜索相关的网页，得到网页的内容，整理成所需要的结果文本。
    # 1.搜索
    # 2.获取知识
    # 3.提炼结果。

    pass


@g_tool_registry.register("tool:local_retrieval")
def local_retrieval(keyword: str = None,  # 信息关键词
                    owner: StateOwner = None, **kwargs):
    """
    从本地上下文检索信息
    return: 检索结果
    """
    # 从本地上下文检索信息
    pass
