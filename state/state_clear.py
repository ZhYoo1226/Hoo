import os
import shutil

from common import GlobalFunction
from state import BaseState


class ClearTaskState(BaseState):
    """清除工作任务状态"""

    stateName = "ClearTaskState"

    def Enter(self, owner):
        GlobalFunction.log("运行状态", "开始清除所有的工作任务")
        task_dir = GlobalFunction.task_path()
        if os.path.exists(task_dir):
            for filename in os.listdir(task_dir):
                file_path = os.path.join(task_dir, filename)
                try:
                    if os.path.isfile(file_path):
                        os.unlink(file_path)
                        GlobalFunction.log("运行状态", f"成功删除工作任务文件: {file_path}")
                    elif os.path.isdir(file_path):
                        shutil.rmtree(file_path)
                        GlobalFunction.log("运行状态", f"成功删除工作任务目录: {file_path}")
                except Exception as e:
                    GlobalFunction.log("运行状态", f"删除 {file_path} 时发生错误: {e}")

    def Execute(self, owner):
        owner.remove_state(self)


class ClearChatState(BaseState):
    stateName = "ClearChatState"

    def Enter(self, owner):
        GlobalFunction.log("运行状态", "开始清除所有聊天记录")
        chat_dir = GlobalFunction.chat_path()
        if os.path.exists(chat_dir):
            for filename in os.listdir(chat_dir):
                file_path = os.path.join(chat_dir, filename)
                try:
                    if os.path.isfile(file_path):
                        os.unlink(file_path)
                        GlobalFunction.log("运行状态", f"成功删除聊天记录文件: {file_path}")
                    elif os.path.isdir(file_path):
                        shutil.rmtree(file_path)
                        GlobalFunction.log("运行状态", f"成功删除聊天记录目录: {file_path}")
                except Exception as e:
                    GlobalFunction.log("运行状态", f"删除 {file_path} 时发生错误: {e}")

    def Execute(self, owner):
        owner.remove_state(self)


class ClearEvalAdviceState(BaseState):
    """清除评估建议状态"""

    stateName = "ClearEvalAdviceState"

    def Enter(self, owner):
        GlobalFunction.log("运行状态", "开始清除所有评估建议")
        eval_dir = GlobalFunction.prompt_path()
        if os.path.exists(eval_dir):
            for filename in os.listdir(eval_dir):
                file_path = os.path.join(eval_dir, filename)
                if filename.endswith("_(评估建议).md"):
                    try:
                        if os.path.isfile(file_path):
                            os.unlink(file_path)
                            GlobalFunction.log("运行状态", f"成功删除提示词的评估建议文件: {file_path}")
                    except Exception as e:
                        GlobalFunction.log("运行状态", f"删除 {file_path} 时发生错误: {e}")

    def Execute(self, owner):
        owner.remove_state(self)
