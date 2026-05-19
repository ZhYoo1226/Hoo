import os

from common import GlobalFunction, ExcelEntity
from state import BaseState


class ParseExcelState(BaseState):
    """
    解析Excel文件状态
    """
    stateName = "ParseExcelState"

    # 如何能够找到xlsx文件
    def __init__(self, xlsx_path: str, **kwargs):
        super().__init__(**kwargs)
        self.xlsx_path = xlsx_path
        self.src_doc_name = os.path.basename(xlsx_path)
        self.parse_path = GlobalFunction.file_to_parse_path(xlsx_path)
        os.makedirs(self.parse_path, exist_ok=True)

    def Enter(self, owner):
        # 映射文件路径
        owner._uuid_maps[self._uuid] = self.xlsx_path

    def file_uuid(self):
        return self._uuid

    def preview_file_content(self):
        """
        预览文件内容
        """
        preview_path = GlobalFunction.file_to_preview_text_path(self.xlsx_path)
        if os.path.exists(preview_path):
            with open(preview_path, 'r', encoding='utf-8') as f:
                return f.read()
        return ""

    def parse_sheet(self):
        try:
            # 打开Excel文件
            excel_entity = ExcelEntity(self.xlsx_path)
            doc_text_path = GlobalFunction.file_to_doc_text_path(self.xlsx_path)
            os.makedirs(doc_text_path, exist_ok=True)

            # 生成预览文件
            preview_txt_file = GlobalFunction.file_to_preview_text_path(self.xlsx_path)

            # 第一次清空写
            with open(preview_txt_file, 'w', encoding='utf-8') as preview_f:
                preview_f.write(f"# {self.src_doc_name}\n\n")
                preview_f.write("## 所有Sheet内容汇总预览\n\n")
                # 遍历每个sheet，读取对应的md文件内容并入总文件

            # 遍历每个sheet
            for sheet_name, rows_data in excel_entity.read_sheets(line=6):
                # 读取前6行数据
                # 生成输出文件名
                output_path = os.path.join(doc_text_path, f"{sheet_name}_预览.md")
                # 写入markdown表格
                content = excel_entity.write_sheet_markdown(sheet_name, rows_data, output_path)
                # 追加到总预览文件
                with open(preview_txt_file, 'a', encoding='utf-8') as preview_f:
                    preview_f.writelines(content)

            # 生成全量文件
            full_txt_file = GlobalFunction.file_to_full_text_path(self.xlsx_path)
            with open(full_txt_file, 'w', encoding='utf-8') as full_f:
                full_f.write(f"文件名称: {self.src_doc_name}\n\n")
            # 遍历每个sheet,读取所有的数据
            for sheet_name, rows_data in excel_entity.read_sheets():
                # 生成输出文件名
                output_path = os.path.join(doc_text_path, f"{sheet_name}.md")
                # 写入markdown表格
                content = excel_entity.write_sheet_markdown(sheet_name, rows_data, output_path)
                # 追加到总预览文件
                with open(full_txt_file, 'a', encoding='utf-8') as full_f:
                    full_f.writelines(content)

        except Exception as e:
            print(f"解析Excel出错: {str(e)}")

    def Execute(self, owner):
        self.parse_sheet()
        # 分析表头
        action_json = owner.execute_prompt("推测数据表头字段", state=self)
        # 执行动作
        owner.execute_action(action_json)
        owner.remove_state(self)

    def Exit(self, owner):
        pass


class UpdateTableHeadState(BaseState):
    """
    更新表格头状态
    """
    stateName = "UpdateTableHeadState"

    def __init__(self,
                 headers: list,  # 推测数据表头字段提示词返回的params属性
                 uuid: str,  # 文件uuid
                 **kwargs):
        super().__init__()
        self.headers = headers
        self.file_uuid = uuid

    def Enter(self, owner):
        print(f"Enter UpdateTableHeadState【uuid={self.file_uuid}】: {self.headers}")
        self.xlsx_path = owner._uuid_maps[self.file_uuid]
        print(f"UpdateTableHeadState xlsx_path: {self.xlsx_path}")
        self.doc_text_path = GlobalFunction.file_to_doc_text_path(self.xlsx_path)

    def Execute(self, owner):
        if not os.path.exists(self.doc_text_path):
            owner.remove_state(self)
            return False
        # 获取文件名,去掉后缀 self.src_doc_name,

        # 直接写入分析文件目录下的header.md文件
        for data in self.headers:
            sheet_name = data["sheet_name"]
            table_head = data["table_head"]
            # 打开Excel文件
            header_path = os.path.join(self.doc_text_path, f"{sheet_name}_表头.md")
            with open(header_path, 'w', encoding='utf-8') as f:
                # 按行输出
                f.write("\n".join(table_head))
                # 输出json
                # f.write(f"# {sheet_name} 表头\n\n")
                # f.write(f"{json.dumps(data, ensure_ascii=True, indent=2)}")

        owner.remove_state(self)
        pass

    def Exit(self, owner):
        pass
