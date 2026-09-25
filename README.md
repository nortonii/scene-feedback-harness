# Astra 视觉反馈工作台

让人和 Astra 对着同一张参考图、同一个当前场景交流。你在图片或 3D 视图上圈、画、指，再补一句话；MCP 将原图、标注图、场景截图和必要的上下文交回模型。**Astra 自行判断该改物体、相机、材质还是重建方式**。工作台不要求人填写坐标或几何约束。

**English:** A local visual feedback workbench for photo guided 3D reconstruction. Humans mark the reference photo and current scene; an MCP tool returns those images and the accompanying note to the agent.

![参考图与场景并排标注](preview.png)

## 能做什么

- 左边看参考图，右边旋转、缩放、点选当前 3D 场景；可切换多张参考图。
- 在任一侧画点、框、线、箭头、文字。需要对照时给两边标记相同编号；场景中还没有的东西可以只圈在参考图上。
- 可将当前参考图半透明叠在 3D 视图上做肉眼对比。叠图**没有自动相机配准**。
- 点击「发给 Astra」后，同一会话继续保留。Astra 更新场景时页面自动刷新；标记草稿和查看视角会留在浏览器中，方便下一轮。

提交的视觉反馈包含原始参考图、带标记的参考图、当前场景原始截图及带标记截图、必要的局部图、备注、选中对象 ID、场景版本和查看相机。点击单个 GLB 内的部件时，还会附上所点节点的名称和路径。图像作为 MCP image content 返回，元数据同时在 structured content 和文本中提供；本机绝对路径也会列出，便于宿主在没有转发 MCP 图像块时读取。

## 本机试用

需要 Python 3.11+ 和支持 WebGL 的浏览器。在克隆后的仓库根目录执行：

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r backend/requirements.txt
.venv/bin/python backend/server.py
```

打开 <http://127.0.0.1:18765/>，上传参考图即可试用。右侧默认是一把示例椅子；接入自己的场景后会显示自己的结果。这是独立浏览器工作台，不依赖客户端支持内嵌 MCP Apps。服务只监听 `127.0.0.1`。`backend/data/` 保存导入的图像、场景和反馈，已加入 Git 忽略列表。

## 接入 Codex / Astra

在仓库根目录注册 MCP 服务器；`$PWD` 会展开成仓库的绝对路径：

```bash
codex mcp add scene_feedback -- "$PWD/.venv/bin/python" "$PWD/backend/mcp_server.py"
```

在 `~/.codex/config.toml` 中为 `[mcp_servers.scene_feedback]` 设置较长的交互等待时间。若使用不会弹出审批的 Codex 模式，可只给创建工作台的工具设置自动放行；读取反馈的工具已声明为只读。然后重新加载 MCP 配置：[Codex MCP 配置说明](https://learn.chatgpt.com/docs/extend/mcp)。

```toml
tool_timeout_sec = 900

[mcp_servers.scene_feedback.tools.request_visual_feedback]
approval_mode = "approve"
```

推荐的两步调用方式：

```text
request_visual_feedback(
  reference_images=["/absolute/path/reference.jpg"],
  scene_glb_path="/absolute/path/current.glb",
  wait_for_submit=false
)
→ session_id、url、next_cursor

用户在 url 中圈画并点击「发给 Astra」

wait_visual_feedback(session_id, cursor=next_cursor)
→ 图片块 + 标注/文字/对象/相机/版本等信息 + 新的 next_cursor
```

也可以用 `current_scene={"objects": [...]}` 传入现有场景对象列表，或不传场景以使用工作台当前的场景。`scene_glb_path` 用于导入一份可预览的 GLB；这是显示方式，不规定 Astra 如何建模。GLB 文件和参考图路径必须是运行 MCP 服务那台机器上的本地路径。浏览器中也能直接添加参考图。

收到反馈后，Astra 使用自己的建模工具修改场景。下一轮调用 `request_visual_feedback(session_id=..., scene_glb_path="/absolute/path/updated.glb", wait_for_submit=false)`，再以本次返回的 `next_cursor` 调用 `wait_visual_feedback`。复用同一个 `session_id`，页面和参考图就不必重开。网页提交并不会独立唤起一个已经结束的 Codex 回合；要让 Astra 继续执行，需有等待中的 MCP 调用，或由自定义宿主开启后续回合。

如果宿主能打开本机浏览器，`request_visual_feedback()` 默认尝试打开页面并等待提交；若没有打开，它立即返回 URL，随后用 `wait_visual_feedback` 等待。等待超时后会话仍然开放，可用 `get_visual_feedback(session_id, cursor)` 查询，或再次等待。

## 反馈格式

标记坐标是各自图片或视图中的归一化屏幕坐标（`0–1`），用于表达用户指向的画面位置；它们不是世界坐标，也不是要执行的建模命令。例如：

```json
{
  "scene_revision": 12,
  "note": "柜子顶部应该跟左图①的线差不多；右边还缺一盏灯。",
  "selected_object_ids": ["cabinet"],
  "annotations": [
    {
      "pane": "reference",
      "reference_image_id": "<reference-id>",
      "type": "line",
      "group_id": "1",
      "coordinates": {"x": 0.23, "y": 0.32, "x2": 0.68, "y2": 0.32}
    },
    {
      "pane": "scene",
      "type": "point",
      "group_id": "1",
      "object_id": "cabinet",
      "coordinates": {"x": 0.54, "y": 0.46}
    }
  ]
}
```

`group_id` 和 `object_id` 都是可选的。选中 GLB 内的节点时，反馈还会带 `selected_scene_nodes`；每个节点给出所属模型对象 ID、GLB 内子节点索引路径及可用的节点名称。这是对画面位置的辅助引用，不是模型应执行的操作。原图与标注图分别保留，Astra 可以区分照片原本的内容和人的提示。若场景版本变化，旧场景标记会在界面上提示来源版本；提交时会检查当前版本，避免误把旧画面当成新画面。保留的标记在下一轮仍会发送，可以单独删除或清空。

现有的 `get_scene`、`update_scene`、`replace_scene` 和 `import_scene_model` 工具仍可用于展示场景或与既有调用方兼容；此工作台本身不修改 GLB 网格。

## 验证与使用范围

```bash
.venv/bin/python -m unittest discover -s backend/tests -v
```

这是面向可信本机环境的原型。同机其他进程可以访问页面和会话 API，切勿将本机端口开放给不可信访问者。自动放行配置只应用于你信任的本机服务器。Codex CLI 已实测能从本工具的 MCP 图像块读取参考图内容；其他宿主是否转交图像取决于其实现。如果宿主只显示文本，可让 Astra 读取反馈中的本机图片路径。

项目原创代码使用 [MIT 许可证](LICENSE)。仓库中的 Three.js 文件保留其[原始 MIT 许可证](web/vendor/three/LICENSE)。
