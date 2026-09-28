**语言 / Languages:** **简体中文** · [English](README.en.md) · [日本語](README.ja.md) · [한국어](README.ko.md) · [Русский](README.ru.md)

# Codex 视觉重建工作台

默认模式下，在参考图片和当前 3D 场景上圈画，写一段提示，点击「发送反馈」。工作台把文字、原图、标注图和场景快照作为**工作台所管理的同一条 Codex 会话中的用户消息**送出；Codex 修改项目并发布 GLB 后，结果回到这个页面。你不需要去终端再触发一次读取。已有 Codex 桌面任务也可以通过下文的外部 MCP 模式使用同一套审图界面。

![参考图与场景并排标注](preview.png)

## 工作方式

```text
浏览器：参考图 + 3D 场景 + 标注 + 文字
                 │ 用户点击「发送反馈」
                 ▼
          本地 Workspace Gateway
                 │ 真实图像输入与执行事件
                 ▼
       Codex App Server：同一个项目、同一个 thread
                 │ 修改源文件、导出 GLB、调用 MCP 工具
                 ▼
              工作台刷新场景
```

默认模式下，Gateway 为这个项目保存一条持久 Codex 会话。它通过 stdio 启动 `codex app-server`，并将图片作为 `localImage` 输入项发送。此模式不向其他已经打开的 Codex 桌面或终端会话注入消息。MCP 用于读取上下文、请求用户检查以及发布场景；**网页的发送按钮直接启动用户回合**。

这套界面负责帮助人指出问题，不定义重建算法，也不要求人输入坐标或几何约束。Codex 可以使用项目已有的建模、重建和编辑工具。

## 页面布局与入口

页面使用暖白底色和黑灰控件。宽屏下，参考图与场景并排显示，横向工具栏在上方，统一提示框在底部；任务、记录和引用面板按需打开。

| 入口 | 用途 |
| --- | --- |
| 工具栏 | 「选择」「点」「框」「线」「箭头」；「更多」里是文字、自由画笔和可选对应编号；「物品 / 部件」切换点选层级 |
| 顶部当前任务名称 | 打开「Codex 任务」，切换接收任务，或选择名称、模型、思考强度和权限新建任务 |
| 顶部「记录」 | 查看执行、审批、反馈队列和消息记录。默认关闭；新的审批或需处理的失败、旧版本、送达待核实反馈会自动打开一次 |
| 底部「物体与标记」 | 查看引用列表；点「引用」后插入提示光标处，面板关闭，光标回到提示框 |
| 参考图区域「导入」 | 添加参考图片、视频或帧序列，并设置序列帧率 |
| 时间轴「选项」 | 选择反馈范围、时间区间及 GLB 动画动作 |

底部按钮通常显示「发送反馈」；任务正在执行时显示「加入下一轮」，反馈会排队。查看旧场景快照时，叠图下方会显示类似「标注 v13 · 查看最新 v15 ↗」的按钮；点它查看最新实时场景，旧快照和标记仍保留。

## 页面能做什么

- 左边切换、缩放和平移参考图；右边旋转、缩放和点选 GLB 场景中的节点。
- 可切换物品级和部件级点选：物品级选中 GLB 中顶层的场景节点（当前素材中对应一个完整物品），部件级选中点击到的具名细分节点。场景中的基本图元对象始终整体选中；同一段提示可以引用任一级别的目标。
- 在两侧画点、框、线、箭头、自由笔迹和文字。对应标记可以编号；缺失物体也可以只圈在参考图上。
- 只写一段给 Codex 的提示。把光标放在文字里，点击物体、已点选的 GLB 节点或标记旁的「引用」，就能在同一段提示中插入多个引用，例如 `[[object:ID]]`、`[[node:MODEL_ID:0/2]]`、`[[annotation:ID]]`。可以写“把这个柜子移到左图标记的位置，再对齐那条边线”，然后一次发送。尚未建模的东西也可以先在参考图上标记并引用；场景一次仍只高亮一个选中对象。
- 点击「标注」后，场景标记绑定**当时的截图、相机、选中对象和场景版本**。转动实时 3D 视图不会让旧标记漂到别的物体上；新场景发布也不会覆盖正在标注的快照。
- 插入标记引用、点击工具栏「选择」或场景区「返回 3D」后，会回到实时 3D 场景继续点选。固定截图和已有标记仍保留；点击「看标注」即可继续在截图上标注。
- 发送时保存不可变的反馈包：用户原话、参考原图与标注图、干净与标注后的场景截图、选中节点、相机及场景版本。标记表示人的提示，原图仍单独保留。
- 服务器确认通过 HTTP 保存反馈后，提示文本会清空，供下一轮重新填写；已有标记保留，即使反馈仍在排队或尚未确认投递到 Codex。若 HTTP 保存失败或结果未确认，本地草稿和待发送记录会保留，方便修改和重试。
- 执行中的新反馈进入下一轮队列；若场景在排队期间改变，页面先请你确认旧版本反馈。审批请求和停止操作也在页面处理。刷新页面后，项目、会话和草稿继续保留。

目前场景输入是自包含的 `.glb`；发布校验要求几何和纹理资源嵌入 GLB 的 BIN 块，外部 URI 和 data URI 都会被拒绝。参考图可以单独提交，所以没有初始场景也能开始。

## 对齐参考相机视角

选中带相机位姿的参考图时，右侧 3D 场景会自动切到该图的拍摄视角。叠图默认显示，可直接调节透明度（调到 0% 可隐藏）；手动旋转场景后，点「对齐」即可回到该机位。若提供了去畸变副本，叠图会使用它来匹配针孔相机投影；原始参考图仍单独保存，用于查看和反馈。没有相机标定的图片仍可手动对照；使用近似内参时，边缘仍可能有残余偏差。

每张图的相机元数据包含 GLB 世界坐标中的 `camera_to_world`（按行排列的 4×4 矩阵）和以该图像素为单位的 `intrinsics`（`width`、`height`、`fx`、`fy`、`cx`、`cy`）。已有图片可通过受保护的 `POST /api/workspace/reference-cameras` 接口按 `reference_id` 附加 `camera` 与可选的 `alignment_image_data_url`；未来导入的图片可由工作台数据目录中的 `reference_cameras.json` 清单按文件名前缀匹配（[示例](examples/reference_cameras.example.json)）。放入清单后，对已有图片发送 `{"apply_manifest":true}` 即可应用。相机位姿应与发布的 GLB 使用同一世界坐标系。

## 动态场景：共享时间轴与多帧反馈

参考图序列或视频可以与带动画的 GLB 共用时间轴。播放、暂停、拖动时间轴和逐帧前后跳转，会同时更新参考帧与 3D 动画；静态图片和静态 GLB 的原有流程仍可使用。GLB 支持节点位移、旋转、缩放、骨骼和固定拓扑的 morph 动画；可变拓扑网格缓存、流体与实时物理模拟尚未接入。

- 开始圈画时自动暂停，标记记录当时的时间、参考帧、相机和场景版本。可以保留最多 8 个不同时间点的场景快照，切回对应时间点继续查看或标注。
- 一次提示可以引用多个时间点的标记和物体，并在时间轴「选项」中选择「所标帧」「时间区间」或「整个片段」作为反馈范围。区间只是表达修改范围，不会把画线变成轨迹或几何约束。
- 标记默认只显示在自己的帧上。发送时交付所选时间点的原图、标注图和场景快照，以及时间戳、相机、对象引用与场景版本；不会把整段视频逐帧塞给模型。
- 每帧可带自己的相机位姿，切帧时按对应相机对齐。更新 GLB 后保留当前播放位置；拖动时间轴不会增加 `scene_revision`。
- 导出 GLB 时，可在节点的 `extras` 中保留 `semantic_id` 或 `stable_id`，帮助模型跨帧及重新导出识别同一物体。节点路径只定位当前版本内的节点；名称和路径不保证跨版本不变，也不会自动跟踪物体。

在参考图区域的「导入」菜单中，可按指定 FPS 导入有序图片，或导入视频并由服务端抽帧。视频导入需要服务主机安装可执行的 `ffmpeg` 和 `ffprobe`；图片序列不需要它们。视频按公共时间网格采样，默认 10 FPS，每个片段最多 600 帧，超过上限会报错，请降低采样率或先截取所需片段。

Codex 也可调用 `workspace_set_reference_clip(manifest_path=None, video_path=None, fps=None, camera_manifest_path=None, clear=False)` 导入项目内的素材；`manifest_path` 与 `video_path` 二选一。图片序列使用清单自己的 `fps`（省略时为 30），工具参数 `fps` 仅用于视频（默认 10）。每帧可指定 `time_sec`；省略时按从 0 开始的帧序号除以清单 FPS 计算，不从文件名推测帧率。帧路径可相对清单文件指定，但所有素材必须位于项目内。FPS 范围为 0.1–120；浏览器视频上传上限 40 MiB，项目本地视频上限 250 MiB。`clear=True` 移除当前参考片段，不改变场景版本。

图片序列清单示例（所有输入文件须位于当前项目内）：

```json
{
  "name": "assembly-review",
  "fps": 10,
  "frames": [
    {"path": "/absolute/path/to/project/frames/0000.png", "time_sec": 0.0},
    {"path": "/absolute/path/to/project/frames/0001.png", "time_sec": 0.1}
  ]
}
```

要保留相机对齐，请通过清单 / MCP 导入：在序列的每个帧条目内添加前文定义的 `camera`。浏览器只导入图片文件或视频时不会自动获得相机位姿。视频可另传 `camera_manifest_path`；固定相机清单示例如下（须将示例值替换为实际标定）：

```json
{
  "camera": {
    "camera_to_world": [[1,0,0,0],[0,1,0,0],[0,0,1,2],[0,0,0,1]],
    "intrinsics": {"width":1920,"height":1080,"fx":1200,"fy":1200,"cx":960,"cy":540}
  }
}
```

移动相机清单使用 `{"frames":[{"time_sec":0.0,"camera":…},…]}`，须覆盖视频的采样时刻；这一版按邻近采样匹配，不插值或估计相机。视频相机内参会按抽帧图片的尺寸缩放；序列的相机内参应直接匹配对应图片的尺寸。

导入清单，或按指定 FPS 抽取视频：

```text
workspace_set_reference_clip(manifest_path="/absolute/path/to/project/clip.json")
workspace_set_reference_clip(video_path="/absolute/path/to/project/reference.mp4", fps=10, camera_manifest_path="/absolute/path/to/project/video-cameras.json")
```

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

Gateway 会在项目的 Codex thread 中自动配置工作台 MCP 工具，无需手动执行 `codex mcp add`。端口、数据目录和项目目录由 Gateway 传给该 thread，避免其他项目或全局 MCP 配置指向错误的工作台。

打开 <http://127.0.0.1:18765/>。左侧是目标示意图，右侧是初始房间 GLB。选中柜子，在图上指出目标位置，输入「柜子应该更靠近左墙，请按左图调整」，然后点击「发送反馈」。演示源参数在 [`examples/room_demo/scene.json`](examples/room_demo/scene.json)，[`build_scene.py`](examples/room_demo/build_scene.py) 会从参数生成 GLB；结果由 Codex 调用 `workspace_publish_scene` 后出现在页面。`seed_demo.py` 使用独立的 `examples/room_demo/output/data`，不会覆盖普通工作区的数据。

自己的项目可用：

```bash
.venv/bin/python backend/server.py --project-dir "/absolute/path/to/your/project" --data-dir "/absolute/path/to/private/workspace-data"
```

浏览器里添加参考图；让 Codex 从项目源文件构建或导出自包含 GLB，并通过 MCP 发布。项目目录和 thread ID 会保存在数据目录中；重新启动时恢复同一会话。一个数据目录只绑定一个项目，不会悄悄切换到别的项目。

## 在现有 Codex 桌面任务中审图（外部 MCP 模式）

已有 Codex 桌面任务可用两种方式接收反馈。只用 `--external-review` 时，该任务调用 `request_visual_feedback`；工具默认等待浏览器提交，**即使服务端没有图形桌面、无法自动打开浏览器也会等待**。点击「发送反馈」后，文字和图像作为 MCP 工具结果返回给仍在等待的原任务。显式传入 `wait_for_submit=False` 时，工具立即返回链接、`session_id` 和 `next_cursor`；原任务须调用 `wait_visual_feedback(session_id, cursor=next_cursor)` 来接收提交。如果等待调用已经结束或超时，反馈仍会保存，但不会自动唤醒空闲任务；可在原任务中重新读取，或使用下文的任务绑定方式。`get_visual_feedback` 可重新读取已提交的反馈。

先在仓库根目录安装上面的依赖。在一个终端中设置绝对路径，注册全局 MCP，再启动独立的审图服务：

```bash
REPO=/absolute/path/to/scene_feedback_harness
PROJECT=/absolute/path/to/your/existing/project
DATA=/absolute/path/to/private/external-review-data
codex mcp add scene_feedback_external \
  --env SCENE_FEEDBACK_PORT=18768 \
  --env SCENE_FEEDBACK_DATA_DIR="$DATA" \
  --env SCENE_FEEDBACK_PROJECT_DIR="$PROJECT" \
  -- "$REPO/.venv/bin/python" "$REPO/backend/mcp_server.py"
"$REPO/.venv/bin/python" "$REPO/backend/server.py" \
  --project-dir "$PROJECT" --data-dir "$DATA" --port 18768 --external-review
```

在 `~/.codex/config.toml` 中，由 `codex mcp add` 创建的 `[mcp_servers.scene_feedback_external]` 表下设置 `tool_timeout_sec = 900`；审图工具的 `timeout_sec` 用默认 600 或更小值，留出传输图像的时间。**重启 Codex 桌面应用**，让现有任务刷新 MCP 工具目录。然后在该任务中说「进入人工调试模式」，或明确要求它调用 `request_visual_feedback`，传入项目内的参考图片路径及现有 GLB 路径（没有初始场景时可省略 GLB）。在未绑定任务的方式下，浏览器的发送按钮完成正在等待的 MCP 调用；如果工具明确选择不等待，则须另行调用 `wait_visual_feedback`。

`PROJECT` 是要审查的工程根目录，传入的参考图和 GLB 必须位于其中；它可以是 Codex 任务工作目录的子目录。`DATA` 是该项目专用的私有目录。示例使用独立的端口、数据目录和 MCP 名称，避免混用上面的默认模式。已发布的场景仍可通过 `workspace_publish_scene` 更新到页面。

### 绑定现有桌面任务：发送后自动继续

如果希望点击「发送反馈」就让**已有的那条 Codex 桌面任务**继续，而不用保持 MCP 工具调用等待，先停止上述服务，再用同一工程和数据目录加上该任务的 UUID 重新启动。`THREAD_ID` 是 Codex 桌面任务 ID，**不是**工作台的 `session_id`：

```bash
THREAD_ID=your-existing-codex-task-uuid
"$REPO/.venv/bin/python" "$REPO/backend/server.py" \
  --project-dir "$PROJECT" --data-dir "$DATA" --port 18768 \
  --external-review --shared-thread-id "$THREAD_ID"
```

工作台会连接**同一用户、同一主机上正在运行的 Codex Desktop App Server**，并把每次提交的文字、原图、标注图和场景截图作为新用户回合送入该任务；它不会新建任务。任务忙时，反馈在队列中等候；页面会显示排队、执行和完成状态。若排队期间场景版本改变，须先确认旧版本反馈。Codex 的审批和交互请求显示在工作台，由人决定；工作台不会自动批准。若连接中断造成送达状态不确定，先检查原任务历史，再决定是否重试，以免重复发送。此方式需要通过 `backend/requirements.txt` 安装的 `websocket-client`。绑定后，`request_visual_feedback` 只需打开或更新工作台并返回链接，**无需等待 MCP 结果**。

连接暂时不可用、或原任务仍在执行时，确定尚未发出的反馈会保留在队列中，条件恢复后自动重试；明确发送失败的反馈可在页面上手动重试。若发送结果无法确认，工作台会按反馈编号核对原任务记录。仍无法确认的反馈会隔离，避免阻塞后续反馈，但**不会自动重发**；请先查看原任务，确认没有收到后再点击「确认未收到，重试」。

想换一个 agent 时，先在 Codex Desktop 打开要接手的对话，再点击顶部当前任务名称，在「Codex 任务」面板的「发送到哪个 Codex 任务」列表中选择它。工作台保留当前场景和参考图，之后的新反馈送到新对话；尚未发送的旧反馈仍属于原对话，切回原对话才会继续发送。模型由 Codex 对话本身决定，切换工作台目标不会修改对话模型。

也可以在这个任务面板展开「新建 Codex 任务」，选择当前账号可用的模型、思考强度和权限模式，再点击「新建并切换」。默认「工作区写入」在必要时请求审批；也可明确选择「完全访问」（`never`，免去常规文件和命令的沙箱审批）或「只读」（写入需审批）。工具自身的授权或交互仍可能要求回应。新任务是空白对话，不复制旧对话记录；场景、参考图和工作台标注仍可继续使用。只有点击按钮后才会创建任务。新建时选择的权限不能追溯改变已经运行或正在等待审批的回合。

## 在局域网的其他设备上打开页面

服务仍运行在保存工程、运行 Codex 和 MCP 的主机上；其他设备只需要浏览器。先停止占用该端口的旧服务，然后使用上文相同的 `REPO`、`PROJECT`、`DATA` 启动局域网服务。把示例 IP 换成**服务主机**在局域网内可访问的 IPv4 地址：

```bash
LAN_IP=192.168.1.10
"$REPO/.venv/bin/python" "$REPO/backend/server.py" \
  --project-dir "$PROJECT" --data-dir "$DATA" --port 18768 --external-review \
  --listen-host 0.0.0.0 --public-base-url "http://$LAN_IP:18768"
```

终端启动信息或 MCP 的 `request_visual_feedback` / `workspace_open` 结果会给出带 `access_token` 的完整链接。把**整个链接**在另一台设备的浏览器中打开；页面验证后会清除地址栏中的令牌，并用浏览器 Cookie 保持访问。不要只输入 `http://$LAN_IP:18768/`，也不要把访问链接发到公开位置。默认工作台模式也可加这两个参数，使用原来的端口和数据目录，并省略 `--external-review`。需要常驻时，可用 systemd 用户服务运行同一条启动命令。局域网开放的是工作台网页及其受保护的 API；MCP 仍由工程主机上的 Codex 经 `127.0.0.1` 在本机调用。如果连接不通，检查主机防火墙是否允许所选 TCP 端口；HTTP 局域网模式适合可信网络。

使用上述桌面任务绑定方式时，在局域网启动命令中同时保留 `--shared-thread-id "$THREAD_ID"`。浏览器可位于局域网的其他设备；连接 Codex Desktop 的服务仍须运行在该桌面任务所在的同一主机和用户下。

## MCP 工具

| 工具 | 用途 |
| --- | --- |
| `workspace_open` | 返回或打开这个项目的工作台地址 |
| `workspace_get_context` | 读取当前参考图、场景版本和项目上下文 |
| `workspace_get_feedback` | 读取已提交反馈及其实际图像 |
| `workspace_publish_scene` | 校验并发布新的 GLB，检查预期版本，通知页面刷新 |
| `workspace_set_reference_clip` | 导入带时间戳及可选逐帧相机的参考图序列，或用 FFmpeg 对项目内的视频抽帧 |
| `workspace_request_feedback` | 默认模式：在页面请求用户检查某处，立即返回；用户的回复会成为下一条用户消息 |
| `request_visual_feedback` / `wait_visual_feedback` | 未绑定的外部 MCP 模式：等待提交并把图文作为工具结果交回；绑定桌面任务时，前者返回页面链接，提交自动创建新回合 |
| `get_visual_feedback` | 外部 MCP 模式：读取已提交的视觉反馈 |

仓库内的 [视觉重建 Skill](.agents/skills/visual-reconstruction-feedback/SKILL.md) 提醒 Codex 区分原图和标记、编辑项目源文件，并在结果就绪时发布 GLB。默认模式直接发送新回合；未绑定的外部 MCP 模式由正在等待的 `request_visual_feedback` 或 `wait_visual_feedback` 返回反馈；绑定桌面任务的模式则在浏览器提交后自动发送新回合。

## 验证与边界

```bash
.venv/bin/python -m unittest discover -s backend/tests -v
```

App Server 的真实联调已用两张不同的本地图像完成：Codex 在第一轮识别红图，关闭并重启 stdio 后，在**同一 thread** 的第二轮识别蓝图；`thread/read` 历史记录包含两轮的 `localImage` 项。这验证了图片确实进入模型，不只是传了图片路径。完整浏览器验收中，人在参考图和场景图上分别画编号框并点击发送；Codex 收到五张实际图像，修改演示场景源文件并导出 GLB，经页面审批后两次成功调用 `workspace_publish_scene`，工作台自动从场景版本 2 刷新到版本 4。随后在网页再次画参考线并发送；Codex 在**同一 thread** 收到第二轮的五张图像并确认，没有修改文件或重复发布。

默认服务只监听 `127.0.0.1`；显式启用上述局域网参数后，页面需要访问链接及 Cookie，提交操作仍需浏览器 capability。为访问本机 MCP Gateway，Codex 的 `workspace-write` 回合设置 `networkAccess: true`。执行审批由用户在页面决定，工作台不会自动批准。运行数据和演示输出均不进入 Git。

项目代码使用 [MIT 许可证](LICENSE)。仓库里的 Three.js 文件保留其[原始 MIT 许可证](web/vendor/three/LICENSE)。
