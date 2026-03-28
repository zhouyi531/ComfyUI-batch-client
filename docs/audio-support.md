# Audio File Support

## 背景

本次修改新增了对音频文件的完整支持，包括：将音频文件上传至 ComfyUI 服务器、从 workflow 输出中下载音频结果并保存到本地。

---

## 问题一：音频文件未上传到 ComfyUI

### 现象

使用含 `LoadAudio` 节点的 workflow 时报错：

```
Error: Failed to queue prompt: 400
"audio - Invalid audio file: /home/.../data/uploads/upload_xxx.mp3"
```

### 根本原因

`server.py` 的 batch 处理逻辑只对 `IMAGE_EXTENSIONS` 的文件调用 `upload_image_bytes()` 上传到 ComfyUI。音频文件走 `else` 分支，将本机的绝对路径直接注入 workflow，ComfyUI 服务器无法访问该路径。

### 修复

**`comfyuiclient/client.py`** — 新增 `upload_audio_bytes()` 方法：

- 优先尝试 ComfyUI 的 `/upload/audio` 接口（需安装 VHS 等扩展）
- 若收到 `405 Method Not Allowed`，自动降级至 `/upload/image` 接口
- `/upload/image` 同样将文件存入 ComfyUI 的 `input/` 目录，`LoadAudio` 节点可按文件名找到它

**`scripts/server.py`** — 模块级新增常量并修复上传分支：

```python
IMAGE_EXTENSIONS = {'.png', '.jpg', '.jpeg', '.webp', '.bmp', '.gif'}
AUDIO_EXTENSIONS = {'.mp3', '.wav', '.flac', '.ogg', '.aac', '.m4a'}
```

batch 处理、单次 `/api/run` 均改为：对音频扩展名调用 `upload_audio_bytes()`，而不是直接传本地路径。

**`scripts/run.py`** — `run_workflow` 函数中对音频扩展名同样调用 `upload_audio_bytes()`。

---

## 问题二：音频输出未下载到本地

### 现象

workflow 成功运行，但 `SaveAudio` 节点的输出文件没有出现在本地 outputs 目录中。

### 根本原因

`client.py` 的 `get_images` 方法只从 ComfyUI history 中读取 `"images"` 和 `"text"` 字段，完全跳过了 `"audio"` 字段，导致音频数据从未被取回。

### 修复

**`comfyuiclient/client.py`**（async + sync 两个版本）：

- `get_images`：新增读取 history 中的 `"audio"` 字段，用 `/view` 接口（与图片相同）下载音频字节。返回值由 `(images, text)` 扩展为 `(images, text, audio)`。
- `generate`：处理 audio 结果，以统一格式放入 results：
  ```python
  {"_type": "audio", "filename": "output.flac", "data": <bytes>}
  ```

**`scripts/server.py`**：

- **batch** `/api/batch`：检测到 `_type == "audio"` 时，保留原始扩展名写入磁盘（如 `name_workflow.flac`），并加入 outputs 列表返回给前端。
- **单次** `/api/run`：将音频 base64 编码后返回，携带正确的 MIME type（`audio/flac` 等）。
- **`/api/outputs/{job_id}`**：扩展文件列举，支持音频格式扩展名。
- **`/api/outputs/{job_id}/{filename}`**：为音频文件设置正确的 `Content-Type` 与 `Content-Disposition: attachment`，支持浏览器直接下载。

---

## 涉及文件

| 文件 | 变更内容 |
|------|---------|
| `comfyuiclient/client.py` | 新增 `upload_audio_bytes()`；`get_images` 新增 audio 输出；`generate` 处理 audio 结果 |
| `scripts/server.py` | 新增 `AUDIO_EXTENSIONS` 常量；batch/run 上传音频；batch/run 保存/返回音频；outputs API 支持音频 |
| `scripts/run.py` | `run_workflow` 对音频文件调用 `upload_audio_bytes()` |

---

## 支持的音频格式

上传（输入）和下载（输出）均支持：`.mp3` `.wav` `.flac` `.ogg` `.aac` `.m4a`
