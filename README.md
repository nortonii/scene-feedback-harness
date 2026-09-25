# Scene Feedback Harness

一个本地 3D 场景反馈工具：Codex 通过 MCP 创建审阅会话，用户在浏览器中选择对象、画辅助线、设置目标包围框，提交后结构化反馈回到 Codex。

**English:** A local 3D review UI and MCP server for collecting object-linked spatial feedback before an agent edits a scene.

![3D 场景反馈界面](preview.png)

## 工作流程

1. Codex 调用 `open_scene_feedback` 创建会话，获得本机页面地址。
2. 用户在页面中选择对象，画辅助线或调整目标包围框，可附加文字备注。
3. 用户提交或取消；`wait_scene_feedback` 返回状态及反馈。反馈包含对象 ID、世界坐标、场景版本和相机视角；截图成功时还会包含预览图路径。
4. Codex 根据反馈调用 `update_scene`、`replace_scene`，或使用自己的建模工具修改网格，再让用户审阅下一版。

页面使用 Three.js，在浏览器中运行；无需在 Codex 聊天界面内嵌 3D 组件。

## 本机试用

需要 Python 3.11 或更新版本。以下命令适用于 Linux/macOS，请在克隆后的仓库根目录执行：

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r backend/requirements.txt
.venv/bin/python backend/server.py
```

浏览器打开 <http://127.0.0.1:18765/>，即可试用内置的椅子场景。HTTP 服务仅监听 `127.0.0.1`。网页和 MCP 工具使用同一份 `backend/data/` 数据；该目录保存场景状态、导入的模型、反馈截图和本机控制令牌，已被 Git 忽略。

## 接入 Codex

在仓库根目录执行以下命令。`$PWD` 会在注册时展开为你的仓库绝对路径：

```bash
codex mcp add scene_feedback -- "$PWD/.venv/bin/python" "$PWD/backend/mcp_server.py"
```

等待用户提交可能持续数分钟。在 `~/.codex/config.toml` 已生成的 `[mcp_servers.scene_feedback]` 节中加入：

```toml
tool_timeout_sec = 900
```

重新加载 Codex 的 MCP 配置后，可以这样发起任务：

> 调用 `open_scene_feedback(wait_for_submit=false)`，把会话 URL 发给我，然后调用 `wait_scene_feedback` 等我提交。收到反馈后按对象 ID 和坐标修改场景。

如果本机图形桌面允许打开浏览器，`open_scene_feedback()` 默认会自动打开页面并等待提交。若浏览器未打开，它会立即返回 URL 和 `session_id`；把 URL 给用户，再调用 `wait_scene_feedback(session_id)`。提交、取消或超时均会结束等待；超时后会话仍可通过 `get_scene_feedback(session_id)` 查询。页面提交本身不会启动新的 Codex 回合，因此需要一个等待中的工具调用，或由外部宿主程序显式启动后续回合。

## 反馈数据

对象由稳定的 `id` 标识，场景由递增的 `revision` 标识。提交时检查 `scene_revision`，以免旧标注应用到新版场景。页面产生的目标框和辅助线使用世界坐标，Z 轴向上。例如：

```json
{
  "scene_revision": 1,
  "annotations": [
    {
      "type": "target_box",
      "object_id": "chair_back",
      "coordinate_frame": "world",
      "center": [0, 0.51, 1.69],
      "size": [1.25, 0.16, 1.72],
      "anchor": "bottom",
      "source_box": {
        "center": [0, 0.51, 1.54],
        "size": [1.25, 0.16, 1.42]
      }
    },
    {
      "type": "guide_line",
      "object_id": "chair_back",
      "coordinate_frame": "world",
      "start": [0, 0.51, 0.83],
      "end": [0, 0.51, 2.55]
    }
  ],
  "note": "椅背上沿与辅助线终点对齐"
}
```

`get_scene` 返回当前场景和版本。`get_scene_feedback(session_id)` 可以稍后读取已提交反馈。`update_scene` 和 `replace_scene` 均要求传入预期版本，版本变化时会拒绝写入。

## 导入 GLB 模型

通过 MCP 工具 `import_scene_model(local_path=...)` 传入模型在运行 MCP 服务器的机器上的绝对路径。支持 GLB 2.0，导入时会将文件复制到 `backend/data/assets/`；原文件不会被修改。导入对象的 `size` 是模型各轴的缩放倍数，反馈目标框中的 `size` 则是世界坐标下的尺寸。

当前页面只负责预览和标注模型。它不会直接改写 GLB 网格；网格编辑需由 Codex 调用其他建模工具完成。

## 测试

```bash
.venv/bin/python -m unittest discover -s backend/tests -v
```

## 本机使用范围与许可

此原型面向可信的本机环境。虽然服务仅监听回环地址，同机其他进程仍可访问会话 API；请勿将端口转发给不可信访问者，或用于承载敏感场景。

项目原创代码使用 [MIT 许可证](LICENSE)。随仓库提供的 Three.js 文件保留其[原始 MIT 许可证](web/vendor/three/LICENSE)。
