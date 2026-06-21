# Workflow 解析、保存与 Data Template 保存 —— UI 流程设计

本文档描述 **Setup Mode**（界面左侧第一个 Tab）中，从加载 Workflow 到保存 Workflow / Data Template 的完整 UI 交互设计。

---

## 整体流程

```
Step 1: Load Workflow
        │
        │  ← Scan Workflow（POST /api/scan）
        ▼
Step 2: Select Variables
        │
        │  ← Continue
        ▼
Step 3: Configure & Run
        │
        ├──→ 💾 Save Workflow     （POST /api/workflows）
        ├──→ 📋 Save Data Template（POST /api/templates）
        └──→ 🧪 Test Run          （POST /api/run）
                │
                ▼
Step 4: Results
        ├──→ 💾 Save Workflow
        └──→ 📋 Save Data Template
```

四个 Step 对应四张卡片（`#step1` ~ `#step4`），同一时间只显示一张，通过 `show()`/`hide()` 切换。每一步都可以点 **Back** 回到上一步。

---

## Step 1 — Load Workflow

### 界面元素

| 元素 | 说明 |
|------|------|
| **Saved Workflow 下拉框** | 从 `GET /api/workflows` 拉取已保存列表，选中后点 **Load** 回填到文本框 |
| **"— OR —" 分隔线** | 提示用户还可手动导入 |
| **拖拽区域 (Drop Zone)** | 拖放或点击选择 `.json` 文件，`FileReader` 读取后填入文本框 |
| **Workflow JSON 文本框** | 可直接粘贴 JSON；文本变化时自动启用/禁用 Scan 按钮 |
| **Scan Workflow 按钮** | 触发解析流程（见下节） |

### Scan 逻辑

1. 前端将文本框内容 `JSON.parse()` 为对象，赋值给全局 `currentWorkflow`。
2. `POST /api/scan`，body 为原始 JSON 字符串。
3. 服务端调用 `WorkflowManager.scan_possible_inputs()`：
   - 先 `ensure_api_format()`——如果是 UI 格式（含 `nodes`/`links`）则自动转为 API 格式。
   - 遍历每个节点的 `inputs` 字典，**跳过连线引用**（长度为 2 的 list 且首元素为 string），保留标量值。
   - 返回列表，每项包含：`id`（`node_id.field`）、`node_id`、`node_title`、`field`、`value`、`type`。
4. 前端收到列表后存入 `allInputs`，渲染 Step 2 的表格，并切换到 Step 2。

### 支持的 Workflow 格式

| 格式 | 特征 | 处理 |
|------|------|------|
| UI 格式 | 含 `nodes`、`links`（ComfyUI "Save" 导出） | `ensure_api_format()` → `convert_workflow_to_api()` 转换 |
| API 格式 | 顶层键为节点 ID，含 `class_type`、`inputs` | 校验 `class_type` 后直接使用 |

---

## Step 2 — Select Variables

### 界面元素

| 元素 | 说明 |
|------|------|
| **全选 Checkbox** | 表头的勾选框，一键全选/全不选 |
| **变量表格** | 每行一个扫描到的输入项 |
| **Continue 按钮** | 收集已勾选项，进入 Step 3 |
| **Back 按钮** | 回到 Step 1 |

### 表格列

| 列 | 内容 |
|----|------|
| ☑ | 勾选框；若当前值以 `**` 开头（正则变量占位符），默认勾选 |
| Node | `#node_id` 徽章 + `node_title` |
| Field | 字段名（如 `image`、`text`、`seed`） |
| Value | 当前默认值，超长截断 |
| Alias | 可编辑文本框，默认为 `field`，用于后续表单标签和 Template 中的显示名 |

### Continue 逻辑

1. 遍历表格中所有已勾选行，从 `allInputs` 中取出对应项并附上用户填写的 `alias`。
2. 组成 `selectedInputs` 数组。
3. 调用 `renderForm(selectedInputs)` 生成 Step 3 表单，切换视图。

---

## Step 3 — Configure & Run

### 界面元素

| 元素 | 说明 |
|------|------|
| **动态表单** | 每个已选变量渲染为一个表单项 |
| **💾 Save Workflow** | 保存当前 workflow JSON |
| **📋 Save Data Template** | 保存变量元数据 |
| **🧪 Test Run** | 单次执行 workflow |
| **Back 按钮** | 回到 Step 2 |

### 表单项渲染规则

- **普通字段**：`<input type="text">` 或 `<input type="number">`（依 `type` 而定），默认值为 `value`。
- **Seed 字段**（字段名含 `seed` 且 type 为 `number`）：额外显示 **🎲 Random** 复选框。勾选后输入框禁用，运行时发送 `__random_seed__` 标记，由服务端在 ComfyUI seed 范围内生成随机值。
- 每项右侧有 **✕ 按钮**，点击可从 `selectedInputs` 中移除该字段。
- **Label 格式**：`alias (node_id.field)` — alias 为粗体，`node_id.field` 以小字灰色显示。

### 三个核心操作

#### 💾 Save Workflow

1. 弹出 `prompt()` 让用户输入名称。
2. `POST /api/workflows`，body：`{ name, workflow: currentWorkflow }`。
3. 服务端将 `workflow` JSON 写入 `data/workflows/<safe_name>.json`。
4. 文件名安全化：仅保留字母、数字、`-`、`_`。
5. 保存成功后刷新所有下拉框。

#### 📋 Save Data Template

1. 弹出 `prompt()` 让用户输入名称。
2. 前端组装 template 对象：

```json
{
  "name": "模板名",
  "variables": [
    {
      "id": "7.image",
      "node_id": "7",
      "node_title": "Load Image",
      "field": "image",
      "alias": "image",
      "type": "text",
      "default": "example.png"
    }
  ]
}
```

   - `default` 取表单中当前填写的值。
   - 若勾选了 Random Seed，额外写入 `"random_seed": true`。
3. `POST /api/templates`，服务端写入 `data/templates/<safe_name>.json`。

#### 🧪 Test Run

1. 构造 `FormData`：附上 `workflow`（JSON 字符串）、`server_address`、每个变量的 `vars[node_id.field]=value`。
2. `POST /api/run`（multipart），服务端执行：
   - 上传本地文件到 ComfyUI（图片/音频/视频）。
   - `ensure_api_format()` → `resolve_random_seeds()` → `inject_variables()`。
   - 调用 ComfyUI 生成，返回 base64 编码的结果。
3. 切换到 Step 4 显示结果。

---

## Step 4 — Results

### 界面元素

| 元素 | 说明 |
|------|------|
| **Loading 动画** | 请求期间显示 spinner |
| **结果网格** | 图片以 `<img>` 展示（base64）；音频以 `<audio>` 展示；其他为文本 |
| **💾 Save Workflow** | 同 Step 3，方便测试满意后直接保存 |
| **📋 Save Data Template** | 同 Step 3 |
| **Test Again** | 回到 Step 3 重新调参 |
| **Start Over** | `location.reload()` 重新开始 |

---

## 数据流总结

```
                          前端状态                      服务端
                    ┌───────────────┐
粘贴/上传 JSON ───→ │ currentWorkflow│
                    └───────┬───────┘
                            │ POST /api/scan
                            ▼
               ┌────────────────────────┐       scan_possible_inputs()
               │ allInputs (全部候选参数) │ ←──── ensure_api_format() + 遍历节点
               └────────────┬───────────┘
                  用户勾选 + alias
                            ▼
               ┌────────────────────────┐
               │ selectedInputs (已选)   │
               └──────┬─────────┬───────┘
                      │         │
         Save Workflow│         │Save Template
                      ▼         ▼
          POST /api/workflows  POST /api/templates
          data/workflows/      data/templates/
          *.json               *.json
```

### 关键全局变量

| 变量 | 类型 | 说明 |
|------|------|------|
| `currentWorkflow` | Object | 当前加载的完整 workflow JSON（经 parse 后的对象） |
| `currentWorkflowName` | String | 若从 Saved Workflow 加载，记录其名称 |
| `allInputs` | Array | Scan 返回的所有可能输入项 |
| `selectedInputs` | Array | 用户在 Step 2 勾选后的子集，附带 `alias` 等额外信息 |

---

## 与 Batch Mode 的衔接

Setup Mode 保存的 Workflow 和 Data Template 会在 **Batch Mode**（第二个 Tab）中被引用：

1. Batch Mode 的下拉框从 `/api/workflows` 和 `/api/templates` 加载列表。
2. 用户选择 Workflow + Template 后点 **Load**，进入批量编辑器。
3. Template 中的 `variables` 决定了批量编辑器的列头（每个变量一列）。
4. 每行数据的 key 使用 `variable.id`（即 `node_id.field`），与 `inject_variables()` 的注入 key 一致。

因此 Setup Mode 的核心产出就是这两个文件：
- **Workflow** = 完整的图结构
- **Data Template** = 从图中圈定的一批可调字段 + 默认值 + 别名
