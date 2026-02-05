# ComfyUI Batch Client 使用指南

本文档详细说明如何一步步使用 ComfyUI Batch Client 项目。

## 目录

- [环境准备](#环境准备)
- [快速启动](#快速启动)
- [Web UI 使用方法](#web-ui-使用方法)
  - [模式一：Data Template Builder（数据模板构建器）](#模式一data-template-builder数据模板构建器)
  - [模式二：Run Mode（批量运行模式）](#模式二run-mode批量运行模式)
- [CLI 命令行使用](#cli-命令行使用)
- [数据文件说明](#数据文件说明)
- [常见问题](#常见问题)

---

## 环境准备

### 前提条件

1. **Python 3.8+** 已安装
2. **ComfyUI** 服务器正在运行（本地或远程）
3. 已准备好 ComfyUI 的 `workflow.json` 文件

### 安装依赖

```bash
# 方式一：直接安装
pip install -r requirements.txt

# 方式二：使用启动脚本（会自动创建虚拟环境）
./start.sh
```

### 配置 ComfyUI 服务器地址

默认服务器地址为 `127.0.0.1:8188`。如需修改，有两种方式：

```bash
# 方式一：设置环境变量
export COMFY_BASE_URL="192.168.1.21:8188"

# 方式二：在 Web UI 右上角直接修改
```

---

## 快速启动

### 方式一：使用启动脚本（推荐）

```bash
./start.sh
```

脚本会自动：
1. 创建 Python 虚拟环境
2. 安装依赖
3. 创建必要的数据目录
4. 启动 Web 服务器

### 方式二：手动启动

```bash
# 安装依赖
pip install -r requirements.txt

# 启动服务器
python scripts/server.py
```

启动后打开浏览器访问：**http://127.0.0.1:8000**

---

## Web UI 使用方法

Web UI 提供两种使用模式：

### 模式一：Data Template Builder（数据模板构建器）

用于**首次配置工作流**，选择需要暴露的参数，创建可复用的模板。

#### 步骤 1：加载工作流

有三种方式加载工作流：

1. **从已保存的工作流加载**
   - 在下拉菜单中选择已保存的工作流
   - 点击 "Load" 按钮

2. **拖拽上传**
   - 将 `workflow.json` 文件拖拽到上传区域

3. **粘贴 JSON**
   - 直接在文本框中粘贴 workflow JSON 内容

加载后点击 **"Scan Workflow"** 按钮。

> **提示**：支持两种格式的 workflow：
> - ComfyUI "Save" 按钮导出的格式（包含 nodes 和 links）
> - ComfyUI "Save (API Format)" 导出的 API 格式

#### 步骤 2：选择变量

系统会扫描工作流中所有可配置的参数，显示在表格中：

| 列名 | 说明 |
|------|------|
| 复选框 | 勾选要暴露为变量的参数 |
| Node | 节点编号和名称 |
| Field | 参数字段名 |
| Value | 当前默认值 |
| Alias | 变量别名（可自定义） |

- 使用顶部复选框可全选/全不选
- 在 Alias 列可以给变量起一个更友好的名称
- 选择完成后点击 **"Continue"**

#### 步骤 3：配置与运行

在此步骤可以：

1. **填写变量值** - 为每个选中的变量填写具体值
2. **保存工作流** - 点击 "💾 Save Workflow" 保存工作流供以后使用
3. **保存数据模板** - 点击 "📋 Save Data Template" 保存变量配置模板
4. **测试运行** - 点击 "🧪 Test Run" 执行单次测试

#### 步骤 4：查看结果

运行完成后显示生成的图片结果。可以：
- 点击 "Test Again" 使用不同参数再次测试
- 点击 "Start Over" 重新开始

---

### 模式二：Run Mode（批量运行模式）

用于**批量执行**已配置好的工作流。

#### 步骤 1：选择工作流和模板

1. **选择工作流** - 从下拉菜单选择之前保存的工作流
2. **选择模板**（可选）- 选择之前保存的数据模板
3. 点击 **"Load"** 加载配置

#### 步骤 2：编辑批量数据

加载后显示批量数据编辑器：

- 每一行代表一次运行的参数
- 点击 **"+ Add Row"** 添加新行
- 点击行末的 🗑️ 删除该行

**文件类型参数支持：**
- 直接输入服务器上的文件路径
- 输入本地文件夹路径（系统会自动展开为多行）
- 点击 📁 按钮上传本地文件

#### 步骤 3：运行批量任务

1. 点击 **"🚀 Run Batch"** 开始批量执行
2. 执行过程中可点击 **"⏹️ Stop"** 中止任务
3. 结果实时显示在下方画廊中

#### 输出结果

所有输出保存在 `data/outputs/` 目录，按任务 ID 组织：

```
data/outputs/
├── batch_1234567890_abc123/
│   ├── image1_workflow.png
│   ├── image2_workflow.png
│   └── ...
└── ...
```

---

## CLI 命令行使用

除了 Web UI，也可以使用命令行工具。

### 提取变量

查看工作流中的变量定义：

```bash
python scripts/run.py extract-vars workflow.json
```

### 单次运行

```bash
python scripts/run.py run workflow.json \
    --set "7.image=/path/to/image.png" \
    --set "3.text=a beautiful cat" \
    --out ./outputs
```

### 使用模板运行

```bash
python scripts/run.py run --template my_template.json \
    --set "7.image=/path/to/image.png" \
    --out ./outputs
```

### 批量运行

创建一个 batch.json 文件：

```json
[
    {"7.image": "/path/to/image1.png", "3.text": "prompt 1"},
    {"7.image": "/path/to/image2.png", "3.text": "prompt 2"},
    {"7.image": "/path/to/image3.png", "3.text": "prompt 3"}
]
```

然后执行：

```bash
python scripts/run.py run --template my_template.json \
    --batch batch.json \
    --out ./batch_outputs
```

### 文件夹批量处理

如果变量值是一个文件夹路径，系统会自动展开为多个任务：

```bash
python scripts/run.py run --template my_template.json \
    --set "7.image=/path/to/images_folder" \
    --out ./outputs
```

---

## 数据文件说明

### 目录结构

```
data/
├── workflows/      # 保存的工作流文件
│   └── my_workflow.json
├── templates/      # 保存的数据模板
│   └── my_template.json
├── outputs/        # 批量任务输出
│   └── batch_xxx/
│       └── output_xxx.png
└── uploads/        # 上传的临时文件
```

### Workflow 文件格式

保存的 workflow 是 ComfyUI 的原始 JSON 格式，可以直接在 ComfyUI 中加载使用。

### Template 文件格式

```json
{
    "name": "模板名称",
    "variables": [
        {
            "id": "7.image",
            "node_id": "7",
            "node_title": "Load Image",
            "field": "image",
            "alias": "输入图片",
            "type": "image",
            "default": ""
        },
        {
            "id": "3.text",
            "node_id": "3",
            "node_title": "CLIP Text Encode",
            "field": "text",
            "alias": "正向提示词",
            "type": "text",
            "default": "a beautiful landscape"
        }
    ]
}
```

---

## 常见问题

### Q: 连接不上 ComfyUI 服务器？

1. 确认 ComfyUI 服务器正在运行
2. 检查服务器地址是否正确（默认为 `127.0.0.1:8188`）
3. 如果 ComfyUI 在其他机器上，确保网络可达
4. 查看 Web UI 右上角的连接状态指示器

### Q: 工作流格式不正确？

本项目支持两种格式：

1. **UI 格式**：ComfyUI 界面点击 "Save" 导出的格式（包含 `nodes` 和 `links` 数组）
2. **API 格式**：ComfyUI 界面点击 "Save (API Format)" 导出的格式

两种格式都会自动识别并转换。

### Q: 图片上传失败？

1. 确保图片格式为 PNG、JPG、JPEG、WebP、BMP 或 GIF
2. 确保 ComfyUI 服务器的 input 目录有写入权限
3. 检查文件大小是否过大

### Q: 批量任务中途失败？

- 已完成的任务结果会保留在 `data/outputs/` 目录
- 可以点击 "Stop" 按钮中止后续任务
- 查看终端日志了解具体错误原因

### Q: 如何使用自定义变量语法？

支持在 workflow 中使用 `**变量名[类型]**` 语法定义变量：

```json
{
    "inputs": {
        "text": "**prompt[text]**",
        "seed": "**seed[number]**"
    }
}
```

### Q: 结果图片保存在哪里？

- 单次运行：结果在 Web UI 中显示，不自动保存
- 批量运行：保存在 `data/outputs/batch_xxx/` 目录下

---

## 技术支持

如有问题，请查看项目 README 或提交 Issue。
