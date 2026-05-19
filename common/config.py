import yaml

'''
统一管理配置文件
'''
g_yaml_config = None

with open("config.yaml", "r", encoding="utf-8") as f:
    g_yaml_config = yaml.safe_load(f)
