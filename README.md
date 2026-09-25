**语言 / Languages:** **简体中文** · [English](README.en.md) · [日本語](README.ja.md) · [한국어](README.ko.md) · [Русский](README.ru.md)

# Codex 视觉重建工作台

在参考图片和当前 3D 场景上圈画，写一句话，点击「发送」。工作台把文字、原图、标注图和场景快照作为**同一条 Codex 会话中的用户消息**送出；Codex 修改项目并发布 GLB 后，结果回到这个页面。你不需要去终端再触发一次读取。

![参考图与场景并排标注](preview.png)

## 工作方式

```text
浏览器：参考图 + 3D 场景 + 标注 + 文字
                 │ 用户点击「发送」
                 ▼
          本地 Workspace Gateway
                 │ 真实图像输入与执行事件
                 ▼
       Codex App Server：同一个项目、同一个 thread
                 │ 修改源文件、导出 GLB、调用 MCP 工具
                 ▼
              工作台刷新场景
```

Gateway 为这个项目保存一条持久 Codex 会话。它通过 stdio 启动 `codex app-server`，并将图片作为 `localImage` 输入项发送。工作台不向其他已经打开的 Codex 桌面或终端会话注入消息。MCP 用于读取上下文、请求用户检查以及发布场景；**网页的发送按钮直接启动用户回合**。

这套界面负责帮助人指出问题，不定义重建算法，也不要求人输入坐标或几何约束。Codex 可以使用项目已有的建模、重建和编辑工具。

## 页面能做什么

- 左边切换、缩放和平移参考图；右边旋转、缩放和点选 GLB 场景中的节点。
- 在两侧画点、框、线、箭头、自由笔迹和文字。对应标记可以编号；缺失物体也可以只圈在参考图上。
- 点击「标注当前视角」后，场景标记绑定**当时的截图、相机、选中对象和场景版本**。转动实时 3D 视图不会让旧标记漂到别的物体上；新场景发布也不会覆盖正在标注的快照。
- 发送时保存不可变的反馈包：用户原话、参考原图与标注图、干净与标注后的场景截图、选中节点、相机及场景版本。标记表示人的提示，原图仍单独保留。
- 执行中的新反馈进入下一轮队列；若场景在排队期间改变，页面先请你确认旧版本反馈。审批请求和停止操作也在页面处理。刷新页面后，项目、会话和草稿继续保留。

目前场景输入是自包含的 `.glb`；发布校验要求几何和纹理资源嵌入 GLB 的 BIN 块，外部 URI 和 data URI 都会被拒绝。参考图可以单独提交，所以没有初始场景也能开始。参考图叠加显示只用于肉眼比较，不会自动做相机配准。

## 快速试用：房间与柜子

需要 Python 3.11+、支持 WebGL 的浏览器，以及已登录的 **`codex-cli 0.156.1`**。App Server 的请求和响应格式已对照这个版本生成的 JSON Schema 核对；其他版本会明确报错，避免静默使用不兼容字段。

在克隆后的仓库根目录执行：

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r backend/requirements.txt
codex --version
.venv/bin/python examples/room_demo/seed_demo.py
.venv/bin/python backend/server.py --project-dir "$PWD" --data-dir "$PWD/examples/room_demo/output/data"
```

Gateway 会在项目的 Codex thread 中自动配置这五个 MCP 工具，无需手动执行 `codex mcp add`。端口、数据目录和项目目录由 Gateway 传给该 thread，避免其他项目或全局 MCP 配置指向错误的工作台。

打开 <http://127.0.0.1:18765/>。左侧是目标示意图，右侧是初始房间 GLB。选中柜子，在图上指出目标位置，输入「柜子应该更靠近左墙，请按左图调整」，然后点击「发送」。演示源参数在 [`examples/room_demo/scene.json`](examples/room_demo/scene.json)，[`build_scene.py`](examples/room_demo/build_scene.py) 会从参数生成 GLB；结果由 Codex 调用 `workspace_publish_scene` 后出现在页面。`seed_demo.py` 使用独立的 `examples/room_demo/output/data`，不会覆盖普通工作区的数据。

自己的项目可用：

```bash
.venv/bin/python backend/server.py --project-dir "/absolute/path/to/your/project" --data-dir "/absolute/path/to/private/workspace-data"
```

浏览器里添加参考图；让 Codex 从项目源文件构建或导出自包含 GLB，并通过 MCP 发布。项目目录和 thread ID 会保存在数据目录中；重新启动时恢复同一会话。一个数据目录只绑定一个项目，不会悄悄切换到别的项目。

## MCP 工具

| 工具 | 用途 |
| --- | --- |
| `workspace_open` | 返回或打开这个项目的工作台地址 |
| `workspace_get_context` | 读取当前参考图、场景版本和项目上下文 |
| `workspace_get_feedback` | 读取已提交反馈及其实际图像 |
| `workspace_publish_scene` | 校验并发布新的 GLB，检查预期版本，通知页面刷新 |
| `workspace_request_feedback` | 在页面请求用户检查某处，立即返回；用户的回复会成为下一条用户消息 |

仓库内的 [视觉重建 Skill](.agents/skills/visual-reconstruction-feedback/SKILL.md) 提醒 Codex 区分原图和标记、编辑项目源文件，并在结果就绪时发布 GLB。新工作流无需等待某个 MCP 调用才能发送下一轮。

## 验证与边界

```bash
.venv/bin/python -m unittest discover -s backend/tests -v
```

App Server 的真实联调已用两张不同的本地图像完成：Codex 在第一轮识别红图，关闭并重启 stdio 后，在**同一 thread** 的第二轮识别蓝图；`thread/read` 历史记录包含两轮的 `localImage` 项。这验证了图片确实进入模型，不只是传了图片路径。完整浏览器验收中，人在参考图和场景图上分别画编号框并点击发送；Codex 收到五张实际图像，修改演示场景源文件并导出 GLB，经页面审批后两次成功调用 `workspace_publish_scene`，工作台自动从场景版本 2 刷新到版本 4。随后在网页再次画参考线并发送；Codex 在**同一 thread** 收到第二轮的五张图像并确认，没有修改文件或重复发布。

服务只监听 `127.0.0.1`，面向可信本机使用。为访问本机 MCP Gateway，Codex 的 `workspace-write` 回合设置 `networkAccess: true`；页面控制接口使用本地随机 capability。不要把端口代理给不可信访问者。执行审批由用户在页面决定，工作台不会自动批准。运行数据和演示输出均不进入 Git。

项目代码使用 [MIT 许可证](LICENSE)。仓库里的 Three.js 文件保留其[原始 MIT 许可证](web/vendor/three/LICENSE)。
