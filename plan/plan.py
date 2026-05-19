from typing import Any, Dict, List

#计划的步骤
class Step:
    #唯一标识
    id: str
    #步骤：需要做什么？
    intent: str
    #依赖信息需求
    requires: List[str]
    #依赖信息由什么执行提供
    provider: str
    #参数
    params: Dict
    status: str = 'pending'


class InformationStore:
    data: Dict[str, Any]  # info_key -> value + metadata
    def is_known(self, key):
        pass

    def get(self, key):
        pass

    def update(self, key, value, source):
        pass


#一个任务计划
#一个任务计划
class Plan:
    #计划中的任务步骤
    steps: List[Step]
    current_index: int
    context: Dict  # 全局信息

    def __init__(self, plan_type: str = "default"):
        self.steps = []
        self.current_index = 0
        self.context = {}
        self.plan_type = plan_type

    def add_step(self, step: Step) -> None:
        self.steps.append(step)


#元认知与元计划
def _examples_build_intent_clarification_plan(task_description: str, info_store: InformationStore) -> Plan:
    plan = Plan(plan_type="intent_clarification")

    # Step 1: 收集本地背景
    plan.add_step(Step(
        step_id="cl_1",
        intent="主动收集本地背景信息",
        requires=[],
        provider="tool:local_context_retrieval",
        params={"task_hint": task_description}
    ))
    # Step 2: 生成澄清问题 (LLM)
    plan.add_step(Step(
        step_id="cl_2",
        intent="基于本地背景决定是否需要向用户提问",
        requires=["cl_1.result"],
        provider="llm:generate_clarification_question",
        params={"task": task_description}
    ))
    # Step 3: 向用户提问 (条件执行)
    plan.add_step(Step(
        step_id="cl_3",
        intent="向用户提出澄清问题",
        requires=["cl_2.question_text"],  # 如果cl_2输出need_question=false，这个依赖会缺失，由此触发跳过逻辑
        provider="user:ask",
        params={"message": "{{cl_2.question_text}}"}
    ))
    # Step 4: 判断就绪
    plan.add_step(Step(
        step_id="cl_4",
        intent="整合信息判断是否足够执行任务",
        requires=["cl_1.result", "cl_3.user_response"],  # cl_3可能未执行，此时user_response为空
        provider="llm:decide_readiness",
        params={"task": task_description}
    ))
    return plan

#
# class ReActLoop:
#     def run(self, initial_task, user_context):
#         task_type = TaskClassifier.classify(initial_task)
#         plan = Planner.create_plan(initial_task, task_type, user_context)
#         info_store = InformationStore.from_context(user_context)
#
#         while not self.goal_reached(plan, info_store):
#             step = plan.current_step()
#             # 检查信息依赖
#             missing_plan = self.resolve_dependencies(step, info_store)
#             if missing_plan:
#                 plan.insert_sub_plan(missing_plan)
#                 continue
#
#             # 决定动作：调用 LLM 进行 DECIDE
#             decision = self.llm_decide(plan, info_store, task_type)
#             action = decision['action']
#
#             # 执行动作
#             observation = self.execute(action)
#
#             # 反思观察
#             reflection = self.llm_reflect(step, action, observation, task_type)
#
#             if reflection.get('trigger_new_task'):
#                 new_plan = self.planner.create_plan(reflection['trigger_new_task'], ...)
#                 plan.suspend_and_store(new_plan)  # 堆栈式多任务
#                 continue
#
#             if not reflection['is_expected'] or reflection.get('need_replan'):
#                 # 重规划
#                 plan = self.replan(plan, observation, info_store, task_type)
#             else:
#                 info_store.update_from_observation(observation)
#                 plan.advance_step()
#
#         return plan.final_result()