'''
构建领域规划
'''

import hashlib
import json
import os
from dataclasses import dataclass
from typing import Optional, List, Dict

import yaml
from engram import Memory

from l2p.domain_builder import DomainBuilder
from l2p.llm.openai import OPENAI
from l2p.prompt_builder import PromptBuilder
from l2p.utils import load_file
from l2p.utils.pddl_types import Predicate
from task_template import think_task


@dataclass
class DomainTemplate:
    """领域模板，包含类型、常量、谓词等完整信息"""
    domain_desc: str
    types: Optional[Dict[str, str]]
    constants: Optional[Dict[str, str]]
    predicates: List[Predicate]
    functions: Optional[List]


class PredicateManager:
    """
    管理谓词的自动提取与缓存。
    结合 Engram 和 L2P，实现“先检索，后生成”的模式。
    """

    def __init__(self, engram_client, llm: OPENAI, domain_builder: DomainBuilder):
        self.engram = engram_client
        self.llm = llm
        self.domain_builder = domain_builder

        self._FUNCTIONS = {
            'formalize_type' : domain_builder.formalize_types,
            'formalize_predicates' : domain_builder.formalize_predicates,
            'formalize_type_hierarchy' : domain_builder.formalize_type_hierarchy,
            'formalize_constants' : domain_builder.formalize_constants,
            'formalize_effects' : domain_builder.formalize_effects,
            'formalize_functions' : domain_builder.formalize_functions,
            'formalize_parameters' : domain_builder.formalize_parameters,
            'formalize_pddl_action' : domain_builder.formalize_pddl_action,
            'formalize_preconditions' : domain_builder.formalize_preconditions,
            'extract_nl_actions' : domain_builder.extract_nl_actions,
        }


    #反馈一个具体的设计是否有修改建议。
    def setup_feedback_prompt(self):
        '''
        反馈是关于PDDL的设计，是否存在建议。
        '''
        from l2p.feedback_builder import FeedbackBuilder
        domain_desc = """
        这里的智能机器人代理是一个机械臂，能够拾取并放置这些积木。每次只能移动一块积木：它可以被放置在桌子上，也可以放置在另一块积木之上。正因为如此，任何在某一时刻处于另一块积木之下的积木都无法被移动。
        """

        types = {
                "机械臂": "能够拾取并放置积木的智能机器人代理",
                # "积木": "可以被机械臂拾取和放置的物体",
                # "桌子": "积木可以被放置的平面表面",
                'carpet': '; a carpet for a room.'  # unnecessary type for domain
        }

        feedback_builder = FeedbackBuilder()

        feedback_prompt = PromptBuilder(
            role="您是为“:types”设计提供反馈的 PDDL 助手。",
            format=load_file("../../templates/feedback_templates/feedback.txt"),
            task="{domain_desc} \n\n##Types\n{types}"
        ).generate_prompt()
        print(f"feedback_prompt:{feedback_prompt}")

        no_feedback, llm_output = feedback_builder.type_feedback(
            model=self.llm,
            domain_desc=domain_desc,
            feedback_template=feedback_prompt,
            feedback_type="llm",
            types=types
        )

        print(no_feedback, llm_output)
        

    def formalize_domain_spec(self,
                            domain_desc : str=None,
                            role : str=None,
                            format: str=None,
                            task: str="{domain_desc}",
                            examples: list = None,
                            **kwargs):
        format_desc = load_file(f"templates/domain_templates/{format}.txt")
        if not role:
            role = load_file(f"../../templates/domain_templates/role.txt")
        print(f"role.txt:{role}")
        if not examples:
            example = load_file(f"../../templates/domain_templates/examples.txt")
            if example:
                examples = [example]
            else:
                examples = None
        '''
        role:
        format:
        examples:
        task:
        '''
        types_prompt = PromptBuilder(
            role=role,
            format=format_desc,
            task=task,
            examples = examples
        )
        assert domain_desc, f"setup_domain_prompt,领域描述不能为空,模板【{format}.txt】：-->\n{format_desc}\n"
        prompt_desc = types_prompt.generate_prompt()
        print(f"formalize_domain_spec构建的提示词模板：*************start>>>>>>>>>\n{prompt_desc}\n<<<<<End*******")

        # 准备传给 _FUNCTIONS[format] 的基础参数
        call_args = {
            "model": self.llm,
            "domain_desc": domain_desc,
            "prompt_template": prompt_desc
        }
        # 将额外的 kwargs 合并进去
        call_args.update(kwargs)
        # extract types via LLM
        try :
            results, llm_output, validation_info = self._FUNCTIONS[format](**call_args)
        except Exception as ex:
            print(f"method error===>>>>{format}")
            raise ex
        return results, llm_output, validation_info

    #一次性抽取:types, :constants, :predicates, :functions
    def extract_domain_level_spec(
            self,
            domain_desc: str,
            max_retries: int = 3
    ):
        #spec_results{types,constants,predicates,functions}, llm_output, validation_info
        #直接生成全部的领域级的规范描述
        spec_results,llm_output, validation_info = self.domain_builder.formalize_domain_level_specs(
            model=self.llm,
            domain_desc=domain_desc,
            prompt_template=load_file("../../templates/domain_templates/formalize_domain_level_specs.txt"),
            formalize_types = True,
            formalize_constants = True,
            formalize_predicates = True,
            formalize_functions = True,
            max_retries = 3,
            )
        return spec_results,llm_output, validation_info

    def process_domain_pddl(self,domain_desc : dict | str) -> None:
        # 如果 domain_desc 是字典（即 JSON 对象），则转换为 JSON 字符串
        if isinstance(domain_desc, dict):
            domain_desc = json.dumps(domain_desc, ensure_ascii=False)
        # 1. 提取根类型
        types, llm_output, validation_info = self.formalize_domain_spec(
            domain_desc=domain_desc,
            format="formalize_type",
            types = domain_builder.get_types(),
        )
        # print(f"types:{types}")
        with open('./workspace/types.json', 'w', encoding='utf-8') as f:
            json.dump(types, f, ensure_ascii=False, indent=4)

        domain_builder.set_types(types)  # 存入 domain_builder


        #1.1 提取子类型（层次化）
        type_hierarchy, llm_output, validation_info = self.formalize_domain_spec(
            domain_desc=domain_desc,
            types = domain_builder.get_types(),
            format="formalize_type_hierarchy")

        # print(f"type_hierarchy:{type_hierarchy}")
        with open('./workspace/type_hierarchy.json', 'w', encoding='utf-8') as f:
            json.dump(type_hierarchy, f, ensure_ascii=False, indent=4)
        domain_builder.set_type_hierarchy(type_hierarchy)  # 存入 domain_builder

        # 2. 提取谓词
        predicates, llm_output, validation_info = self.formalize_domain_spec(
            domain_desc=domain_desc,
            format="formalize_predicates",
            types = domain_builder.get_types(),
            constants = domain_builder.get_constants(),
            predicates = domain_builder.get_predicates(),
            functions = domain_builder.get_functions(),
        )
        for pred in predicates:
            # print(f"\n=================\n{pred}\n")
            domain_builder.set_predicate(pred)

        with open('./workspace/predicates.json', 'w', encoding='utf-8') as f:
            json.dump(predicates, f, ensure_ascii=False, indent=4)

        #3. 生成多个动作名称，extract_nl_actions
        actions, llm_output, validation_info = self.formalize_domain_spec(
            domain_desc=domain_desc,
            format="extract_nl_actions",
            types=domain_builder.get_types(),
            nl_actions = domain_builder.get_pddl_actions(),
        )
        with open('./workspace/actions.json', 'w', encoding='utf-8') as f:
            json.dump(actions, f, ensure_ascii=False, indent=4)

        for action_name, action_desc in actions.items():
            print(f"{action_name}: {action_desc}")
            # 4. 提取动作（逐个），需要设计动作名称，动作名称从哪里来？
            task_desc = """
[已知信息]
可用动作: 
{action_list}
可用类型: 
{types}
可用常量: 
{constants}
可用谓词: 
{predicates}
可用函数: 
{functions}
[任务信息]
动作名称: 
{action_name}
动作描述: 
{action_desc}
"""
            # 领域描述:
            # {domain_desc}
            
            res_specs, llm_output, validation_info = self.formalize_domain_spec(
                task=task_desc,
                action_name=action_name,
                action_list = domain_builder.get_pddl_actions(),
                domain_desc=domain_desc+"\n\n动作描述："+action_desc,
                format="formalize_pddl_action",
                types=domain_builder.get_types(),  # 传入已有类型
                predicates=domain_builder.get_predicates(),
                functions=domain_builder.get_functions(),
                extract_new_preds=True  # 允许从动作描述中提取新谓词
            )
            action = res_specs['action']
            print(f"抽象的Action结果:{action}")
            new_predicates = res_specs['new_predicates']
            domain_builder.set_pddl_action(action)

            # 根据新的谓词更新
            for pred in new_predicates:
                domain_builder.set_predicate(pred)

        #写入全部的谓词
        with open('./workspace/predicates.json', 'w', encoding='utf-8') as f:
            json.dump(domain_builder.get_predicates(), f, ensure_ascii=False, indent=4)


        # 5. 生成最终领域
        domain_pddl = domain_builder.generate_domain(domain_name="server-admin")
        print(domain_pddl)
        with open('./workspace/domain_pddl.txt', 'w', encoding='utf-8') as f:
            f.write(domain_pddl)

    def get_or_extract_domain_template(
            self,
            domain_desc: str,
            prompt_template: str,
            types: Optional[Dict[str, str]] = None,
            constants: Optional[Dict[str, str]] = None,
            existing_predicates: Optional[List] = None,
            functions: Optional[List] = None,
            syntax_validator=None,
            max_retries: int = 3,
    ) -> DomainTemplate:
        """
        获取领域模板。先从 Engram 检索，若无则调用 L2P 提取并存储。
        """
        search_key = self._make_search_key(domain_desc)

        # 1. 尝试从 Engram 检索
        # 假设 memory 是 Engram 实例，有 search 方法
        # 如果 Engram 使用语义搜索，可以这样调用：
        # cached = self.engram.search(query=domain_desc, type="domain_template")
        # 但这里我们暂时使用 search_key 精确匹配（实际应使用语义）
        # 为了简化，我们假设 self.engram 有 get 方法
        cached = self.engram.recall(query=domain_desc, types=["domain_template"])
        if cached:
            # 从缓存重建 DomainTemplate
            return DomainTemplate(
                domain_desc=cached.get('domain_desc', domain_desc),
                types=cached.get('types'),
                constants=cached.get('constants'),
                predicates=[Predicate(**p) for p in cached['predicates']],
                functions=cached.get('functions'),
            )
        else:
            print("engram没有找到缓存")

        # 2. 未命中，调用 L2P 提取新谓词
        print(f"领域描述:{domain_desc}")
        new_predicates, llm_output, validation_info = self.domain_builder.formalize_predicates(
            model=self.llm,
            domain_desc=domain_desc,
            prompt_template=prompt_template,
            types=types,
            constants=constants,
            predicates=existing_predicates,  # 传入已有谓词，让 LLM 生成增量
            functions=functions,
            syntax_validator=syntax_validator,
            max_retries=max_retries,
        )

        if not validation_info[0]:
            raise RuntimeError(f"谓词提取失败: {validation_info[1]}")

        # print(f"LLM返回内容:\n{llm_output}")

        # 合并已有谓词和新谓词（去重）
        # 注意：需要统一类型，假设 existing_predicates 可能是 Predicate 对象列表或字典列表
        if existing_predicates:
            combined_predicates = self._merge_predicates(existing_predicates, new_predicates)
        else:
            combined_predicates = new_predicates

        # 3. 存储到 Engram
        template = {
            'domain_desc': domain_desc,
            'types': types,
            'constants': constants,
            'predicates': [self._predicate_to_dict(p) for p in combined_predicates],
            'functions': functions,
        }
        # 假设 self.engram 是 Engram 客户端，有 add 方法
        self.engram.add(
            content=json.dumps(template, ensure_ascii=False),
            type="domain_template",
            importance=0.75,
            metadata={
                "domain_desc": domain_desc,
                "types": str(types),
                "constants": str(constants),
                "predicates": str([self._predicate_to_dict(p) for p in combined_predicates]),
                "functions": str(functions)
            }
        )

        return DomainTemplate(
            domain_desc=domain_desc,
            types=types,
            constants=constants,
            predicates=combined_predicates,
            functions=functions,
        )

    # ------------------ 辅助方法 ------------------
    def _predicate_to_dict(self, pred) -> dict:
        if hasattr(pred, '__dict__'):
            return pred.__dict__
        elif isinstance(pred, dict):
            return pred
        else:
            raise TypeError(f"Unsupported predicate type: {type(pred)}")
        
    def _make_search_key(self, domain_desc: str) -> str:
        """生成检索键（简单使用描述哈希的前16位）"""
        return hashlib.sha256(domain_desc.encode()).hexdigest()[:16]

    def _merge_predicates(
            self, existing: List[Predicate], new: List[Predicate]
    ) -> List[Predicate]:
        """合并两个谓词列表，基于 name 去重"""
        names = {p.name for p in existing}
        merged = existing[:]
        for p in new:
            if p.name not in names:
                merged.append(p)
                names.add(p.name)
        return merged



def test_generate_domain_lev_spec():
    spec_results, llm_output, validation_info = manager.extract_domain_level_spec(
        domain_desc=domain_desc,
        prompt_template=predicates_prompt,
    )

    print("============================\n")
    print(f"{spec_results['types']}")
    print("============================\n")
    print(f"{spec_results['constants']}")
    print("============================\n")
    print(f"{spec_results['predicates']}")
    print("============================\n")
    print(f"{spec_results['functions']}")



# action = load_file(f'{base_path}action.json')
# print(f"{action}")

# extract predicates via LLM
# predicates, llm_output, validation_info = domain_builder.formalize_predicates(
#     model=llm,
#     domain_desc=domain_desc,
#     prompt_template=predicates_prompt,
#     types=types
# )

# format key info into PDDL strings
# predicate_str = "\n".join([pred["raw"].replace(":", " ; ") for pred in domain_template.predicates])
# print(f"----最终生成【{domain_desc}】的谓词PDDL:\n{predicate_str}")

# 使用示例
if __name__ == "__main__":
    domain_builder = DomainBuilder()
    '''
    统一管理配置文件
    '''
    with open("../../config.yaml", "r", encoding="utf-8") as f:
        g_yaml_config = yaml.safe_load(f)

    '''
    统一管理大模型
    '''
    llm = OPENAI(model="gpt-5",  # gpt-4o-mini
                 api_key=os.getenv("API_KEY") or g_yaml_config["openai"]["api_key"],
                 base_url=g_yaml_config["openai"]["base_url"],
                 )

    # retrieve prompt information
    base_path = '../../templates/domain_templates/'
    domain_desc = "本机是一台Linux服务器，需要使用账号密码，登录到远程另一台Linux服务器。"
    predicates_prompt = load_file(f'{base_path}formalize_domain_spec.txt')
    # print(f"谓词提取提示词：{predicates_prompt}")

    # types = load_file(f'{base_path}types.json')
    # print(f"{types}")

    engram = Memory("./task_templates.db")

    manager = PredicateManager(engram, llm, domain_builder)



    # results, llm_output, validation_info = manager.setup_domain_prompt(
    #     domain_desc="这里的智能机器人代理是一个机械臂，能够拾取并放置这些积木。每次只能移动一块积木：它可以被放置在桌子上，也可以放置在另一块积木之上。正因为如此，任何在某一时刻处于另一块积木之下的积木都无法被移动。",
    #     role="你是一位中文语言的 PDDL 辅助工具，正在协助我进行类型设计:types。",
    #     format="formalize_type",
    #     task="{domain_desc}",
    #     examples=None)
    # print(f"llm_output:\n{llm_output}\n===>>>>>>>>>>>>>>>>\n")
    # print(f"{results}")

    domain_desc = "信息分为已知信息和未知信息。当遇到未知信息时，需要按照一下方案执行：搜索查找信息，存储信息，分析信息，形成可执行的方案。验证方案是否可行，记录验证结果。如果不行，需要重新搜索信息，调试方案进化步骤，继续尝试执行。"
    domain_desc = """
    
    """

    #动态构建领域提示词，动态解析生成对应的数据。
    # results, llm_output, validation_info = manager.formalize_domain_spec(
    #     domain_desc=domain_desc,
    #     role="你是一位中文语言的 PDDL 辅助工具，正在协助我进行谓词设计:Predicates。",
    #     format="formalize_predicates",
    #     task="{domain_desc}",
    #     examples=None)
    # print(f"【setup_domain_prompt】llm_output:\n{llm_output}\n===>>>>>>>>>>>>>>>>\n")
    # print(f"{results}")


    #一次性抽取domain领域的specs，并没有复用已有的信息。
    # results, llm_output, validation_info = manager.extract_domain_level_spec(domain_desc=domain_desc)
    # print("++++++++++++++++++++++【extract_domain_level_spec】+++++++++++++++++++++++++++++++++++++++")
    # print(f"llm_output:\n{llm_output}\n===>>>>>>>>>>>>>>>>\n")
    # print(f"{results}")
    #
    # domain_desc = "未知信息获取失败，对未知信息重新执行搜索，需要修改搜索的关键词。"
    # results, llm_output, validation_info = manager.extract_domain_level_spec(domain_desc=domain_desc)
    # print("++++++++++++++++++++++【extract_domain_level_spec】+++++++++++++++++++++++++++++++++++++++")
    # print(f"llm_output:\n{llm_output}\n===>>>>>>>>>>>>>>>>\n")
    # print(f"{results}")
    #



    # domain_desc_json = think_task("""
    # 解决任务问题的依据是根据已知信息和已知工具，工具使用说明来灵活构建可执行的方案。如果缺少相关信息，这个信息就是一个未知的，未知的信息包括未知的知识，未知的方案，未知的数据（比如账号，密码，IP，端口）等。
    # 一个具体的任务应必备可执行的方案，如果没有方案，就需要搜索PDDL方案，如果搜索不到PDDL方案，就要创建PDDL方案。
    # 创建PDDL方案,需要根据执行的目标任务，获取任务的构建过程文字描述。为了让这个描述更为专业和清晰，应当使用TOOLS调用API接口来优化任务需求。
    # 优化一个任务需求，需要检索已知信息。检索已知信息可以调用函数search_user('任务需求')获取。
    # 一个具体的任务应具备前提条件信息，如果没有前提条件信息，就需要根据信息的不同类型：如果是隐私信息就需要从个人信息存储中获取（如密码信息、账号信息、助记词、亲朋好友），隐私信息存储的地方多个：文件，memory，数据库；如果是网络信息就需要调用web_search工具去搜索信息摘要，根据信息摘要决定是否采用web_fetch工具去获取信息。
    # """)
    domain_desc_json = think_task("""
    获取一个未知信息（数据、方案、方式方法、知识）的操作步骤。""")
    # 确保workspace目录存在
    os.makedirs('../../workspace', exist_ok=True)

    # 将domain_json存储到文件
    domain_desc_file = './workspace/domain_desc.json'
    with open(domain_desc_file, 'w', encoding='utf-8') as f:
        json.dump(domain_desc_json, f, ensure_ascii=False, indent=4)

    #处理领域的pddl
    manager.process_domain_pddl(domain_desc= load_file(domain_desc_file))
    #生成提示词


    # manager.setup_feedback_prompt()

    # # 获取领域模板（自动处理缓存）
    # domain_template = manager.get_or_extract_domain_template(
    #     domain_desc=domain_desc,
    #     prompt_template=predicates_prompt,
    # )
    #
    # # print(f"{domain_template}")
    #
    # # 使用提取的谓词
    # print("==================================打印所有的谓词===========================")
    # for pred in domain_template.predicates:
    #     print(f"\n{pred}\n\n")
