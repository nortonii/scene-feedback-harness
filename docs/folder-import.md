# 从服务主机文件夹导入已准备场景

## 空白工作台入口

启动独立空白入口：

```bash
.venv/bin/python scripts/start_blank_workbench.py
```

默认访问 `http://127.0.0.1:18776/`。首页显示「打开文件夹」，导入后从左侧列表选择场景。直接打开根地址始终进入空白首页，已有导入实例保留在列表，卸载当前场景后返回空白首页。

默认工作目录与数据目录分别为 `~/.local/share/scene-feedback/blank-workbench/workspace` 和 `~/.local/share/scene-feedback/blank-workbench/data`。可用 `--project-dir`、`--data-dir` 指定独立位置；它们不复用旧重建服务的数据。误指定含模型、参考图、任务或反馈的根数据时，启动器报错并保留原数据，需选择新目录。

局域网启动示例（将 IP 换为服务主机地址）：

```bash
.venv/bin/python scripts/start_blank_workbench.py \
  --port 18776 \
  --listen-host 0.0.0.0 \
  --public-base-url http://192.168.3.157:18776 \
  --lan-access open
```

此模式可直接收藏根地址 `/`，不需要会话参数或访问凭证链接。通过工作台服务主机文件夹导入场景；启动采用 MCP Events 模式，不自动创建或绑定 Codex 任务。图文反馈仍按该场景的插件订阅方式接入模型。

## 打开与导入

在侧栏「场景」旁点击文件夹图标「打开文件夹」。这里浏览的是**工作台服务所在机器**的目录；通过 LAN 打开的浏览器不会读取客户端电脑的本地文件夹。可以逐级浏览，也可以粘贴服务主机上的绝对路径。

点击「打开并导入」后，服务在扫描边界内递归识别已准备实例，并把成功导入的场景加入列表。当前正在编辑的场景、草稿和接收任务保持原样；导入不会自动创建 Codex 任务。之后可从列表选择导入的场景，需要模型继续处理时再明确连接或创建任务。

每个导入场景独立保存工作台数据与反馈。导入过程只读取来源素材，不改原 GLB、可编辑 `.blend`、参考图或参考清单。

## 标准实例标识

标准实例在根目录放一份 `workbench-ready.json` 或 `workbench_ready.json`；这两个文件名都支持下表的 `schema_version: 1` 格式。保留其中一份即可，同时存在会报告标识歧义。已有视频 ready 包另按下文的兼容字段读取，无须补 `schema_version`。

| 字段 | 含义 |
| --- | --- |
| `schema_version` | 必须为整数 `1` |
| `name` | 可选场景名；省略时使用实例目录名 |
| `scene_glb_path` 或 `glb` | 要导入的 GLB 路径 |
| `reference_manifest` | 可选动态参考清单路径 |
| `reference_clip: {"manifest_path": "…"}` | 动态参考清单的另一种写法 |
| `reference_images` | 可选静态参考图路径列表 |
| `ready` | 明确为 `false` 时跳过该实例 |

必须有参考清单或至少一张静态参考图，也可同时提供两者。GLB、参考图、视频与清单仍使用工作台既有的文件和素材校验；标识存在不代表损坏的素材可以导入。

### 静态参考图实例

实例根目录中的 `workbench-ready.json`：

```json
{
  "schema_version": 1,
  "name": "静态物品",
  "scene_glb_path": "output/scene.glb",
  "reference_images": [
    "references/front.png",
    "references/side.png"
  ]
}
```

### 动态／多机位实例

实例根目录中的 `workbench_ready.json`：

```json
{
  "schema_version": 1,
  "name": "动态片段",
  "glb": "output/scene.glb",
  "reference_clip": {
    "manifest_path": "references/multiview.json"
  }
}
```

也可用 `"reference_manifest": "references/multiview.json"` 指定同一份清单。动态清单保留机位、帧、精确 `time_sec` 与相机信息，格式沿用 [README 的动态参考说明](../README.md)。支持清单内直接声明 `views[].frames`，也支持 `views[].manifest_path` 指向各机位的帧清单。

例如，选中的父目录可以包含这两个独立实例：

```text
/absolute/path/ready-scenes/
├── static-item/
│   ├── workbench-ready.json
│   ├── output/scene.glb
│   └── references/
│       ├── front.png
│       └── side.png
└── motion-item/
    ├── workbench_ready.json
    ├── output/scene.glb
    └── references/
        ├── multiview.json
        └── camera-A/
            ├── frames.json
            └── frame_000.png
```

## 已有视频 ready 包兼容

已有实例根目录的 `workbench_ready.json` 也可使用 `status: "ready"`、`scene_glb` 与 `reference_sequence`，不要求重写成标准标识或添加 `schema_version`。例如：

```json
{
  "status": "ready",
  "name": "A · Escalator",
  "scene_glb": "dynamic_scene.glb",
  "editable_blend": "dynamic_scene.blend",
  "reference_sequence": "reference_clip.json",
  "camera_manifest": "camera_manifest.json",
  "world_up": "Z"
}
```

`scene_glb` 指定现有模型，`editable_blend` 指向可编辑源文件，`reference_sequence` 指向已有视频帧清单。相对路径以实例根目录为基准，仍须满足实例内路径边界。单机位的 `camera_manifest` 会导入对应相机，若序列也声明 `camera_manifest_path`，两者须指向同一文件；多机位在各自的参考清单中声明相机。帧时间与相机标定直接沿用，无须重新准备素材。`world_up` 可指定 `Y` 或 `Z`，作为自动地面方向，不旋转原几何。

例如一个父目录下的 `A`–`H` 子目录可分别放这种 ready 包。已有 `reference_clip.json` 的 120 帧、24 FPS、5 秒片段继续作为该实例的参考序列，原 GLB、`.blend` 与相机文件不被改写。

## 父目录的场景清单

同时支持父目录中的 `manifest.json` 以 `scenes` 列表声明实例，不要求每个子目录再提供 ready 标识：

```json
{
  "scenes": [
    {
      "name": "消防车",
      "folder": "fire_truck",
      "glb": "fire_truck/scene.glb",
      "blend": "fire_truck/scene.blend"
    }
  ]
}
```

`folder`、`glb`、可选 `blend` 和参考路径相对于清单所在目录解析；模型与参考素材必须位于声明的实例目录内。按清单指定的模型导入，不搜索或猜测另一份模型。`source_glb`、`source_blend`、`workbench_url` 等来源记录不用于读取外部素材或打开其他工作台。

此格式允许只包含模型的实例：缺少参考图时仍可加入场景列表，界面提示「只包含模型，尚无参考图」。已有 `reference_manifest`、`reference_images` 等可选参考字段会随模型导入。实例名称沿用清单，重复来源复用现有实例，单个坏条目不影响其他模型。

## 路径与兼容布局

标识中的素材路径可使用绝对路径，或相对于**实例根目录**的路径。路径解析后的真实文件必须仍在该实例根目录内；通过 `..` 或符号链接逃出实例的路径会拒绝。

参考清单内的路径则相对于**声明它的清单所在目录**解析。例如，`references/multiview.json` 的 `views[].manifest_path` 写 `camera-A/frames.json`，而该机位清单中的帧路径写 `frame_000.png`。帧路径、视频路径、相机清单及嵌套机位清单都须满足同一实例根目录边界。

已有目录也支持以下识别方式，优先级在实例根目录 ready 标识之后：

- `workbench/active_scene.json`：读取现有 `glb`、`reference_manifest` 与可选 `blend` 字段；相对路径仍以实例根目录为基准。`blend` 保留可编辑来源信息，不在导入时修改。
- `references/multiview.json` 加 `output/` 中唯一的 `.glb`：清单必须是多机位组，输出目录有且只有一份 GLB 才能选定模型。多个 GLB 时须添加显式实例标识指定模型；不会按修改时间、文件名或旧脚本猜测。

识别到一个实例后，不再把它内部的素材与历史树继续当作独立实例扫描。缺文件、坏 JSON、无参考资料或模型歧义会记录该实例的原因，继续处理其他实例。

## 扫描边界与结果

默认以所选目录为深度 `0`，最多扫描至深度 `5`，累计最多检查 `20,000` 个目录条目。目录浏览列表也可能因条目上限被截断；结果中的截断提示表示尚有未扫描或未显示内容，可选择更具体的子目录继续。

递归扫描不进入隐藏子目录，不跟随子目录符号链接，并跳过素材、历史与环境子目录：

```text
workbench references output review revisions history
archive archives backup backups frames images rgb
undistorted renders media assets node_modules venv env
core_env vision_env __pycache__ site-packages
```

这些目录中约定的实例标识与素材路径仍可被读取；跳过的是把子目录当成更多独立实例的递归搜索。`ready:false` 的实例会列为跳过，扫描上限会标记 `truncated` 并说明边界。

导入结果包含新增、跳过与失败项及各自原因；界面显示数量、失败详情和扫描边界提醒。单个实例失败不撤销其他已成功实例。重复导入同一来源目录复用已有场景，不覆盖它的工作台数据或反馈；相同请求重试也复用已成功的来源。结果未确认时可重试原请求，修复失败素材后也可再次导入。

## 卸载与重新导入

导入或新建场景的列表行提供「卸载」，在同一行确认后从列表移除并卸载运行中的工作台，包括暂时不可用的场景。默认场景不可卸载；卸载当前场景前保存提示草稿，随后返回默认场景。卸载状态在服务重启后保留。

卸载保留来源 GLB、`.blend`、参考素材、已修改的工作台结果、会话、反馈与 Codex 任务，不删除文件或取消任务。执行中的回合、尚未解决的送达核实或进行中的操作会阻止卸载并显示具体原因，先完成处理后可重试。

再次导入同一个来源目录会重新显示原场景，并复用原项目、会话、已修改模型与参考数据，不从来源重建初始状态、不覆盖编辑成果，也不创建新 Codex 任务。

## 接口摘要

| 接口 | 请求 | 返回要点 |
| --- | --- | --- |
| `POST /api/projects/folders` | 可选绝对目录 `path` | 当前 `path`、`parent`、子目录 `directories` 与 `truncated` |
| `POST /api/projects/import-folder` | 绝对目录 `path`、32 位小写 UUID hex `request_id` | `projects`、`imported`、`skipped`、`errors`、`truncated` 与扫描计数 `counters` |
| `DELETE /api/projects/<project_id>` | 要卸载的场景 ID；使用当前工作台的浏览器访问权限 | `unloaded_project_id` 与默认场景元数据 `fallback`；同一请求可安全重试，受保护或仍有操作的场景返回具体原因 |

同一个 `request_id` 只能用于同一个文件夹，也不能复用新建场景的请求编号。接口结果用于刷新场景列表，不指定或切换当前正在编辑的场景。
