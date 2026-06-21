# 从 Workflow 提取参数并保存 Data Template

本文说明本项目中「参数从哪里来」「如何挑选」「如何保存为数据模板」，以及保存后的文件放在哪里、格式是什么。

---

## 1. Workflow 格式

支持两种来源（会自动统一处理）：

| 格式 | 特征 | 处理 |
|------|------|------|
| **UI 格式** | 含 `nodes`、`links`（ComfyUI 界面 Save） | `WorkflowManager.ensure_api_format()` 转为 API 格式 |
| **API 格式** | 顶层键为节点 ID，节点含 `class_type`、`inputs`（Save API Format） | 校验后直接使用 |

转换逻辑在 `comfyuiclient/workflow_manager.py` 与 `comfyuiclient/client.py` 的 `convert_workflow_to_api`。

---

## 2. 参数是如何从 Workflow 里「扫」出来的

核心函数：`WorkflowManager.scan_possible_inputs(workflow_json)`。

流程简述：

1. 先 `ensure_api_format`，保证是 API 结构。
2. 遍历**每个节点**的 `inputs` 字典。
3. **跳过**连线引用：值为长度为 2 的列表、且第一个元素为字符串时，视为 ComfyUI 的节点连接，不是可调参数。
4. **保留**标量：字符串、数字、布尔等，作为「可暴露输入」。
5. 为每一项生成：
   - **`id`**：`{node_id}.{field}`（例如 `7.image`、`3.text`），后续注入与批量 JSON 都用这个键。
   - **`type`**：按 Python 类型粗分为 `text` / `number` / `boolean`（字符串默认 `text`）。

Web 端在 **Data Template Builder** 里点击 **Scan Workflow** 时，会向 `POST /api/scan` 提交当前文本框里的 JSON，服务端返回上述列表，前端表格展示。

### 与「正则变量」的区别

若工作流字符串里使用旧式占位符 `**变量名[类型]**`（可选 `(opt1|opt2)`），可用 CLI：

```bash
python scripts/run.py extract-vars path/to/workflow.json
```

这会走 `WorkflowManager.extract_variables()`，按正则扫描，与 UI 的「节点输入扫描」是**另一条路径**。日常用 Web 选节点字段即可；正则方式适合已在 workflow 里写好占位符的场景。

---

## 3. 在界面里如何「选取」参数

对应步骤 **Step 2：Select Variables**：

1. 表格每一行对应 `scan_possible_inputs` 的一条结果（节点、字段、当前值）。
2. **勾选**要参与批处理 / 单次测试的字段。
   - 若当前值以 `**` 开头（正则变量占位），界面会**默认勾选**，减少手工操作。
3. **Alias** 列可改显示名，仅影响表单标签与可读性，**注入 workflow 仍使用 `id`（`node_id.field`）**。
4. 点 **Continue** 进入 Step 3，可填默认值、对种子字段勾选 **Random** 等。

---

## 4. 保存 Workflow 与保存 Data Template

### 4.1 Save Workflow（保存工作流）

- 时机：Step 3 或 Step 4 点击 **Save Workflow**。
- 内容：当前解析后的 **`currentWorkflow` 对象**（即你加载/粘贴的那份 JSON），**不包含**变量列表。
- 接口：`POST /api/workflows`，body：`{ "name": "<名称>", "workflow": { ... } }`。
- 落盘：`data/workflows/<安全化名称>.json`（名称只保留字母数字、`-`、`_`）。

用途：在 **Run Mode** 里下拉选择同一份图，无需反复上传文件。

### 4.2 Save Data Template（保存数据模板）

- 时机：同上，点击 **Save Data Template**。
- 内容：仅保存**当前 Step 3 已选变量**及其元数据，**不**内嵌完整 workflow。
- 接口：`POST /api/templates`，body 结构示例：

```json
{
  "name": "my_template",
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

- 若某变量在表单中勾选了种子 **Random**，会额外写入 `"random_seed": true`。
- 落盘：`data/templates/<安全化名称>.json`。

仓库里部分文件命名为 `*_data_template.json`，这是**命名习惯**；服务端只按「模板名 → `模板名.json`」保存，可在保存时的名称里写上 `xxx_data_template`，便于区分。

### 4.3 两者关系（批量运行时）

在 **Run Mode**：

1. **Saved Workflow**：提供完整图结构。
2. **Saved Template**（可选）：提供要改哪些 `node_id.field`、默认值与别名等。
3. 批量数据里每一行是一个对象，键为变量的 **`id`**（如 `"7.image"`），值为字符串或路径；服务端用 `WorkflowManager.inject_variables` 写回各节点 `inputs`。

因此：**Workflow = 图；Data Template = 从图中圈定的一批可调字段 + 默认值的说明**。

---

## 5. 运行时如何注入（与模板字段对应）

`WorkflowManager.inject_variables(workflow, values)` 支持：

- 键为 **`node_id.field`**：直接写入对应节点的 `inputs[field]`。
- 键为 **正则变量名**：替换 workflow 里 `**name[type]**` 形式的字符串。

Web 与批量 API 主要使用第一种键，与 `scan_possible_inputs` 产出的 `id` 一致。

---

## 6. 快速对照

| 步骤 | 操作 |
|------|------|
| 准备 JSON | 粘贴或上传 ComfyUI 导出的 workflow（UI 或 API 格式均可） |
| 提取候选参数 | Scan → 服务端 `scan_possible_inputs` |
| 选取并命名 | Step 2 勾选 + 填写 Alias |
| 保存图 | Save Workflow → `data/workflows/*.json` |
| 保存变量方案 | Save Data Template → `data/templates/*.json` |

更完整的批量与 CLI 示例见项目根目录 `USAGE_GUIDE.md` 中的「数据文件说明」与命令行章节。
