**语言 / Languages:** [简体中文](README.md) · [English](README.en.md) · [日本語](README.ja.md) · **한국어** · [Русский](README.ru.md)

# Astra 시각 피드백 작업대

사람과 Astra가 같은 참고 사진과 현재 장면을 보며 소통할 수 있는 작업대입니다. 사진이나 3D 뷰에 점을 찍거나, 선과 상자를 그리고, 짧은 설명을 덧붙이면 MCP가 원본 이미지, 주석이 표시된 이미지, 장면 스크린샷과 필요한 문맥을 모델에 전달합니다. **객체, 카메라, 재질 또는 재구성 방식을 어떻게 수정할지는 Astra가 판단합니다.** 사용자가 좌표나 기하학적 제약을 입력할 필요는 없습니다.

![참고 사진과 장면을 나란히 표시하고 주석을 추가한 화면](preview.png)

## 주요 기능

- 왼쪽에서 참고 사진을 보고, 오른쪽에서 현재 3D 장면을 회전하거나 확대하고 객체를 선택할 수 있습니다. 여러 참고 사진 사이를 전환할 수도 있습니다.
- 어느 쪽이든 점, 사각형, 선, 화살표, 텍스트를 그릴 수 있습니다. 양쪽의 표시를 연결하고 싶다면 같은 번호를 붙이세요. 장면에 아직 없는 대상은 참고 사진에만 표시해도 됩니다.
- 현재 참고 사진을 반투명하게 3D 뷰 위에 겹쳐 육안으로 비교할 수 있습니다. 이 오버레이에는 **자동 카메라 정합 기능이 없습니다**.
- 「Astra에 보내기」를 누른 뒤에도 같은 세션이 유지됩니다. Astra가 장면을 업데이트하면 페이지가 자동으로 새로고침됩니다. 주석 초안과 뷰 각도는 다음 라운드를 위해 브라우저에 남습니다.

제출되는 시각 피드백에는 원본 참고 사진, 주석이 표시된 참고 사진, 현재 장면의 원본 및 주석 스크린샷, 필요한 부분 확대 이미지, 메모, 선택한 객체 ID, 장면 버전, 뷰 카메라가 포함됩니다. GLB 내부의 개별 부품을 클릭하면 선택한 노드의 이름과 경로도 포함됩니다. 이미지는 MCP image content로 반환되며, 메타데이터는 structured content와 텍스트에도 담깁니다. 호스트가 MCP 이미지 블록을 전달하지 않을 때 읽을 수 있도록 로컬 절대 경로도 제공됩니다.

## 로컬에서 사용해 보기

Python 3.11 이상과 WebGL을 지원하는 브라우저가 필요합니다. 저장소를 복제한 뒤 루트 디렉터리에서 실행하세요.

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r backend/requirements.txt
.venv/bin/python backend/server.py
```

<http://127.0.0.1:18765/>을 열고 참고 사진을 업로드하면 됩니다. 오른쪽에는 기본 예시 의자가 표시되며, 자신의 장면을 연결하면 해당 결과가 표시됩니다. 이 작업대는 독립된 브라우저 화면이므로 클라이언트가 MCP Apps 내장 표시를 지원하지 않아도 됩니다. 서버는 `127.0.0.1`에서만 수신합니다. 가져온 이미지, 장면, 피드백은 `backend/data/`에 저장되며 이 디렉터리는 Git에서 제외됩니다.

## Codex / Astra에 연결하기

저장소 루트에서 MCP 서버를 등록하세요. `$PWD`는 저장소의 절대 경로로 확장됩니다.

```bash
codex mcp add scene_feedback -- "$PWD/.venv/bin/python" "$PWD/backend/mcp_server.py"
```

`~/.codex/config.toml`의 `[mcp_servers.scene_feedback]`에 충분히 긴 상호작용 대기 시간을 설정하세요. `codex mcp add`가 만든 기존 섹션을 수정하고, 같은 이름의 섹션을 중복해서 추가하지 마세요. 승인을 묻지 않는 Codex 모드를 사용한다면 작업대를 만드는 도구에만 자동 승인을 설정할 수 있습니다. 피드백을 읽는 도구는 읽기 전용으로 선언되어 있습니다. 그다음 MCP 설정을 다시 불러오세요: [Codex MCP 설정 안내](https://learn.chatgpt.com/docs/extend/mcp).

```toml
[mcp_servers.scene_feedback]
tool_timeout_sec = 900

[mcp_servers.scene_feedback.tools.request_visual_feedback]
approval_mode = "approve"
```

권장하는 두 단계 호출 방식:

```text
request_visual_feedback(
  reference_images=["/absolute/path/reference.jpg"],
  scene_glb_path="/absolute/path/current.glb",
  wait_for_submit=false
)
→ session_id, url, next_cursor

사용자가 url에서 표시를 그리고 「Astra에 보내기」를 클릭

wait_visual_feedback(session_id, cursor=next_cursor)
→ 이미지 블록 + 주석/텍스트/객체/카메라/버전 정보 + 새로운 next_cursor
```

`current_scene={"objects": [...]}`로 기존 장면의 객체 목록을 전달하거나, 장면을 전달하지 않고 작업대의 현재 장면을 사용할 수도 있습니다. `scene_glb_path`는 미리 볼 수 있는 GLB 파일을 가져오는 데 사용하며 Astra의 모델링 방식을 지정하지 않습니다. GLB 파일과 참고 사진 경로는 MCP 서버가 실행되는 컴퓨터의 로컬 경로여야 합니다. 브라우저에서도 참고 사진을 직접 추가할 수 있습니다.

피드백을 받은 Astra는 자체 모델링 도구로 장면을 수정합니다. 다음 라운드에는 `request_visual_feedback(session_id=..., scene_glb_path="/absolute/path/updated.glb", wait_for_submit=false)`를 호출하고, 반환된 `next_cursor`로 `wait_visual_feedback`을 호출하세요. 같은 `session_id`를 재사용하면 페이지와 참고 사진을 다시 열 필요가 없습니다. 웹페이지에서 피드백을 제출하는 것만으로 이미 끝난 Codex 턴이 새로 시작되지는 않습니다. Astra가 이어서 작업하려면 대기 중인 MCP 호출이 있거나 사용자 정의 호스트가 다음 턴을 시작해야 합니다.

호스트가 로컬 브라우저를 열 수 있다면 `request_visual_feedback()`은 기본적으로 페이지를 열고 제출을 기다립니다. 페이지를 열지 못하면 즉시 URL을 반환하며, 이후 `wait_visual_feedback`으로 기다릴 수 있습니다. 대기 시간이 초과되어도 세션은 열린 상태로 유지됩니다. `get_visual_feedback(session_id, cursor)`로 조회하거나 다시 기다리면 됩니다.

## 피드백 형식

주석 좌표는 해당 사진이나 뷰 안에서 정규화된 화면 좌표(`0–1`)입니다. 사용자가 가리킨 화면 위치를 표현하며, 월드 좌표나 실행할 모델링 명령이 아닙니다. 예:

```json
{
  "scene_revision": 12,
  "note": "캐비닛 윗면이 왼쪽 사진 ①의 선과 비슷한 높이에 있어야 해요. 오른쪽에는 조명도 하나 빠졌어요.",
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

`group_id`와 `object_id`는 모두 선택 사항입니다. GLB 내부의 노드를 선택하면 피드백에 `selected_scene_nodes`도 포함됩니다. 각 노드에는 소속 모델 객체 ID, GLB 내부 하위 노드의 인덱스 경로, 사용할 수 있는 노드 이름이 들어갑니다. 이는 화면 위치를 이해하기 위한 참고 정보이지 모델이 실행할 작업이 아닙니다. 원본 사진과 주석이 표시된 사진은 별도로 보관되어 Astra가 사진의 원래 내용과 사람의 지시를 구분할 수 있습니다. 장면 버전이 바뀌면 UI에 이전 장면 주석의 원본 버전이 표시되며, 제출할 때 현재 버전을 확인하여 오래된 화면을 새 화면으로 오인하지 않게 합니다. 유지된 주석은 다음 라운드에도 전송되므로 개별적으로 삭제하거나 모두 지울 수 있습니다.

기존 `get_scene`, `update_scene`, `replace_scene`, `import_scene_model` 도구는 장면 표시와 기존 호출자와의 호환을 위해 계속 사용할 수 있습니다. 작업대 자체는 GLB 메시를 수정하지 않습니다.

## 검증 및 사용 범위

```bash
.venv/bin/python -m unittest discover -s backend/tests -v
```

이 프로젝트는 신뢰할 수 있는 로컬 환경을 위한 프로토타입입니다. 같은 컴퓨터의 다른 프로세스가 페이지와 세션 API에 접근할 수 있으므로, 로컬 포트를 신뢰할 수 없는 사용자에게 개방하지 마세요. 자동 승인 설정은 신뢰하는 로컬 서버에만 적용하세요. Codex CLI가 이 도구의 MCP 이미지 블록에서 참고 사진 내용을 읽는 것은 실제로 확인했습니다. 다른 호스트가 이미지를 전달하는지는 구현에 따라 다릅니다. 호스트가 텍스트만 표시한다면 Astra가 피드백에 포함된 로컬 이미지 경로를 읽도록 할 수 있습니다.

프로젝트의 독창적인 코드는 [MIT 라이선스](LICENSE)를 사용합니다. 저장소의 Three.js 파일에는 [원래의 MIT 라이선스](web/vendor/three/LICENSE)가 유지됩니다.
