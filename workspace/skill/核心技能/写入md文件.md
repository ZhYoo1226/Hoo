### 技能摘要
```json
{
	"技能名称" : "写入md文件",
	"技能描述" : "将文本内容存储为markdown格式的.md文件。",
	"实现步骤" : "调用save_as_md函数,写入文件。",
	"工具描述" : {
		"function" : "save_as_md",,
		"example" : "save_as_md(text='#写入md文件内容测试',path='./workspace/task/test.md')",
		"params" : [
			{
				"name" : "text",
                "description" : "写入md文件的内容",
				"type" : "string",
				"required" : "true"
			},{
				"name" : "path",
                "description" : "md文件的存储路径",
				"type" : "string",
				"required" : "true"
			}
		]
	},
    "验证用例" : "true",
    "测试用例" : "工具可用性的测试用例"
}
```

怎么让搜索存储到目的地？
