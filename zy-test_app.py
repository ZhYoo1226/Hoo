import os.path
from pathlib import Path
import pytest

from app import g_owner, run_server
from common import g_yaml_config
# 行续符，等价于括号下的换行写法 python主动处理括号、方括号、花括号的换行
from state import MetaReadState, ClearChatState, ClearTaskState, \
    ClearEvalAdviceState
from state.state_doc import WordParseState, HeadingChunk
from state.state_convert import Text2ChunksState
from state.state_ocr import TableOCRState
from state.state_task import ScanTaskState, ExecuteTaskState
from state.state_modify import (
    ModifyYamlState, ModifyJsonState, ModifyIniState, ModifyTomlState, ModifyExcelState,
    ModifyHtmlState, ModifyMarkdownState, ModifyDocxState,
    ModifyXmlState, ModifyEnvState, ModifyRegexState, ModifyPyCodeState,
)
from state.state_review import BuildKnowledgeState

# 测试生成知识树
def test_build_json_tree():
    file_path = r'D:\solver\docs\review\标准\中国铁路成都局集团有限公司营业线施工管理实施细则 - 副本.docx'
    g_owner.add_state(BuildKnowledgeState(file_path,'营业线施工管理实施细则'))

# FIXME 张耀: 元认知--测试文件解析
def utest_file_parse():
    file_path = os.path.join(g_owner.workspace_path, "user/files/test/test.xlsx")
    g_owner.add_state(MetaReadState(file_path, "稍等，我阅读一下文件", "用户突然传来文件，没有明显的需求和任务归属。"))

# 测试前 图片OCR 检查是否有旧产物（ 重跑 word parse state，效果更清晰 ）
def ztest_img_ocr_state():
    root = Path(g_owner.workspace_path) / "user/files/202605/罗玉华-中国银行__docx__解析/图"
    imgocr_state = TableOCRState(source_path=str(root), batch_index=0, auto_install=False)
    g_owner.add_state(imgocr_state)

# zhang 文档解析状态机 提前删除产物，看效果（有幂等检查）
def ztest_word_parse_state():
    file_path = str(Path(__file__).parent / "docs/review/方案/S218盐边县城连接线下穿峨广线铁路工程涉铁段专项施工方案.docx")
    parse_state = WordParseState(file_path)
    g_owner.add_state(parse_state)

# zhang 仅实现ScanTaskState
def ztest_scan_task():
    """扫描现有的任务"""
    g_owner.add_state(ScanTaskState())
    g_owner.add_state(ExecuteTaskState())

def clear_local_chat_data_state():
    """清除所有状态"""
    # 清除聊天记录
    if g_yaml_config["chat"]["launch_clear_chat"]:
        g_owner.add_state(ClearChatState())
    # 清除任务
    if g_yaml_config["chat"]["launch_clear_task"]:
        g_owner.add_state(ClearTaskState())
    # 清除评估建议
    if g_yaml_config["chat"]["launch_clear_prompt_eval"]:
        g_owner.add_state(ClearEvalAdviceState())


# --------------------------------------------------------------------------
# 文件修改状态测试
# --------------------------------------------------------------------------

_MODIFY_DIR = "user/files/test_modify"

# yaml文件修改（已有 key）
def ztest_modify_yaml():
    test_file = os.path.join(g_owner.workspace_path, _MODIFY_DIR, "test.yaml")
    g_owner.add_state(ModifyYamlState(file=test_file, xpath="server.port", value=9090))

# json文件修改
def ztest_modify_json():
    test_file = os.path.join(g_owner.workspace_path, _MODIFY_DIR, "test.json")
    g_owner.add_state(ModifyJsonState(file=test_file, xpath="database.host", value="10.0.0.1"))

# ini文件修改
def ztest_modify_ini():
    test_file = os.path.join(g_owner.workspace_path, _MODIFY_DIR, "test.ini")
    g_owner.add_state(ModifyIniState(file=test_file, xpath="database.host", value="db.internal"))

# toml修改
def ztest_modify_toml():
    test_file = os.path.join(g_owner.workspace_path, _MODIFY_DIR, "test.toml")
    g_owner.add_state(ModifyTomlState(file=test_file, xpath="server.port", value=3000))

# excel修改
def ztest_modify_excel():
    test_file = os.path.join(g_owner.workspace_path, _MODIFY_DIR, "test.xlsx")
    g_owner.add_state(ModifyExcelState(file=test_file, xpath="Sheet1!B5", value=999))

# yaml文件修改--新增嵌套结构里的字段
def ztest_modify_new_key():
    test_file = os.path.join(g_owner.workspace_path, _MODIFY_DIR, "test.yaml")
    g_owner.add_state(ModifyYamlState(file=test_file, xpath="app.debug.enabled", value=True))

# html修改
def ztest_modify_html():
    test_file = os.path.join(g_owner.workspace_path, _MODIFY_DIR, "test.html")
    g_owner.add_state(ModifyHtmlState(file=test_file, origin_text="Hello World", new_text="Hello Claude"))

# md修改
def ztest_modify_markdown():
    test_file = os.path.join(g_owner.workspace_path, _MODIFY_DIR, "test.md")
    g_owner.add_state(ModifyMarkdownState(file=test_file, origin_text="Hello World", new_text="Hello Claude"))

# docx文档修改
def ztest_modify_docx():
    test_file = os.path.join(g_owner.workspace_path, _MODIFY_DIR, "test.docx")
    g_owner.add_state(ModifyDocxState(file=test_file, origin_text="Hello World", new_text="Hello Claude"))

# xml文件修改
def ztest_modify_xml():
    test_file = os.path.join(g_owner.workspace_path, _MODIFY_DIR, "test.xml")
    g_owner.add_state(ModifyXmlState(file=test_file, xpath="server.port", value=9090))

# env环境配置修改
def ztest_modify_env():
    test_file = os.path.join(g_owner.workspace_path, _MODIFY_DIR, "test.env")
    g_owner.add_state(ModifyEnvState(file=test_file, xpath="DATABASE_HOST", value="10.0.0.1"))

# conf文件修改
def ztest_modify_regex():
    test_file = os.path.join(g_owner.workspace_path, _MODIFY_DIR, "test.conf")
    g_owner.add_state(ModifyRegexState(file=test_file, origin_text=r"server_port=\d+", new_text="server_port=9090"))

# python代码修改
def ztest_modify_pycode():
    test_file = os.path.join(g_owner.workspace_path, _MODIFY_DIR, "test.py")
    g_owner.add_state(ModifyPyCodeState(file=test_file, xpath="PORT", value=9090))


if __name__ == "__main__":
    # 启动时自动执行单元测试 执行test_*
    # zhang 不加-cache--clear 不能很好的切换test函数测试
    pytest.main([__file__, "-v", "-s", "--cache-clear"])
    # 测试通过后启动服务器  先注释服务器启动
    run_server()
