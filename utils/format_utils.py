import json

def safe_parse_json_and_get_key(x, key, default):
    '''
    安全地解析JSON字符串并获取指定键的值。如果解析失败或键不存在，返回默认值。
    适用于处理可能包含格式错误的JSON字符串。
    例如:
    x = '{"result": {"name": "example", "value": 42}}'
    safe_parse_json_and_get_key(x, "result", {})
    返回: {"name": "example", "value": 42}
    '''
    if isinstance(x, dict):  # 已经是字典
        return x.get(key, default)
    if not isinstance(x, str):  # 不是字符串
        return default
    try:
        data = json.loads(x)
        return data.get(key, default)
    except json.JSONDecodeError:
        # 尝试修复常见错误, 比如字符串被放在了```json```中
        try:
            if x.startswith("```json") and x.endswith("```"):
                x = x[len("```json"): -3].strip()
            data = json.loads(x)
            return data.get(key, default)
        except:
            return default
        
def safe_parse_json(x, default):
    '''
    安全地解析JSON字符串。如果解析失败，返回默认值。
    适用于处理可能包含格式错误的JSON字符串。
    例如:
    x = '{"name": "example", "value": 42}'
    safe_parse_json(x, {})
    返回: {"name": "example", "value": 42}
    '''
    if isinstance(x, dict):  # 已经是字典
        return x
    if not isinstance(x, str):  # 不是字符串
        return default
    try:
        data = json.loads(x)
        return data
    except json.JSONDecodeError:
        # 尝试修复常见错误, 比如字符串被放在了```json```中
        try:
            if x.startswith("```json") and x.endswith("```"):
                x = x[len("```json"): -3].strip()
            data = json.loads(x)
            return data
        except:
            return default