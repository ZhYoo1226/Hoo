# 用于异步思考，生成新的任务。
import queue
import threading

from common import g_tool_registry
from state import SolverOwner, g_owner


class ThinkClient:

    def __init__(self, owner: SolverOwner, **kwargs):
        # #用户意图信息
        # self.chat_text_queue = owner.intent_queue
        # #退出思考event
        # self.quit_event = owner.quit_event
        #
        self.owner = owner
        self.total_intent_count = 0

    def start_working(self):
        # self.owner不能在多进程中传递，只能传递queue数据
        self.process_thread = threading.Thread(target=self.process_chat_text_intent,
                                               args=(self.owner.quit_event, self.owner.intent_queue, self.owner))
        self.process_thread.start()

    def observe_user_intent(self, owner):
        owner.sys_breathe_log("开始观察用户意图")
        self.total_intent_count = 0
        # 分析用户的对话意图,不需要系统提示词
        user_input = "---\n观察用户意图，请输出评估预测结果。\n**输出**"
        predict_action = owner.execute_prompt(user_prompt=None, user_msg=user_input, system_prompt="观察用户意图")
        # 这个地方是返回的action，具体的执行是在下一个状态处理的
        owner.execute_action(predict_action)

    def process_chat_text_intent(self, quit_event, chat_text_queue, owner: SolverOwner):
        """处理聊天的文本意图是什么，如果是一个task，则交给TaskState处理"""
        # 退出思考event
        # self.quit_event = quit_event
        # # 用户意图信息
        # self.chat_text_queue = chat_text_queue
        # # 退出思考event
        # self.quit_event = quit_event
        #
        print('============================LLM对话意图思考线程start=============================')
        # 意图模板
        # judge_prompts = load_file("./templates/chat_templates/judge_chat_intent.txt")
        while not quit_event.is_set():
            try:
                role, chat_new_msg = chat_text_queue.get(timeout=0.5)
                if not chat_new_msg:
                    # 如果没有新消息，且意图分析次数超过3次，则分析意图意图分类
                    if self.total_intent_count >= 3:
                        self.observe_user_intent(owner)
                    continue
                assert owner.llm_intent_model, "意图理解模型未设置，调用llm_intent_model设置意图模型。"
                self.total_intent_count += 1
                owner.record_role_chat(role, chat_new_msg)
                # 回复用户的聊天信息
                owner.execute_chat(owner=owner, state=None)
                # 没有得到明确的意图，需要重新分析用户意图。
                if chat_text_queue.empty() or self.total_intent_count >= 3:
                    # 如果有新消息未处理，则优先处理新消息，再分析意图
                    self.observe_user_intent(owner)
                # 处理任务
            except queue.Empty:
                pass
        print('============================process_chat_text_intent thread stop=============================')


@g_tool_registry.register("predict:task")
def predict_task(**kwargs):
    """用户提出了一个明确的、全新的、未被追踪的任务，智能体需要开始执行或管理该任务。 """
    output_action_json = g_owner.execute_prompt("分析创建任务")
    g_owner.execute_action(output_action_json)


@g_tool_registry.register("predict:speak")
def predict_speak(**kwargs):
    """若本轮已完成对用户的直接回应，且无待处理任务"""
    g_owner.sys_breathe_log("继续推进流程...")
    pass


@g_tool_registry.register("predict:ask")
def predict_ask(**kwargs):
    """用户的意图或需求模糊，缺少执行必要信息，智能体必须立即提问以推进对话。"""
    output_action_json = g_owner.execute_prompt("分析任务信息需求")
    g_owner.execute_action(output_action_json)


@g_tool_registry.register("predict:clarify")
def predict_clarify(**kwargs):
    """用户正在对某个已存在的任务进行信息补充、细节说明、约束修正或澄清。  """
    g_owner.sys_breathe_log("开始分析用户是否在补充任务基础信息")
    output_action_json = g_owner.execute_prompt("用户补充信息")
    g_owner.execute_action(output_action_json)


@g_tool_registry.register("predict:continue")
def predict_continue(**kwargs):
    """当前所有信息已被处理完毕，没有更多未处理的数据或待办项，且用户并未发出新的交互请求。智能体应主动推进流程或结束交互。  """
    g_owner.sys_breathe_log("继续推进流程...")
    pass
