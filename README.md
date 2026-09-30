**语言 / Languages:** **简体中文** · [English](README.en.md) · [日本語](README.ja.md) · [한국어](README.ko.md) · [Русский](README.ru.md)

[更新日志](CHANGELOG.md) · [维护规则](CONTRIBUTING.md)

# Codex 视觉重建工作台

默认模式下，在参考图片和当前 3D 场景上圈画，写一段提示，点击「发送反馈」。工作台把文字、原图、标注图和场景快照作为**工作台所管理的同一条 Codex 会话中的用户消息**送出；Codex 修改项目并发布 GLB 后，结果回到这个页面。你不需要去终端再触发一次读取。已有 Codex 桌面任务也可以通过下文的外部 MCP 模式使用同一套审图界面。

![参考图、场景与可收起的玻璃会话框](preview.png)

## 插件与 MCP Events（试验）

可将工作台安装为本地 Codex 插件，并为支持 MCP 2.0 的宿主提供事件订阅与反馈读取。当前本地 Codex 的插件安装不保证事件自动唤醒原任务；启用事件模式前需验证宿主订阅与回调。安装、项目绑定、ZIP 打包和 hosted 连接步骤见 [插件与 MCP Events](docs/mcp-events-plugin.md)。

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

页面保留暖白底色和黑灰控件，统一使用系统无衬线字体。标题、正文、操作和辅助文字分别采用清晰的字号层级，常态下精简重复说明，仅在需要时显示排队、断线或送达待核实等状态。宽屏下，参考图与场景并排占据主要空间；顶部工具栏和边距更紧凑，底部是带柔光高光、圆润边缘的半透明玻璃会话浮层。任务、执行记录和引用面板按需打开。

| 入口 | 用途 |
| --- | --- |
| 工具栏 | 「选择」「点」「框」「线」「箭头」；「更多」里是文字、自由画笔和可选对应编号；「物品 / 部件」切换点选层级 |
| 顶部场景名称 | 打开场景列表，切换独立场景；连接 Codex Desktop 后可创建新场景和新的重建任务 |
| 顶部当前任务名称 | 连接 Codex Desktop 时，切换当前场景的接收任务，或选择名称、模型、思考强度和权限新建任务 |
| 底部会话框 | 查看输入框上方的对话记录；「收起记录」只隐藏历史，「收起会话」隐藏整个输入区，右下角「展开会话」恢复 |
| 顶部「记录」 | 查看执行、审批和反馈队列。默认关闭；新的审批或需处理的失败、送达待核实反馈会自动打开一次 |
| 会话顶部「物体与标记」 | 查看引用列表；点「引用」后插入提示光标处，面板关闭，光标回到提示框 |
| 参考图区域「导入」 | 添加参考图片、视频或帧序列，并设置序列帧率；动态素材追加为独立机位 |
| 场景顶部「叠图」 | 一键开启 / 关闭参考图叠加；点击旁边的百分比展开透明度滑杆和常用档位 |
| 参考区机位列表 | 在共享时间轴上切换动态参考机位 |
| 参考区「人体追踪」 | 点击即追踪当前片段全部机位和参考帧；「结果」中查看进度并引用二维骨架 |
| 时间轴「选项」 | 选择反馈范围、时间区间及 GLB 动画动作 |

收起会话后可继续点选和标注，当前未发送的草稿、标记、相机和截图保持原样；插入物体、标记或人体引用时会自动展开输入框。记录展开时，拖动会话框顶部的细条可调整高度，向上拉可显示更多聊天记录；双击恢复默认，也可聚焦细条后用上下方向键微调。收起记录时隐藏拖动条，保持紧凑输入；点击「展开记录」后恢复拖动和之前设定的高度。折叠状态和自定义高度按工作台会话保存在本机浏览器，刷新后恢复；小窗口会自动限制高度。新消息不会自动展开会话，入口会显示未读数量；查看旧消息时不会强制滚到底部，可点击「查看新消息 / 回到最新」。聊天区回放工作台已保存的近期消息，最多显示 100 条；未绑定任务的外部 MCP 模式，后续模型回复仍需在原 Codex 任务查看。

输入框铺满会话宽度，右下角圆形上箭头用于发送；悬停提示和读屏名称会显示「发送反馈」或任务执行时的「加入下一轮」，后者会将反馈排队。也可按 `Ctrl/Cmd + Enter` 发送；中文输入法确认候选字时不会触发快捷发送。查看旧场景快照时，场景顶部工具栏下方会显示类似「标注 v13 · 查看最新 v15 ↗」的按钮；点它查看最新实时场景，本轮未发送的快照和标记仍保留。服务器确认保存反馈后，这些内容会从编辑区清空，完整证据仍保存在已提交的反馈包中。

## 页面能做什么

- 左边切换、缩放和平移参考图；右边旋转、缩放和点选 GLB 场景中的节点。
- 可切换物品级和部件级点选：物品级选中 GLB 中顶层的场景节点（当前素材中对应一个完整物品），部件级选中点击到的具名细分节点。场景中的基本图元对象始终整体选中；同一段提示可以引用任一级别的目标。
- 在两侧画点、框、线、箭头、自由笔迹和文字。对应标记可以编号；缺失物体也可以只圈在参考图上。
- 工具栏「撤销 / 重做」可回退或恢复标记的添加、删除和清空；快捷键是 `Ctrl/Cmd + Z` 与 `Ctrl/Cmd + Shift + Z`。「清空」一键移除全部标记、固定截图和已保存的动态时刻，并回到实时 3D 场景，可撤销；提示文字和参考图不受影响。提示输入框内仍使用原生文字撤销。标注历史只保留在当前页面，刷新或反馈保存成功后重新开始；撤销不会把已提交的标记恢复到新一轮。
- 只写一段给 Codex 的提示。把光标放在文字里，点击物体、已点选的 GLB 节点或标记旁的「引用」，就能在同一段提示中插入多个引用，例如 `[[object:ID]]`、`[[node:MODEL_ID:0/2]]`、`[[annotation:ID]]`。可以写“把这个柜子移到左图标记的位置，再对齐那条边线”，然后一次发送。尚未建模的东西也可以先在参考图上标记并引用；场景一次仍只高亮一个选中对象。 在「物体与标记」面板点击「引用全部标记」，可在当前光标处一次插入所有尚未引用的标记（含已保存动态时刻），已有引用不重复插入；面板随后关闭，供你继续写提示。
- 选好绘图工具后，直接在右侧点击或拖动就能标注；系统会自动保存**当时的截图、相机、选中对象和场景版本**。也可先点「标注」提前固定画面。静态场景在同一视角和版本下继续圈画会复用原截图；更换已有标注的静态截图前，会提示清除旧场景标记。取消后保持原状，确认后的更换可撤销恢复；动态场景按各时刻保留截图。转动实时 3D 视图不会让旧标记漂到别的物体上；新场景发布也不会覆盖正在标注的快照。
- 插入标记引用、点击工具栏「选择」或场景区「返回 3D」后，会回到实时 3D 场景继续点选。本轮未发送的固定截图和已有标记仍保留；点击「标注截图」可回到保存的原视角，继续在截图上标注。右侧箭头等场景标记始终跟随原截图保存；之后转动实时 3D 视角或刷新页面都不会覆盖它。发送会带上标记的原截图和原相机，而不会把箭头贴到当前旋转后的画面上。
- 发送时保存不可变的反馈包：用户原话、参考原图与标注图、干净与标注后的场景截图、选中节点、相机及场景版本。标记表示人的提示，原图仍单独保留。
- 服务器确认通过 HTTP 保存反馈后，会自动清空本轮提示、全部标记、固定截图、已保存的动态时刻、物体/节点选择及本轮引用，回到实时 3D，并保存空白草稿；即使反馈仍在排队或尚未确认投递到 Codex，也会开始新一轮。已提交反馈中的证据、参考图和当前动画时间仍保留；新场景结果到达时，下一轮已开始的草稿继续保留。若 HTTP 保存失败或结果未确认，本地草稿和待发送记录会保留，方便修改和重试。
- 执行中的新反馈进入下一轮队列。排队期间场景更新后，已保存的反馈仍自动投递；模型会收到原截图版本与投递时的当前版本，并按旧证据理解你的提示。即使发送时截图已是旧版本，点击「发送」也会直接提交，无需再次确认；模型仍会看到截图的真实版本。审批请求和停止操作也在页面处理。
- 刷新页面会恢复项目、会话，以及本轮未发送的草稿和原截图，不会把旧截图自动变成新版本；已保存成功的一轮不会重新出现在编辑区。「查看最新」只切换到实时场景，未发送的旧证据仍保留；要重新标注当前结果，可先「清空」再圈画，误清可以撤销。截图和已保存反馈的版本号始终保持真实，不会被改写。

目前场景输入是自包含的 `.glb`；发布校验要求几何和纹理资源嵌入 GLB 的 BIN 块，外部 URI 和 data URI 都会被拒绝。参考图可以单独提交，所以没有初始场景也能开始。

## 切换场景与新建重建任务

点击顶部场景名称，打开场景列表。可以切回已有场景，也可以输入新场景名称、选择模型、思考强度与权限，创建**新场景和新的 Codex 桌面任务**。导入新参考图片或视频，在底部提示框写重建要求并发送，即可开始；不需要先有 GLB。

每个场景独立保存参考素材、相机、动态机位、重建结果、反馈记录和接收任务。切换前保存当前草稿；回到原场景可继续标注。不同标签页可查看不同场景，旧任务的执行与排队反馈继续留在原场景。提交或导入尚未完成时，先等该操作结束再切换。

新场景在服务数据目录的 `projects/<project_id>/workspace` 下建立工作目录，使用同一 LAN 地址和端口。新任务的 MCP 单独绑定新目录，旧 MCP 仍访问原场景。任务创建结果不确定或绑定失败时，已建立的场景会留在列表中供恢复；重复同一创建请求不会再建一个任务。

新建桌面任务需要工作台已经连接 Codex Desktop（`--external-review --shared-thread-id`）；浏览器可以在其他 LAN 设备上使用。原来的任务入口用于切换或新建**当前场景内**的任务。

## 对齐参考相机视角

选中带相机位姿的参考图时，右侧 3D 场景会自动切到该图的拍摄视角。场景顶部的「叠图」可一键开关，旁边的百分比展开透明度滑杆与 25% / 45% / 75% 档位；首次默认 45%，调到 0% 可隐藏，再次开启恢复上次非零透明度。开关与透明度按工作台会话保存在当前浏览器；查看标注截图时显示「暂停」，返回 3D 后恢复原设置。手动旋转场景后，点「对齐」即可回到该机位。若提供了去畸变副本，叠图会使用它来匹配针孔相机投影；原始参考图仍单独保存，用于查看和反馈。没有相机标定的图片仍可手动对照；使用近似内参时，边缘仍可能有残余偏差。

每张图的相机元数据包含 GLB 世界坐标中的 `camera_to_world`（按行排列的 4×4 矩阵）和以该图像素为单位的 `intrinsics`（`width`、`height`、`fx`、`fy`、`cx`、`cy`）。已有图片可通过受保护的 `POST /api/workspace/reference-cameras` 接口按 `reference_id` 附加 `camera` 与可选的 `alignment_image_data_url`；未来导入的图片可由工作台数据目录中的 `reference_cameras.json` 清单按文件名前缀匹配（[示例](examples/reference_cameras.example.json)）。放入清单后，对已有图片发送 `{"apply_manifest":true}` 即可应用。相机位姿应与发布的 GLB 使用同一世界坐标系。

## 动态场景：多机位共享时间轴与多帧反馈

同一片段的多个参考机位（图片序列或视频）可以与带动画的 GLB 共用时间轴。播放、暂停、拖动时间轴和逐帧前后跳转，会同时更新参考帧与 3D 动画；静态图片和静态 GLB 的原有流程仍可使用。GLB 支持节点位移、旋转、缩放、骨骼和固定拓扑的 morph 动画；可变拓扑网格缓存、流体与实时物理模拟尚未接入。

- 切换参考机位时保持当前时间，并自动按新机位当前帧的相机对齐；没有相机数据的机位可手动比较。各机位的 `time_sec` 应使用同一时间起点。一次反馈可引用多个机位的标记；每个标记保留所属机位名称、`view_id`、帧号/时间、原图及相机。
- 相机标定唯一匹配到某个动态机位的参考缩略图标为「同步」；点击后切换到该同步机位，拖动进度条继续使用它。未匹配或有多个匹配的缩略图仍作为静态参考；左侧机位选择器可直接选择动态机位。
- 暂停时可查看静态缩略图；播放、拖动时间轴或前后切帧会自动回到动态参考序列，新发布的序列也会恢复同步。已有标记仍保留在原始帧上。
- 开始圈画时自动暂停，标记记录当时的时间、参考帧、相机和场景版本。可以保留所有机位合计最多 8 条时刻快照，切回对应时间点继续查看或标注。
- 一次提示可以引用多个机位、多个时间点的标记和物体，并在时间轴「选项」中选择「所标帧」「时间区间」或「整个片段」作为反馈范围。区间只是表达修改范围，不会把画线变成轨迹或几何约束。
- 单独或批量引用动态标记时，提示中显示「片段第 N 帧 · T s」；引用同时标明机位名称，对应结构化反馈保留 `view_id`；帧号按该机位导入或采样的序列从 1 开始，保存后的标记不会随时间轴移动而改变帧号和时间。送给模型的 `frame_index` 按保存的参考图 ID 与该机位的序列顺序从 0 开始计算；静态参考图或仅 GLB 的标记只附时间。
- 标记默认只显示在所属机位的对应帧上。发送时交付所选时间点的原图、标注图和场景快照，以及时间戳、相机、对象引用与场景版本；不会把整段视频逐帧塞给模型。
- 每帧可带自己的相机位姿，切帧时按对应相机对齐。更新 GLB 后保留当前播放位置；拖动时间轴不会增加 `scene_revision`。
- 导出 GLB 时，可在节点的 `extras` 中保留 `semantic_id` 或 `stable_id`，帮助模型跨帧及重新导出识别同一物体。节点路径只定位当前版本内的节点；名称和路径不保证跨版本不变，也不会自动跟踪物体。

在参考图区域的「导入」菜单中，可多选视频，每个视频分别追加为一个机位，由服务端抽帧；有序图片每次导入组成一个机位，使用指定 FPS。浏览器导入会追加机位，并保留旧机位和已有标记。视频导入需要服务主机安装可执行的 `ffmpeg` 和 `ffprobe`；图片序列不需要它们。视频按公共时间网格采样，默认 10 FPS。最多 8 个机位，每个机位最多 600 帧；超过上限会报错，请降低采样率或先截取所需片段。

Codex 也可调用 `workspace_set_reference_clip(manifest_path=None, video_path=None, fps=None, camera_manifest_path=None, clear=False, append_view=False, view_name=None)` 导入项目内的素材；`manifest_path` 与 `video_path` 二选一。`append_view=True` 追加机位并保留旧素材和标记，`view_name` 可指定新机位名称。默认 `append_view=False` 延续旧接口的替换行为，会替换整组旧机位；替换前请先提交依赖旧帧的反馈。`clear=True` 移除整组参考机位，不改变场景版本。

图片序列使用清单自己的 `fps`（省略时为 30），工具参数 `fps` 仅用于视频（默认 10）。每帧可指定 `time_sec`；省略时按从 0 开始的帧序号除以清单 FPS 计算，不从文件名推测帧率。FPS 范围为 0.1–120。容量上限按机位独立检查：浏览器视频文件或图片序列总量为 40 MiB；项目本地视频文件、解码帧总量或图片序列总量为 250 MiB。所有输入文件必须位于项目内。

现有单片段会作为主机位继续使用，原来的 `clip_id`、帧与标记保留。同一 `reference_clip` 顶层保留主机位字段，`views` 数组只存附加机位；追加时主 `clip_id` 保持稳定，各机位自身的 `clip_id` 用作 `view_id`。这项功能组织多机位查看与反馈，几何修改仍由同一 Codex 任务完成。

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

多机位批量清单见 [reference_multiview.example.json](examples/reference_multiview.example.json)：顶层可设 `name`，`views` 中每项可设机位 `name`、`fps`、`duration_sec`，并从 `frames`、`manifest_path`、`video_path` 中选择一种素材来源。帧条目支持 `path`、可选的 `time_sec`、`name` 和 `camera`；视频可附 `camera_manifest_path`。相对路径基于外层清单，嵌套清单的帧路径基于该清单自身。第一项作为主机位；`append_view=True` 将整批追加到现有组。示例相机数值须替换为实际标定。

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
workspace_set_reference_clip(manifest_path="/absolute/path/to/project/reference_multiview.json", append_view=True)
workspace_set_reference_clip(video_path="/absolute/path/to/project/camera_B.mp4", fps=10, camera_manifest_path="/absolute/path/to/project/video-cameras.json", append_view=True, view_name="camera_B")
```

## 人体关键点：一键追踪所有机位与参考帧

点击参考区工具栏的「人体追踪」就会直接开始；旁边的「结果」显示进度和已完成任务。工作台会对**当前动态片段的所有已导入机位和每一张已导入参考帧**运行人体检测与 ViTPose，无需框人、选机位、设时间范围或采样率。没有动态片段时，会处理当前会话的全部静态参考图。任务异步执行，显示已处理帧数与整体进度。动态参考最多 8 个机位、每机位 600 帧，合计最多 **4800 帧**；这里的“每帧”指导入工作台的参考帧，视频导入时已抽帧的原视频画面不会自动补回。各机位的 `time_sec` 须有共同起点，结果才能按时间对照。

完成后会在当前机位和时刻显示对应原帧的 COCO17 二维骨架，并注明实际来源帧。可查看结果、下载包含全部机位的 JSON，或点「引用人体」将 `[[pose:任务ID:原帧ID]]` 插入统一提示。同一反馈最多引用 8 个人体样本，可来自不同任务或机位。发送时，服务器读取已保存结果，附带**推理原帧、青色估计骨架图、置信度、人物框、机位、帧号、时间与已有相机数据**；估计与红色人工标记分开，原图不被改写。反馈保存成功后清空本轮人体引用，已完成任务继续保留。

结果按场景保存，并按机位分别记录二维轨迹。切换机位只显示该机位的骨架；可按 `view_id` 分页查询，完整 JSON 保存全部样本。替换片段后仍可读取旧任务的原始证据。每个场景一次只运行一个人体任务，服务内各场景共用一个推理名额；长任务的进度会持续保存，可通过 MCP 取消。服务重启会把未完成的任务标为中断，需重新点击开始。

这是 **ViTPose+ Base 的单人 COCO17 二维估计**。系统在每个机位自动检测并选择一个最显著的人，沿该机位跟踪，丢失时重新检测；不会追踪画面中的所有人。多人出现、遮挡或重检后可能选到不同的人，**不保证各机位追的是同一个人**，也不融合关节或三角化三维姿态。低可信、画外或丢失人物的骨架不显示。不提供手指关键点或自动驱动 Blender 骨骼；请对照原图核验结果。JSON 坐标相对各自原图归一化，并保留原始宽高和来源。

### 可选推理环境

HTTP 服务不加载模型，推理在独立进程运行。推理环境需安装 `torch`、匹配其 ABI 的 `torchvision`、`transformers`、`Pillow`、`numpy`、`scipy`、`opencv-python`，并准备本地 ViTPose+ Base 权重目录（含 `config.json`、`preprocessor_config.json`、`model.safetensors`）。一键自动选人还需要本地 Faster R-CNN MobileNet V3 320 COCO 检测权重；服务运行时不会隐式下载。启动前设置：

依赖清单：[requirements-pose.txt](backend/requirements-pose.txt)；[ViTPose+ Base 权重](https://huggingface.co/usyd-community/vitpose-plus-base)；[Transformers ViTPose 文档](https://huggingface.co/docs/transformers/model_doc/vitpose)；[PyTorch 检测器文档](https://docs.pytorch.org/vision/2.0/models/generated/torchvision.models.detection.fasterrcnn_mobilenet_v3_large_320_fpn.html)与[官方检测权重](https://download.pytorch.org/models/fasterrcnn_mobilenet_v3_large_320_fpn-907ea3f9.pth)。

```bash
export SCENE_FEEDBACK_POSE_PYTHON=/absolute/path/to/pose-env/bin/python
export SCENE_FEEDBACK_POSE_MODEL=/absolute/path/to/vitpose-plus-base
export SCENE_FEEDBACK_POSE_DETECTOR=/absolute/path/to/fasterrcnn_mobilenet_v3_large_320_fpn-907ea3f9.pth
```

依赖或权重缺失时面板显示原因；未配置仍可圈画和发送普通反馈。自定义 `SCENE_FEEDBACK_POSE_RUNNER` 可指定 JSON 参数数组，必须包含输入清单 `{manifest}` 与输出文件 `{output}` 占位符。此前已用 RTX 5060 验证 ViTPose 推理及反馈图像、MCP 图像传递。

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

工作台会连接**同一用户、同一主机上正在运行的 Codex Desktop App Server**，并把每次提交的文字、原图、标注图和场景截图作为新用户回合送入该任务；它不会新建任务。任务忙时，反馈在队列中等候；页面会显示排队、执行和完成状态。排队期间场景版本改变不会阻止已保存反馈投递；模型同时看到截图版本与投递时的当前版本。Codex 的审批和交互请求显示在工作台，由人决定；工作台不会自动批准。若连接中断造成送达状态不确定，先检查原任务历史，再决定是否重试，以免重复发送。此方式需要通过 `backend/requirements.txt` 安装的 `websocket-client`。绑定后，`request_visual_feedback` 只需打开或更新工作台并返回链接，**无需等待 MCP 结果**。

历史上因版本变化而停在 `blocked_stale` 的反馈，只要已保存固定截图且没有启动过 Codex 回合，会安全恢复到队列并自动投递；截图证据与版本保持原样。连接暂时不可用、或原任务仍在执行时，确定尚未发出的反馈会保留在队列中，条件恢复后自动重试；明确发送失败的反馈可在页面上手动重试。若发送结果无法确认，工作台会按反馈编号核对原任务记录。仍无法确认的反馈会隔离，避免阻塞后续反馈，但**不会自动重发**；请先查看原任务，确认没有收到后再点击「确认未收到，重试」。

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
| `workspace_track_human_pose` | 用 `all_views=true` 一键追踪当前片段全部机位及全部已导入帧；无片段时处理全部静态图。原有手框与单机位参数仍可用；立即返回 job_id |
| `workspace_get_human_pose` | 读取环境、进度与 COCO17 结果；可用 `view_id` 只查一个机位，`frame_offset` / `max_frames` 分页，默认 8 帧、最多 32 帧，返回完整 JSON 的本地路径 |
| `workspace_cancel_human_pose` | 取消当前场景内排队或运行中的人体任务，保留完成结果 |
| `workspace_publish_scene` | 校验并发布新的 GLB，检查预期版本，通知页面刷新 |
| `workspace_set_reference_clip` | 导入单机位或多机位清单，或用 FFmpeg 对项目内的视频抽帧；`append_view=True` 追加机位 |
| `workspace_request_feedback` | 默认模式：在页面请求用户检查某处，立即返回；用户的回复会成为下一条用户消息 |
| `request_visual_feedback` / `wait_visual_feedback` | 未绑定的外部 MCP 模式：等待提交并把图文作为工具结果交回；绑定桌面任务时，前者返回页面链接，提交自动创建新回合 |
| `get_visual_feedback` | 外部 MCP 模式：读取已提交的视觉反馈 |

仓库内的 [视觉重建 Skill](.agents/skills/visual-reconstruction-feedback/SKILL.md) 提醒 Codex 区分原图和标记、编辑项目源文件，并在结果就绪时发布 GLB。默认模式直接发送新回合；未绑定的外部 MCP 模式由正在等待的 `request_visual_feedback` 或 `wait_visual_feedback` 返回反馈；绑定桌面任务的模式则在浏览器提交后自动发送新回合。

## 验证与边界

```bash
.venv/bin/python -m unittest discover -s backend/tests -v
```

浮层布局的浏览器回归测试使用临时数据和本机随机端口，不启动 Codex，也不读取真实会话。安装可选的 Playwright 与 Chromium 后运行：

```bash
.venv/bin/python -m pip install playwright
.venv/bin/python -m playwright install chromium
.venv/bin/python tests/focus_workspace_smoke.py
```

覆盖会话与记录独立收起、未读提醒、历史回放、未发送草稿与固定截图恢复、引用插入、发送去重、时间轴和窄屏布局。

App Server 的真实联调已用两张不同的本地图像完成：Codex 在第一轮识别红图，关闭并重启 stdio 后，在**同一 thread** 的第二轮识别蓝图；`thread/read` 历史记录包含两轮的 `localImage` 项。这验证了图片确实进入模型，不只是传了图片路径。完整浏览器验收中，人在参考图和场景图上分别画编号框并点击发送；Codex 收到五张实际图像，修改演示场景源文件并导出 GLB，经页面审批后两次成功调用 `workspace_publish_scene`，工作台自动从场景版本 2 刷新到版本 4。随后在网页再次画参考线并发送；Codex 在**同一 thread** 收到第二轮的五张图像并确认，没有修改文件或重复发布。

默认服务只监听 `127.0.0.1`；显式启用上述局域网参数后，页面需要访问链接及 Cookie，提交操作仍需浏览器 capability。为访问本机 MCP Gateway，Codex 的 `workspace-write` 回合设置 `networkAccess: true`。执行审批由用户在页面决定，工作台不会自动批准。运行数据和演示输出均不进入 Git。

项目代码使用 [MIT 许可证](LICENSE)。仓库里的 Three.js 文件保留其[原始 MIT 许可证](web/vendor/three/LICENSE)。
