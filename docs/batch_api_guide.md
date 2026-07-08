# 批量调用 ComfyUI API 指南

本文档说明如何通过 **Workflow**、**Data Template** 和**用户参数**三者配合，实现对 ComfyUI 的批量（batch）调用。

---

## 概念总览

```
┌─────────────────┐
│  Workflow JSON   │  完整的 ComfyUI 工作流图（节点 + 连线 + 默认值）
└────────┬────────┘
         │
┌────────▼────────┐
│  Data Template   │  从 Workflow 中圈定的「可调参数」列表 + 默认值
└────────┬────────┘
         │
┌────────▼────────┐
│  Batch 数据行    │  每行一组 {参数id → 实际值}，覆盖 Workflow 中对应字段
└────────┬────────┘
         │
         ▼
   POST /api/batch   →  逐行注入 → 调用 ComfyUI → 收集输出
```

**一句话概括：Workflow 提供图结构，Template 标注哪些字段可调，Batch 数据行提供每次运行的实际值。**

---

## 1. Workflow —— 工作流文件

### 1.1 格式

支持两种 JSON 格式，服务端自动识别和转换：

| 格式 | 来源 | 特征 |
|------|------|------|
| **API 格式** | ComfyUI → Save (API Format) | 顶层键为节点 ID（`"7"`, `"323"`），每个节点含 `class_type` 和 `inputs` |
| **UI 格式** | ComfyUI → Save | 含 `nodes` 数组和 `links` 数组 |

建议优先使用 **API 格式**，结构更简单、可读性更好。

### 1.2 API 格式示例

```json
{
  "7": {
    "inputs": {
      "image": "example.jpg"
    },
    "class_type": "LoadImage",
    "_meta": { "title": "Load Image" }
  },
  "3": {
    "inputs": {
      "text": "a beautiful photo",
      "clip": ["4", 0]
    },
    "class_type": "CLIPTextEncode",
    "_meta": { "title": "Positive Prompt" }
  }
}
```

其中 `"clip": ["4", 0]` 是节点连线（二元列表，第一个元素为字符串），不会被扫描为可调参数。

### 1.3 存放位置

- 路径：`data/workflows/<名称>.json`
- 通过 Web UI 保存：点击 **Save Workflow** → `POST /api/workflows`
- 直接放文件亦可

---

## 2. Data Template —— 数据模板

### 2.1 作用

Data Template **不包含工作流本身**，只记录：
- 哪些 `node_id.field` 需要在运行时替换
- 每个字段的默认值、类型、别名
- 是否为随机种子字段

### 2.2 文件格式

```json
{
  "name": "漫画转真人v2_data_template",
  "variables": [
    {
      "id": "323.image",
      "node_id": "323",
      "node_title": "Load Image",
      "field": "image",
      "alias": "image",
      "type": "text",
      "default": "Himawari Uzumaki.jpeg"
    },
    {
      "id": "344.replace",
      "node_id": "344",
      "node_title": "Replace",
      "field": "replace",
      "alias": "replace",
      "type": "text",
      "default": "8"
    }
  ]
}
```

### 2.3 字段说明

| 字段 | 说明 |
|------|------|
| `id` | `{node_id}.{field}` 格式，用于注入时定位到具体节点的具体输入 |
| `node_id` / `node_title` | 节点信息（展示用） |
| `field` | 节点 `inputs` 中的字段名 |
| `alias` | 在 Run Mode 界面显示的别名（注入时仍使用 `id`） |
| `type` | `text` / `number` / `boolean` |
| `default` | 默认值，用于初始化批量数据行 |
| `random_seed` | 可选，`true` 表示该字段使用随机种子（值为 `__random_seed__`） |

### 2.4 Template 的生成

1. Web UI 中 **Scan Workflow** → 调用 `POST /api/scan` → 返回所有可调参数列表
2. 勾选需要的参数 → 填写默认值、别名、种子标记
3. 点击 **Save Data Template** → `POST /api/templates`
4. 存放于 `data/templates/<名称>.json`

---

## 3. 批量调用流程

### 3.1 整体流程图

```
用户准备 Workflow + Template
          │
          ▼
   构建 batch 数据行列表
   [
     {"323.image": "photo1.jpg", "344.replace": "8"},
     {"323.image": "photo2.jpg", "344.replace": "16"},
     ...
   ]
          │
          ▼
   POST /api/batch
   {
     "workflow": { ... },
     "workflow_name": "漫画转真人v6",
     "batch": [ ... ],
     "server_address": "127.0.0.1:8188"  // 可选
   }
          │
          ▼
   服务端逐行处理：
   ┌─────────────────────────────────────┐
   │ 1. ensure_api_format(workflow)      │
   │ 2. 文件夹展开（若值为目录路径）     │
   │ 3. for each row:                    │
   │    a. 上传本地文件到 ComfyUI        │
   │    b. resolve_random_seeds()        │
   │    c. inject_variables(workflow, row)│
   │    d. 调用 ComfyUI /prompt          │
   │    e. 通过 WebSocket 等待完成       │
   │    f. 收集输出（图片/音频/文本）    │
   └─────────────────────────────────────┘
          │
          ▼
   返回结果 + 保存到 data/outputs/{job_id}/
```

### 3.2 参数覆盖机制

模板中的 `default` 仅在 Web UI 中初始化表单行。实际发到服务端的 batch 数据行已经是**最终值**。覆盖链路：

```
Template default → Web UI 初始行 → 用户编辑 → 最终 batch 数据行 → 服务端注入
```

注入核心逻辑（`WorkflowManager.inject_variables`）：

- 键含 `.`（如 `323.image`）：直接设置 `workflow["323"]["inputs"]["image"] = value`
- 自动类型转换：字符串 `"true"` / `"false"` 转布尔值，纯数字字符串转 `int` / `float`

### 3.3 随机种子处理

对于标记了 `random_seed: true` 的字段：
- 批量数据中值为 `__random_seed__`
- 服务端在注入前将其替换为 `[0, 1125899906842624]` 区间内的随机整数
- 每个 batch job 独立生成，确保每次运行结果不同

### 3.4 本地文件自动上传

batch 数据行中的值如果是服务器上的本地文件路径（`os.path.isfile` 为真），服务端会自动：
1. 根据扩展名判断类型（图片 / 音频 / 视频）
2. 上传到 ComfyUI 的对应上传端点（`/upload/image` 或 `/upload/audio`）
3. 将 batch 行中的路径替换为上传后的文件名

支持的媒体格式：
- 图片：`.png`, `.jpg`, `.jpeg`, `.webp`, `.bmp`, `.gif`
- 音频：`.mp3`, `.wav`, `.flac`, `.ogg`, `.aac`, `.m4a`
- 视频：`.mp4`, `.webm`, `.mov`, `.avi`, `.mkv`

### 3.5 文件夹批量展开

如果 batch 行中某个参数的值是**目录路径**，服务端会自动展开：
- 扫描目录下所有媒体文件（按文件名排序）
- 以第一个文件夹变量为主驱动，每个文件生成一个 job
- 其他文件夹变量按索引对齐（不足则复用最后一个文件）

---

## 4. API 接口参考

### 4.1 批量运行

```
POST /api/batch
Content-Type: application/json

{
  "workflow": { ... },          // 完整 workflow JSON（API 或 UI 格式）
  "workflow_name": "my_wf",     // 用于输出文件命名（可选，默认 "workflow"）
  "batch": [                    // 批量数据行列表
    { "323.image": "photo1.jpg", "363.seed": "__random_seed__" },
    { "323.image": "photo2.jpg", "363.seed": "__random_seed__" }
  ],
  "server_address": "host:port", // ComfyUI 服务器地址（可选，默认 COMFY_BASE_URL）
  "save_outputs": true           // 是否保存输出文件（可选，默认 true）
}
```

**响应：**
```json
{
  "job_id": "batch_1713000000_a1b2c3",
  "total": 2,
  "completed": 2,
  "cancelled": false,
  "results": [
    {
      "index": 0,
      "inputs": { "323.image": "upload_xxx.jpg" },
      "outputs": [
        { "node_id": "10", "type": "image", "filename": "photo1_my_wf_0.png", "url": "/api/outputs/batch_.../photo1_my_wf_0.png" }
      ]
    }
  ]
}
```

### 4.2 取消批量任务

```
POST /api/batch/{job_id}/cancel
```

### 4.3 扫描工作流参数

```
POST /api/scan
Content-Type: application/json

{ ... workflow JSON ... }
```

返回所有可调标量输入列表，每项包含 `id`（`node_id.field`）、`type`、`value` 等。

### 4.4 其他相关接口

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/workflows` | 列出已保存的 workflow |
| GET | `/api/workflows/{name}` | 获取 workflow JSON |
| POST | `/api/workflows` | 保存 workflow |
| GET | `/api/templates` | 列出已保存的 template |
| GET | `/api/templates/{name}` | 获取 template JSON |
| POST | `/api/templates` | 保存 template |
| POST | `/api/upload` | 上传文件到服务端（返回本地路径） |
| GET | `/api/outputs` | 列出所有批量任务输出 |
| GET | `/api/outputs/{job_id}` | 获取某次任务的输出列表 |
| GET | `/api/outputs/{job_id}/{filename}` | 下载具体输出文件 |
| GET | `/api/batch-parameters/{job_id}` | 获取任务参数（可用于重跑） |

---

## 5. 完整使用示例

### 5.1 通过 Web UI

1. **准备 Workflow**：在 ComfyUI 中导出 API 格式 JSON，粘贴或上传到 Web UI
2. **扫描参数**：点击 Scan Workflow，勾选要调整的字段
3. **保存**：分别 Save Workflow 和 Save Data Template
4. **切换到 Run Mode**：选择已保存的 Workflow 和 Template
5. **填写批量数据**：每行一组参数值（支持文件上传、文件夹选择、Range 展开）
6. **运行**：点击 Run Batch，等待逐行执行完成，查看输出

### 5.2 直接调用 API

```python
import requests
import json

workflow = json.load(open("data/workflows/漫画转真人v6.json"))

batch_data = [
    {"323.image": "/path/to/photo1.jpg", "344.replace": "8", "363.seed": "__random_seed__"},
    {"323.image": "/path/to/photo2.jpg", "344.replace": "16", "363.seed": "__random_seed__"},
    {"323.image": "/path/to/photo3.jpg", "344.replace": "8", "363.seed": "__random_seed__"},
]

resp = requests.post("http://localhost:8931/api/batch", json={
    "workflow": workflow,
    "workflow_name": "漫画转真人v6",
    "batch": batch_data,
})

result = resp.json()
print(f"Job ID: {result['job_id']}")
print(f"Completed: {result['completed']}/{result['total']}")

for r in result["results"]:
    for out in r["outputs"]:
        print(f"  Output: http://localhost:8931{out['url']}")
```

### 5.3 文件夹批量

将文件夹路径作为参数值，服务端自动展开为多个 job：

```python
batch_data = [
    {"323.image": "/path/to/image_folder/", "344.replace": "8"}
]
```

如果 `/path/to/image_folder/` 下有 10 张图片，会自动展开为 10 个独立的 batch job。

---

## 6. 配置

| 配置项 | 说明 | 默认值 |
|--------|------|--------|
| `COMFY_BASE_URL` 环境变量 | ComfyUI 服务器地址（`host:port`，不带 `http://`） | `127.0.0.1:8188` |
| 服务端口 | 本项目 Web 服务监听端口 | `8931` |
| 数据目录 | `data/workflows/`、`data/templates/`、`data/outputs/` | 项目根目录下 |

启动服务：

```bash
python scripts/server.py
```

确保 ComfyUI 已在 `COMFY_BASE_URL` 指定的地址上运行。
