# DataFlow 快速开始指南
<div align="center">
  <img src="https://github.com/user-attachments/assets/3fe636ad-3026-4faf-aa44-c84b8f97a05d">

[![Documents](https://img.shields.io/badge/官方文档-单击此处-brightgreen?logo=read-the-docs)](https://OpenDCAI.github.io/DataFlow-Doc/)
[![](https://img.shields.io/github/license/OpenDCAI/DataFlow)](https://github.com/OpenDCAI/DataFlow/blob/main/LICENSE)
[![](https://img.shields.io/github/stars/OpenDCAI/DataFlow?style=social)](https://github.com/OpenDCAI/DataFlow)
[![](https://img.shields.io/github/issues-raw/OpenDCAI/DataFlow)](https://github.com/OpenDCAI/DataFlow/issues)
[![](https://img.shields.io/github/contributors/OpenDCAI/DataFlow)](https://github.com/OpenDCAI/DataFlow/graphs/contributors)
[![](https://img.shields.io/github/repo-size/OpenDCAI/DataFlow?color=green)](https://github.com/OpenDCAI/DataFlow)

<!-- [![](https://img.shields.io/github/last-commit/OpenDCAI/DataFlow)](https://github.com/OpenDCAI/DataFlow/commits/main/) -->

🎉 如果你认可我们的项目，欢迎在 GitHub 上点个 ⭐ Star，关注项目最新进展。
</div>

## 安装步骤

### 1. 创建并激活 Conda 环境

```bash
conda create -n dataflow python=3.10
conda activate dataflow
```

### 2. 克隆代码仓库 (https://github.com/OpenDCAI/DataFlow)

```bash
git clone git@github.com:OpenDCAI/DataFlow.git
pip install open-dataflow
git clone git@git.dp.tech:dataflow-dp/dataflow-dp.git
```

**注意，`cof_extract_pipeline`完全是`alloy_extract_pipeline`的简单版本，因此以下着重介绍`alloy_extract_pipeline`**

## 配置步骤

### 4. 设置 API Key 环境变量

```bash
cd dataflow-dp
export DF_API_KEY="your_openai_api_key_here"
```


### 5. 修改配置文件

在运行示例之前，需要修改 `pipelines/alloy_extract_pipeline.py` 中的配置参数

例如在`alloy_extract_pipeline.py`中，修改
```python
model = ExtractAlloy(entry_file_name="./data/AlloyExtractPipeline/alloy_papers_short.jsonl", mode='MD', max_chunk_len=3200)

```
其中第一个参数`entry_file_name`是输入文件jsonl，每一行需要准备好`doi`, `content`, `figure_components`, 准备的示例代码如下：
```python
with open("alloy_papers.jsonl", "w") as f:
        for root, _, files in os.walk("../Alloy-test"):
            for fname in files:
                if fname.lower().endswith(".json"):
                    paper = os.path.join(root, fname)
                    paper_data = json.load(open(paper, "r"))
                    f.write(json.dumps({"doi":paper_data["token"],
                                        "content": paper_data["content"],
                                        "figure_components": extract_figure_components(paper_data)}) + "\n")
```
这部分代码在主函数的注释当中，也可以直接修改使用。

第二个参数`mode`是选择`json_schema`，因为目前金属提取第二个阶段，也就是提取信息的阶段，有三类信息要提取，`user_prompt`相同，只有输出的json字段不同。

第三个参数`max_chunk_len`是输入token的最大长度。目前测试时调的3200这个值是比较小的，可以适当开大一些，比如16000或32000.

## 运行示例
### 6. 运行示例

```bash
python pipelines/alloy_extract_pipeline.py
```

目前的输出会在`../alloy_output`的step最大的一个jsonl文件中。其中`materials_name_list`项是所有的金属名称，`alloys_{xx mode}_info`是提取出来的金属信息。

## 开发指南
### 7. Pipeline解析
目前的pipeline包括5个算子，`prompt_generator_1`, `parse_alloys`, `get_alloy_names`, `prompt_generator_2`, `parse_alloys_info`.
1. `prompt_generator_1`会提取`content`中的金属名字，存入`alloys`当中。
2. `parse_alloys`会对`alloys`原地操作，把大模型的字符串输出，转成json object，并提取alloys列表。
例如
```
{\"alloys\": [\"a\",\"b\"]}
```
变成
```json
["a","b"]
```
3. `get_alloy_names`提取alloys中每一项的alloy_name, 并去重，然后得到一个金属名字的列表存到`materials_name_list`当中。
4. `prompt_generator_2`根据`materials_name_list`中的金属，提取`content`中的金属信息，存入`alloys_{self.mode}_info`当中。
5. `parse_alloys_info`会对`alloys_{self.mode}_info`原地操作，把大模型的字符串输出，转成json object，并提取alloys info列表。

### 8. Prompt修改
目前的prompt示例在`alloy.py`当中。其中`AlloyNameExtractPrompt`用于提取名字，`AlloyInfoExtractPrompt`用于提取信息。

每个prompt包含`build_system_prompt`，`build_prompt`和`build_json_schema`。system_prompt可以暂时留空。prompt用于给LLM提供指令，json_schema用于控制LLM输出的格式。

#### 基本Prompt
`AlloyNameExtractPrompt`比较简单，准备好prompt和json_schema就可以，例如
```python
def build_prompt(self, **kwargs) -> str:
        prompt = """You are an expert in high-entropy alloys and materials science.
Your task is to ...
            """
        return prompt
    
    def build_json_schema(self) -> dict:
        json_schema = json.load(open("./schemas/alloy_schemas/basic_schema.json"))
        return json_schema
```
其中json转json_schema可以用https://transform.tools/json-to-json-schema

在pipeline中，只需要更改
```python
self.prompt_1 = AlloyNameExtractPrompt()
self.prompt_generator_1 = ChunkedPromptedGenerator(
    llm_serving = self.llm_serving, 
    prompt_template=self.prompt_1,
    json_schema=self.prompt_1.build_json_schema(),
    max_chunk_len=max_chunk_len
)
```
里面prompt的名字就可以使用。

而输入（论文具体内容）和输出（金属名字）则是通过
```python
self.prompt_generator_1.run(
            storage = self.storage.step(),
            input_key = "content",
            output_key = "alloys"
        )
```
里面的`input_key`和`output_key`进行控制的。
例如输入（就是最开始的entry_file）
```json
{"content": "foo"}
```
输出
```json
{"content": "foo", "alloys": "bar"}
```
------
`AlloyInfoExtractPrompt`会复杂一些，主要是需要对prompt进行参数传递（这里是`materials_name_list`），以及json_schema的选择。
#### 往Prompt中传参
```python
    def build_prompt(self, **kwargs) -> str:
        materials_name_list = kwargs.get("materials_name_list", [])
        prompt = f"""
      You are an expert in high-entropy alloys and materials science.
      Your task is to extract structured materials information from the given scientific text and organize it according to the specified schema.

      ### Instructions:
      Select alloy materials only from the {materials_name_list} provided.
      ...
    """
       
        return prompt
```
可以看到，`materials_name_list`通过kwargs被传入了`build_prompt`当中进行使用。
这个在pipeline中是通过`input_aux_keys`来指定的，如下：
```python
self.prompt_generator_2.run(
            storage = self.storage.step(),
            input_key = "content",
            output_key = f"alloys_{self.mode}_info",
            input_aux_keys = ["materials_name_list"]
        )
```
`input_aux_keys`是一个列表，其中每一项对应输入文件的一个key，这些key对应的项会被全部传入`build_prompt`来使用。

#### json_schema选择
```python
def build_json_schema(self, mode: Literal['experimental','DFT','MD']) -> dict:
        if mode == "experimental":
            json_schema = json.load(open("./schemas/alloy_schemas/experimental_schema.json"))
        elif mode == "DFT":
            json_schema = json.load(open("./schemas/alloy_schemas/DFT.json"))
        elif mode == "MD":
            json_schema = json.load(open("./schemas/alloy_schemas/MD.json"))
        else:
            raise ValueError(f"Unknown mode: {mode}")
                
        return json_schema
```
可以看到，这里分了三种模式。用这种方法可以实现相同的prompt，但是不同的json输出内容。

### 9. Pandas Operator
Pipeline中的`parse_alloys`, `get_alloy_names`, `parse_alloys_info`都是用到`PandasOperator`对dataframe进行操作（是的，dataflow读入文件后storage形态都是pandas dataframe）. `PandasOperator`可以接受一个函数列表。目前代码中为了偷懒，用的都是lambda函数。例如：
```python
# 提取alloys中每一项的alloy_name, 并去重，然后得到一个列表
self.get_alloy_names = PandasOperator(
    [
        lambda df: df.assign(
            materials_name_list=df["alloys"].apply(
                lambda alloys: list(
                    {
                        alloy.get("alloy_name", "").strip()
                        for alloy in alloys
                        if alloy.get("alloy_name", "").strip() != ""
                    }
                )
            )
        ),
    ]
)
```
非常丑陋，**建议用gpt写**，因为我也是用gpt写的哈哈哈哈哈哈

`PandasOperator`详细描述如下：
```python
"该算子支持通过多个自定义函数对 DataFrame 进行任意操作（如添加列、重命名、排序等）。\n\n"
"每个函数（通常为 lambda 表达式）接受一个 DataFrame 并返回一个修改后的 DataFrame。\n\n"
"输入参数：\n"
"- process_fn：一个函数列表，每个函数形式为 lambda df: ...，"
"必须返回一个 DataFrame。\n\n"
"示例：\n"
"  - lambda df: df.assign(score2=df['score'] * 2)\n"
"  - lambda df: df.sort_values('score', ascending=False)"
```
