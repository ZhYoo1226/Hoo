import json


def parse_llm_heading(llm_output: str, lev: str = "###", heading: str = "TASK_OUTPUT") -> str:
    """Extract the text between the heading and the next second level heading in the LLM output."""
    if heading not in llm_output:
        raise ValueError(
            f"Could not find heading {heading} in the LLM output:\n{llm_output}\n. Likely this is caused by a too long response and limited context length. If so, try to shorten the message and exclude objects which aren't needed for the task"
        )
    heading_str = (
        # llm_output.split(heading)[1].split(f"\n{lev} ")[0].strip()
        # 不需要找到下一个lev，直接取到文件结束。直接分析所有
        llm_output.split(heading)[1].strip()
    )  # Get the text between the heading and the next heading
    return heading_str


def parse_llm_json_blocks(heading_str: str):
    """Combine the inside of blocks from the heading string into a single string."""

    # if "```json" in heading_str:
    #     heading_str = heading_str.replace("```json", "```")
    #
    # if "```markdown" in heading_str:
    #     heading_str = heading_str.replace("```markdown", "```")
    # 第一个是text，直接返回文本.
    heading_str = heading_str.strip()
    if heading_str.startswith("```text"):
        return [{'text': heading_str[7:]}]
    elif heading_str.startswith("```markdown"):
        return [{'text': heading_str[11:]}]

    # 必须是json切割
    possible_blocks = heading_str.split("```")
    blocks = []
    for i in range(1, len(possible_blocks), 2):
        possible_blocks[i] = possible_blocks[i].replace("\n\n", "\n").strip()
        # 如果是以json字符串开头，则按照json解析
        if possible_blocks[i].startswith("json\n"):
            blocks.append(json.loads(possible_blocks[i][5:]))
        else:
            # 提取blocks[i]中的第一行,可能一直是文本
            prop_name = possible_blocks[i].split("\n")[0]
            if prop_name:
                last_block = {prop_name.strip(): possible_blocks[i][len(prop_name):]}
                blocks.append(last_block)
    return blocks
