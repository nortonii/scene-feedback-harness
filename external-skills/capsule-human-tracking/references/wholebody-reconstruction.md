# WholeBody133：固定身体和双手重建

使用这条路线消费 **真实、来源绑定的 WholeBody133 二维观测**，或已有的具名三维手部输入。它独立于旧 `mhr127-hot3d21` 模板与裤腿轮廓 refiner，不把 COCO17 补零成 MHR127，也不从单张图声称恢复了米制人体。

工具依赖 NumPy、Pillow；构建可编辑资产另需 Blender。既有 MHR/HOT3D 命令继续使用原来的配置与索引。

## 先确认来源、人物及相机

1. 使用 `workspace_export_pose_sources` 获得 `input.json`；用独立观察器生成 `coco-wholebody133` 的来源绑定结果。`pose_evidence.py check-result --help` 提供结果核对入口。人工改点使用 `apply-corrections` 生成新的合并证据，保留原结果。
2. 明确各机位的轨迹确实是同一个人，并记录验证来源。多个独立二维跟踪的同名 `track_id` 不构成跨机位身份保证。
3. 每个使用帧必须有对应校准相机，像素尺寸与参考图完全一致。声明真实约定：`threejs` 为相机 +X 右、+Y 上、-Z 前；`opencv` 为 +X 右、+Y 下、+Z 前。相机世界坐标的上轴不会从图片推断。工作台当前标准导出的相机使用 `threejs`；其他导入需依据来源。
4. 本工具拒绝非零未校正畸变。先准备确实去畸变的图像、相应观测与内参；不要把原图坐标当作去畸变坐标。源文件 SHA256、EXIF 显示尺寸、引用 ID、机位、时刻与绑定必须一致。

配置示例（路径相对配置文件）：

```json
{
  "schema_version": 1,
  "manifest_path": "input.json",
  "observations_path": "wholebody_result.json",
  "camera_convention": "threejs",
  "association": {
    "actor_id": "person_1",
    "identity_verified": true,
    "verification": "已人工核对同步画面的同一人物，保留核对记录路径",
    "view_track_ids": {
      "实际机位ID1": "导出结果中的track_id",
      "实际机位ID2": "导出结果中的track_id"
    }
  },
  "confidence_threshold": 0.3,
  "max_reprojection_px": 4,
  "min_ray_angle_deg": 0.5,
  "sync_tolerance_sec": 0.000001,
  "update_gain": 0.7,
  "manual_visible_weight": 0.8,
  "wrist_merge_fraction": 0.35,
  "fps": 30
}
```

`view_track_ids` 必须包含导出的所有机位，值严格对应这一份结果的 `track_id`；它记录明确确认，不执行自动身份匹配。同步只选择容差内的真实来源帧，不插值二维像素。需要容忍已知采样抖动时明确设置 `sync_tolerance_sec`，最多 0.02 秒，并核对报告的来源帧。

没有独立尺度依据时，输出单位仅是相机世界单位。只有明确的尺度标定才能加入 `source_unit_in_meters` 与非空 `metric_scale_provenance`；这个声明不会替你估计尺度或自动变换已有场景。

## 重建与因果更新

```bash
python3 scripts/capsule.py wholebody-reconstruct \
  --config /path/config.json --output /path/new-run

# 独立前缀回放，不读取未来几何来初始化过去
python3 scripts/capsule.py wholebody-reconstruct \
  --config /path/config.json --frames 20 --output /path/new-prefix
```

每个关节通过至少两个合格机位的加权 DLT 与机位对一致性筛选；检查正深度、视差和像素重投影。报告列出使用机位、关节来源、失败原因、重投影误差和每帧的 hold/update 情况。人工 `manual_source=manual_2d`、`manual_position=true` 且 `manual_visibility=visible` 的点使用独立拟合权重，原模型 `score` 保留；`occluded` 与 `missing` 不当作实测位置。三维投影父结果不会作为二维观测输入这条路线。

身体、头、左右脚与左右手各在首个可靠时刻初始化一次。每手 21 点按腕根与五指各四关节排序，生成 20 条指段。之后使用上一接受状态和当前观测更新根位置/骨方向，长度及半径固定；缺失观测保留前态，没有无约束速度外推。初始化某只手需要同一时刻完整、可靠的 21 点；不足时报告 `partial` 或 `unsupported`，没有可见的臆造手指。

WholeBody 手根 91/112 与身体腕 9/10 表示同侧同一位置。两套二维估计经三角化后显式融合；超出相对于前臂尺度的允许差距时拒绝该手。已初始化身体的手根锚定到身体接受后的腕关节，避免手与前臂断开。没有可靠身体但有可靠手时，可以交付明确的独立手部部分结果。

模型保留 133 个源索引，另外 133/134 是显式定义的髋中心与肩中心虚拟节点；不会改动原始关节顺序。脸部 68 点可有独立三角化证据，但不构建随帧变形的脸模型。`available=false` 的模型关节在 NPZ 中保持 NaN，不能当成原点关节使用。

输出：

- `wholebody_template.json`：一次确定的长度、半径、边、初始化时刻、具名节点及来源约定。
- `wholebody_motion.npz`：接受后的位置、可用性、前态增量，以及独立三角化位置/有效性。新增关节出现时增量不冒充来自更早的不存在状态。
- `report.json`：来源绑定、输入哈希、逐关节三角化与逐帧模型拟合误差、初始化/不支持部位及固定几何验证。
- `reconstruction_config.json`：本次显式参数。输出目录必须为空；工具不会发布到线上工作台。

可调用 `wholebody_reconstruction.validate_motion(model, motion, prefix)` 核对固定骨长、递推和独立前缀一致性。它验证这条估计路线，不能替上游二维模型提供因果性保证。

## 已有三维手输入

配置可添加 `hands_3d_path`，明确它来自真实已知三维轨迹；不从 COCO17 生成此文件。结构为：

```json
{
  "schema_version": 1,
  "project_id": "与导出相同",
  "session_id": "与导出相同",
  "source_snapshot_id": "与导出相同",
  "actor_id": "person_1",
  "coordinate_frame": "camera_world",
  "joint_order": "wholebody_hand21",
  "provenance": {"method": "实际三维跟踪方法", "source_artifact": "原始轨迹文件"},
  "frames": [{
    "time_seconds": 0,
    "left": {"joints": [[0, 0, 0]], "scores": [1]}
  }]
}
```

示例里的 `joints` / `scores` 为结构示意，实际必须恰好 21×3 与 21，全部有限，按腕根、拇指四点、食指四点、中指四点、无名指四点、小指四点排序。时刻必须精确对应来源；不同坐标系必须先通过明确标定转换，不会猜测 HOT3D/UmeTrack21 索引映射。

## 可编辑 Blender 与 GLB 预览

```bash
python3 scripts/capsule.py wholebody-build \
  --run /path/new-run --output /path/new-build \
  --blender /path/to/blender

# 集成到场景的新副本，保留输入文件
python3 scripts/capsule.py wholebody-build \
  --run /path/new-run --output /path/new-integrated-build \
  --scene /path/preserved_scene.blend \
  --world-to-blender-json /path/verified_world_to_blender.json \
  --preview-glb --preview-frame 10 --blender /path/to/blender
```

场景集成必须显式提供相机世界到 Blender 世界的刚体 4×4 转换。没有这项标定就只交付独立资产，不猜场景对齐。输出 `wholebody_human.blend` 及有输入场景时的 `scene_with_wholebody.blend`；具名身体/手指 capsule 均可编辑，动画只用位置/四元数及未初始化时的可见性，网格和尺度保持固定。骨段挂在父段固定终点形成 FK，身体可靠后手根通过同侧腕约束连接；在来源帧之间插值也不会让指段或腕根脱开。工具实际保存并重开两个文件，核对原场景对象、输入文件哈希、不可变网格、每个来源时刻的端点与动画，以及中间时刻的 FK/腕连接。

`--preview-glb` 导出指定接受帧的静态人体预览；默认末帧。它不是完整动画交付。GLB 标准轴转换与显式输入转换组成的 `world_to_glb` 写入 `build_report.json`；放回原工作台前检查这个矩阵，不把未标定的独立资产当成已对齐结果。构建不会覆盖场景或自动调用 `workspace_publish_scene`。
