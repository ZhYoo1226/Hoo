from .base_state import BaseState,StateOwner
from pathlib import Path
from common import GlobalFunction
from .state_doc import WordParseState,HeadingChunk
import json,threading
from typing import List

# 生成文本块摘要
class GenSummaryState(BaseState):
    """要在原本的json树形结构里添加新的字段（摘要）"""
    def __init__(self, chunks:List[HeadingChunk], **kwargs):
        super().__init__(**kwargs)
        self.chunks = chunks
        self._done = False
        self._duration = 1000*60*15
    
    # 独立的递归函数-->自下而上
    def _summarize_nodes(self,chunks:List[HeadingChunk],owner:StateOwner):
        """自底向上递归，先生成子节点摘要"""
        for chunk in chunks:
            # dataclass vs dict
            if chunk.children:
                self._summarize_nodes(chunk.children,owner)
            
            lines = []
            for child in chunk.children:
                if child.summary:
                    lines.append(f'{child.number} {child.title}:{child.summary}')
            # 都是对已有的chunk进行原地修改
            self.children_summaries = '\n'.join(lines)
            
            self.node_number = chunk.number
            self.node_title = chunk.title
            self.node_level = chunk.level
            self.node_content = chunk.content
            
            actions = owner.execute_prompt('生成节点摘要',self)
            params = actions[0]['params']
            chunk.prefix_summary = params['prefix_summary']
            chunk.summary = params['summary']
            
    def _run_summarize(self,owner):
        try:
            self._summarize_nodes(self.chunks,owner)
        except Exception as e:
            GlobalFunction.log('知识树摘要总结出错',f'出错内容:{e}')
        finally:
            self._done = True        
            
    def Enter(self,owner):
        self._thread = threading.Thread(
            target=self._run_summarize,args=(owner,),daemon=True
        )
        self._thread.start()
    
    def Execute(self, owner:StateOwner):
        if self._done:
            owner.remove_state(self)
            return
    def Exit(self, owner):
        pass
            
# 构建pageindex的知识库json知识树
class BuildKnowledgeState(BaseState):
    def __init__(self, source_path:str , name:str ,**kwargs):
        super().__init__(**kwargs)
        self.path = source_path
        self.name = name
        self._done = False
        self._duration = 1000 * 60 * 10
        self._stage = 'init'

    # 生命周期函数写状态机和owner
    """
    注意java是静态类型语言，使用的时候必须声明才可以编译通过
    python是运行时才检查owner能不能胜任后续的使用，不行-->AttributeError
    鸭子类型-->看起来像鸭子、叫起来像鸭子-->鸭子
    """
    def Enter(self,owner):
        # expanduser针对“~”，翻译为用户目录，resolver绝对值处理，去除.和..这种
        source = Path(self.path).expanduser().resolve()
        if not source.exists():
            GlobalFunction.log("解析错误",f"源文件不存在：{source}")
            self._done = True
            return
        # 建立知识库的目录 parents比如knowledge不存在，设置为true会自动创建
        # exist_ok设置为true，即使目录已经存在也不报错
        output_dir = Path(owner.workspace_path)/"knowledge"/self.name
        output_dir.mkdir(parents=True,exist_ok=True)
        # 构建知识库的第一步解析文档
        self._current_sub_state = WordParseState(source_path=self.path,output_root=str(output_dir))
        owner.add_state(self._current_sub_state)
        self._stage = "parse"
        self._output_dir = output_dir #存下来后面要用

    def Execute(self,owner:StateOwner):
        if self._done:
            owner.remove_state(self)
            return
        # 解析结束
        if self._stage == 'parse':
            if self._current_sub_state not in owner._states:
                self._stage = 'summarize'  #开始总结摘要
                self._chunks = self._current_sub_state._chunks
                self._full_text = self._current_sub_state._full_text
                self._current_sub_state = GenSummaryState(self._chunks)
                owner.add_state(self._current_sub_state)
        # 总结结束
        elif self._stage == 'summarize':
            if self._current_sub_state not in owner._states:
                self._stage='build'
                # 开始构建树
                self._thread = threading.Thread(target=self._run_build,daemon=True,args=(owner,))
                self._thread.start()

    def _chunks_to_json(self,chunks:List[HeadingChunk]):
        """将chunk树-->最终的json树（PageIndex）"""
        jsonChunks = []
        for chunk in chunks:
            children = chunk.children
            node = { #字典字面量
            "node_id" : chunk.node_id,
            "start_index" : chunk.start_index,
            "end_index" : chunk.end_index,
            "summary" : chunk.summary,
            "prefix_summary" : chunk.prefix_summary,
            "level" : chunk.level,
            "number" : chunk.number,
            "title" : chunk.title,
            "children" : self._chunks_to_json(children)
            }
            # 平铺的 但是有索引，谁是谁的子节点。
            jsonChunks.append(node)
        return jsonChunks

    def _run_build(self,owner:StateOwner):
        try:
            chunks = self._chunks
            full_text = self._full_text
            # DFS计算 node_id + 计算字符偏移 index
            self._assign_ids_and_indices(chunks=chunks,full_text=full_text)
            # 最终的知识树
            jsonChunks = self._chunks_to_json(chunks)
            # 落盘json知识树
            output_path = self._output_dir / '知识树.json'
            output_path.write_text(
                json.dumps(jsonChunks,ensure_ascii=False,indent=2),
                encoding="utf-8"
            )
        except Exception as e:
            GlobalFunction.log('构建失败',f"失败信息：{e}")
        finally:
            # 构建结束
            self._done=True
    
    def _assign_ids_and_indices(self, chunks, full_text):
        """DFS遍历树，计算id和index"""
        counter = [0]
        search_from = [0]

        def walk(nodes):
            for chunk in nodes:
                counter[0] += 1
                chunk.node_id = f"{counter[0]:04d}"

                if chunk.content:
                    start = full_text.find(chunk.content, search_from[0])
                    chunk.start_index = start
                    chunk.end_index = start + len(chunk.content)
                    search_from[0] = chunk.end_index

                if chunk.children:
                    walk(chunk.children)

        walk(chunks)
    
    def Exit(self,owner):
        pass

# 根据方案类型自动生成审查清单
class GenCheckListState(BaseState):
    def __init__(self, knowledge_names: list[str], plan_type: str, **kwargs):
        super().__init__(**kwargs)
        self.knowledge_names = knowledge_names
        self.plan_type = plan_type
        self._done = False

    def Enter(self, owner: StateOwner):
        output_dir = Path(owner.workspace_path) / "knowledge" / self.plan_type
        output_dir.mkdir(parents=True, exist_ok=True)
        self._output_dir = output_dir

        self._thread = threading.Thread(
            target=self._run_generate, args=(owner,), daemon=True
        )
        self._thread.start()

    def _run_generate(self, owner):
        try:
            all_trees = []
            for name in self.knowledge_names:
                tree_path = Path(owner.workspace_path) / "knowledge" / name / "知识树.json"
                all_trees.extend(json.loads(tree_path.read_text(encoding="utf-8")))
            self.knowledge_tree = all_trees

            actions = owner.execute_prompt("生成审查清单", self)
            checklist = actions[0]['params']['checklist']
            
            output_path = self._output_dir / '审查清单.json'
            output_path.write_text(
                json.dumps(checklist, ensure_ascii=False, indent=2),
                encoding='utf-8'
            )
        except Exception as e:
            GlobalFunction.log('生成审查清单错误', f'错误为{e}')
        finally:
            self._done = True

    def Execute(self,owner:StateOwner):
        if self._done:
            owner.remove_state(self)
            return
    def Exit(self,owner):
        pass
    
# 逐项合规性检查
class ReviewPlanState(BaseState):
    def __init__(self,plan_path:str,plan_type:str,**kwargs):
        super().__init__(**kwargs)
        self.plan_path = plan_path
        self.plan_type = plan_type
        self._stage = 'init'
        self._done = False
        self._duration = 1000*60*15
        
    def Enter(self,owner:StateOwner):
        source = Path(self.plan_path).expanduser().resolve()
        if not source.exists():
            GlobalFunction.log("解析错误",f"源文件不存在：{source}")
            self._done = True
            return
        
        output_dir = Path(owner.workspace_path)/"review"/self.plan_type
        output_dir.mkdir(exist_ok=True,parents=True)

        self._current_sub_state = WordParseState(source_path=self.plan_path,output_root=str(output_dir))
        owner.add_state(self._current_sub_state)
        self._stage = "parse"
        self._output_dir = output_dir
        
    def Execute(self,owner:StateOwner):
        if self._done == True:
            owner.remove_state(self)
            return
        if self._stage == 'parse':
            if self._current_sub_state not in owner._states:
                self._stage = 'review'
                self._thread = threading.Thread(
                    # args是按位置一一透传（self是类的实例本身，不是args传的）
                    # 元组和元素的区别(owner就是owner本身，args接受任意可迭代的对象)
                    # 如果owner本身可迭代是不会报错，但是可能数量出现问题；并且也不是我们想要的效果
                    target=self._run_review,args=(owner,),daemon=True  #实例调用，已经绑定了self。类名.方法，才需要self参数也写
                )
                self._thread.start()
            
    def Exit(self,owner):
        pass
    
    '''
    daemon线程的异常不会外抛，直接死掉。使用try/catch
    '''
    def _run_review(self,owner):
        try:
            self.plan_text = self._current_sub_state._full_text
            checkList_path = Path(owner.workspace_path)/'knowledge'/self.plan_type/"审查清单.json"
            checkList = json.loads(checkList_path.read_text(encoding='utf-8'))
            result = []
            for check in checkList:
                self.check_item = check['check_item']
                self.check_method = check['check_method']
                self.level = check['level']
                
                actions = owner.execute_prompt('逐条合规检查',self)
                params = actions[0]['params']
                
                record = {**check,**params}
                result.append(record)
                
            result_path = self._output_dir/'审查结果.json'
            result_path.write_text(json.dumps(result,indent=2,ascii=False),encoding='utf-8')
        except Exception as e:
            GlobalFunction.log('审查线程错误',f'错误为{e}')
        finally:
            self._done = True