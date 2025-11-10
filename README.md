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

## 配置步骤

### 4. 设置 API Key 环境变量

```bash
cd dataflow-dp
export DF_API_KEY="your_openai_api_key_here"
```


### 5. 修改配置文件

在运行示例之前，需要修改 `pipelines/xxxxxxxxx_pipeline.py` 中的配置参数

## 运行示例
### 6. 运行示例

```bash
python pipelines/xxxxxx_pipeline.py
```
