**语言 / Languages:** [简体中文](README.md) · [English](README.en.md) · [日本語](README.ja.md) · **한국어** · [Русский](README.ru.md)

# Codex 시각 재구성 작업대

기본 모드에서는 참고 이미지와 현재 3D 장면에 표시를 그리고 한 문장을 쓴 뒤 「보내기」를 누르세요. 작업대는 글, 원본 이미지, 주석 이미지, 장면 스냅샷을 **작업대가 관리하는 같은 Codex 대화의 사용자 메시지**로 보냅니다. Codex가 프로젝트를 수정하고 GLB를 게시하면 결과가 이 페이지에 나타납니다. 터미널에서 별도의 읽기 작업을 다시 시작할 필요가 없습니다. 이미 진행 중인 Codex 데스크톱 작업에서도 아래의 외부 MCP 모드로 같은 검토 화면을 사용할 수 있습니다.

![참고 이미지와 장면을 나란히 놓고 표시한 화면](preview.png)

## 작동 방식

```text
브라우저: 참고 이미지 + 3D 장면 + 표시 + 글
                 │ 사용자가 「보내기」 클릭
                 ▼
          로컬 Workspace Gateway
                 │ 실제 이미지 입력과 실행 이벤트
                 ▼
       Codex App Server: 같은 프로젝트, 같은 대화
                 │ 소스 파일 수정, GLB 내보내기, MCP 도구 호출
                 ▼
              작업대의 장면 새로고침
```

기본 모드에서 Gateway는 이 프로젝트의 Codex 대화를 지속적으로 보관합니다. stdio로 `codex app-server`를 실행하고 이미지를 `localImage` 입력 항목으로 보냅니다. 이 모드는 이미 열려 있는 다른 Codex 데스크톱 또는 터미널 대화에 메시지를 주입하지 않습니다. MCP는 문맥 읽기, 사용자 검토 요청, 장면 게시에 사용합니다. **웹페이지의 보내기 버튼이 사용자 턴을 직접 시작합니다.**

이 화면은 사람이 문제를 가리킬 수 있도록 돕습니다. 재구성 알고리즘을 규정하거나 사용자에게 좌표나 기하학적 제약을 입력하라고 요구하지 않습니다. Codex는 프로젝트에 이미 있는 모델링, 재구성, 편집 도구를 사용할 수 있습니다.

## 페이지에서 할 수 있는 일

- 왼쪽에서 참고 이미지를 전환하고 확대하거나 이동합니다. 오른쪽에서 GLB 장면을 회전하고 확대하며 노드를 선택합니다.
- 양쪽에 점, 사각형, 선, 화살표, 자유로운 펜 선과 글자를 그립니다. 서로 대응하는 표시에는 번호를 붙일 수 있습니다. 빠진 물체는 참고 이미지에만 표시해도 됩니다.
- 「현재 시점에 주석 달기」를 누르면 장면 표시가 **그때의 스크린샷, 카메라, 선택한 객체, 장면 버전**에 묶입니다. 실시간 3D 뷰를 돌려도 기존 표시가 다른 물체로 옮겨가지 않습니다. 새 장면이 게시되어도 표시 중인 스냅샷을 덮어쓰지 않습니다.
- 전송할 때 사용자의 원문, 참고 이미지 원본과 주석 이미지, 표시 없는 장면 스크린샷과 표시된 스크린샷, 선택한 노드, 카메라와 장면 버전을 변경 불가능한 피드백 묶음으로 저장합니다. 표시는 사람의 힌트이며, 원본 이미지는 별도로 보관됩니다.
- 실행 중에 보낸 새 피드백은 다음 턴의 대기열에 들어갑니다. 대기하는 동안 장면이 바뀌었다면, 페이지가 먼저 이전 버전을 바탕으로 한 피드백인지 확인을 요청합니다. 승인 요청과 중지 동작도 페이지에서 처리합니다. 새로고침 후에도 프로젝트, 대화, 초안이 유지됩니다.

현재 장면 입력은 자체적으로 필요한 데이터를 모두 담은 `.glb` 파일입니다. 게시할 때 기하 형상과 텍스처 리소스가 GLB의 BIN 블록에 내장되어 있는지 검증합니다. 외부 URI와 data URI는 모두 거부합니다. data URI도 자체 완결형일 수 있지만 이 버전은 BIN 블록만 허용합니다. 참고 이미지로만 제출할 수 있으므로 초기 장면이 없어도 시작할 수 있습니다.

## 참고 카메라 시점에 정렬

카메라 정보가 있는 참고 이미지를 선택하면 3D 뷰가 해당 촬영 시점으로 자동 이동합니다. 수동으로 회전한 뒤에는 `对齐参考视角`(「참고 시점에 정렬」)을 눌러 다시 맞추고, 오버레이로 윤곽을 비교할 수 있습니다. 왜곡 보정 이미지가 제공되면 핀홀 카메라 투영과 맞는 오버레이에 사용합니다. 원본 참고 이미지는 보기와 피드백을 위해 별도로 보관됩니다. 카메라 정보가 없는 이미지는 계속 수동으로 비교할 수 있습니다. 내부 파라미터가 근삿값이면 이미지 가장자리에 오차가 남을 수 있습니다.

각 이미지의 카메라 정보에는 GLB 월드 좌표계의 행 우선 4×4 `camera_to_world` 행렬과 이미지 픽셀 단위의 `intrinsics`(`width`, `height`, `fx`, `fy`, `cx`, `cy`)가 들어갑니다. 기존 이미지는 보호된 `POST /api/workspace/reference-cameras` API에 `reference_id`, `camera`, 선택 사항인 왜곡 보정 이미지 `alignment_image_data_url`을 보냅니다. 비공개 작업대 데이터 디렉터리의 `reference_cameras.json`은 이후 가져오는 이미지의 파일 이름 접두사로 카메라를 찾습니다([예시](examples/reference_cameras.example.json)). 기존 이미지에 적용하려면 같은 API에 `{"apply_manifest":true}`를 보냅니다. 카메라와 게시한 GLB는 같은 월드 좌표계를 사용해야 합니다.

## 빠른 체험: 방과 캐비닛

현재 브라우저 UI의 버튼 표시는 중국어입니다. `标注当前视角`는 「현재 시점에 주석 달기」, `发送到 Codex`는 「Codex에 보내기」에 해당합니다.

Python 3.11 이상, WebGL을 지원하는 브라우저, 로그인된 **`codex-cli 0.156.1`**이 필요합니다. App Server 요청과 응답 형식은 이 버전에서 생성한 JSON Schema와 대조해 확인했습니다. 다른 버전에서는 호환되지 않는 필드를 조용히 사용하지 않고 명시적인 오류를 표시합니다.

클론한 저장소의 루트에서 실행하세요.

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r backend/requirements.txt
codex --version
.venv/bin/python examples/room_demo/seed_demo.py
.venv/bin/python backend/server.py --project-dir "$PWD" --data-dir "$PWD/examples/room_demo/output/data"
```

Gateway가 프로젝트의 Codex 대화에 이 다섯 가지 `scene_feedback` MCP 도구를 자동으로 구성하므로 `codex mcp add`를 수동으로 실행할 필요가 없습니다. Gateway는 포트, 데이터 디렉터리, 프로젝트 디렉터리를 해당 대화에 전달하여 다른 프로젝트나 전역 MCP 설정이 잘못된 작업대를 가리키지 않도록 합니다.

<http://127.0.0.1:18765/>을 여세요. 왼쪽에는 목표를 보여 주는 이미지가, 오른쪽에는 초기 방 GLB가 있습니다. 캐비닛을 선택하고 이미지에서 원하는 위치를 가리킨 뒤 「캐비닛을 왼쪽 벽에 더 가깝게 옮기고 왼쪽 이미지를 기준으로 맞춰 주세요」라고 입력하고 「보내기」를 누르세요. 데모의 원본 매개변수는 [`examples/room_demo/scene.json`](examples/room_demo/scene.json)에 있으며, [`build_scene.py`](examples/room_demo/build_scene.py)는 매개변수로부터 GLB를 생성합니다. Codex가 `workspace_publish_scene`을 호출하면 결과가 페이지에 나타납니다. `seed_demo.py`는 별도의 `examples/room_demo/output/data`를 사용하므로 일반 작업 공간의 데이터를 덮어쓰지 않습니다.

자신의 프로젝트에서는 다음처럼 실행할 수 있습니다.

```bash
.venv/bin/python backend/server.py --project-dir "/absolute/path/to/your/project" --data-dir "/absolute/path/to/private/workspace-data"
```

브라우저에서 참고 이미지를 추가하세요. Codex가 프로젝트 소스 파일에서 자체 완결형 GLB를 빌드하거나 내보낸 다음 MCP를 통해 게시하도록 하면 됩니다. 프로젝트 디렉터리와 thread ID는 데이터 디렉터리에 저장되므로 재시작해도 같은 대화가 복원됩니다. 데이터 디렉터리 하나는 프로젝트 하나에만 연결되며 다른 프로젝트로 몰래 바뀌지 않습니다.

## 기존 Codex 데스크톱 작업에서 검토하기 (외부 MCP 모드)

이미 Codex 데스크톱에서 프로젝트를 작업 중이라면 `--external-review`로 작업대를 그 작업에서 호출하는 MCP 도구로 실행할 수 있습니다. 이 모드는 **다른 Codex 대화를 만들거나 인계받지 않습니다**. Codex가 `request_visual_feedback`으로 검토를 시작합니다. 브라우저가 정상적으로 열리면 이 호출이 기본적으로 입력을 기다렸다가 피드백을 반환합니다. URL과 세션 정보만 반환되면 `wait_visual_feedback`을 호출해 기다립니다. 브라우저에서 「보내기」를 누르면 글과 실제 이미지 데이터가 도구 결과로 **원래 작업**에 돌아가며, 그 작업에서 장면 수정을 이어 갑니다. `get_visual_feedback`으로 제출된 피드백을 다시 읽을 수 있습니다.

위의 의존성을 먼저 설치하세요. 터미널에서 절대 경로를 지정해 전역 MCP 서버를 등록하고, 별도의 검토 서비스를 실행합니다.

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

`codex mcp add`가 만든 `~/.codex/config.toml`의 `[mcp_servers.scene_feedback_external]` 항목 아래에 `tool_timeout_sec = 900`을 설정하세요. 이미지 전송 시간을 남기도록 검토 도구의 `timeout_sec`는 기본값인 600 이하로 지정하세요. 기존 작업의 MCP 도구 목록을 새로 읽도록 **Codex 데스크톱 앱을 재시작**하세요. 그 작업에서 “进入人工调试模式”(사람이 직접 검토하는 모드로 들어가기)라고 말하거나 프로젝트 안의 참고 이미지 경로와 현재 GLB 경로를 전달해 `request_visual_feedback`을 호출하라고 명시하세요(초기 장면이 없으면 GLB는 생략 가능). 도구가 `session_id`, `next_cursor`, URL만 반환하면 <http://127.0.0.1:18768/>을 열고 그 `session_id`와 `cursor=next_cursor`로 `wait_visual_feedback`을 호출하세요. 참고 이미지를 추가해 표시하면 됩니다. 이 모드에서 브라우저의 보내기 버튼은 대기 중인 MCP 검토만 완료하며 **새 사용자 턴을 시작하지 않습니다**.

`PROJECT`는 검토할 재구성 프로젝트의 루트이며 참고 이미지와 GLB가 그 안에 있어야 합니다. Codex 작업 디렉터리의 하위 디렉터리여도 됩니다. `DATA`는 그 프로젝트만 사용하는 비공개 디렉터리입니다. 예제에서는 기본 모드와 섞이지 않도록 포트, 데이터 디렉터리, MCP 이름을 분리했습니다. 게시된 장면은 여전히 `workspace_publish_scene`으로 페이지에 반영할 수 있습니다.

## LAN의 다른 기기에서 페이지 열기

서비스는 프로젝트를 보관하고 Codex와 MCP를 실행하는 호스트에서 계속 실행합니다. 다른 기기에는 브라우저만 있으면 됩니다. 해당 포트를 사용 중인 기존 서비스를 중지한 뒤 위에서 사용한 `REPO`, `PROJECT`, `DATA` 값을 그대로 사용하세요. 예시 IP는 **서비스 호스트**의 접속 가능한 LAN IPv4 주소로 바꾸세요.

```bash
LAN_IP=192.168.1.10
"$REPO/.venv/bin/python" "$REPO/backend/server.py" \
  --project-dir "$PROJECT" --data-dir "$DATA" --port 18768 --external-review \
  --listen-host 0.0.0.0 --public-base-url "http://$LAN_IP:18768"
```

시작 메시지 또는 MCP의 `request_visual_feedback` / `workspace_open` 결과에 `access_token`이 포함된 전체 링크가 나옵니다. 다른 기기의 브라우저에서 **전체 링크**를 여세요. 확인이 끝나면 주소창에서 토큰을 지우고 브라우저 쿠키로 접근을 유지합니다. `http://$LAN_IP:18768/`만 입력해서는 접근할 수 없습니다. 접속 링크를 공개하지 마세요. 기본 작업대 모드에서도 기존 포트와 데이터 디렉터리를 사용하면서 이 두 옵션을 추가할 수 있으며, 이때 `--external-review`는 생략합니다. 계속 실행하려면 같은 시작 명령을 systemd 사용자 서비스로 실행할 수 있습니다. LAN에서는 작업대 웹페이지와 인증된 API를 사용할 수 있고, MCP는 프로젝트 호스트의 Codex가 계속 `127.0.0.1`을 통해 호출합니다. 연결되지 않으면 호스트 방화벽에서 선택한 TCP 포트를 허용하세요. 일반 HTTP LAN 모드는 신뢰할 수 있는 네트워크용입니다.

## MCP 도구

| 도구 | 용도 |
| --- | --- |
| `workspace_open` | 이 프로젝트의 작업대 주소를 반환하거나 엽니다 |
| `workspace_get_context` | 현재 참고 이미지, 장면 버전 및 프로젝트 문맥을 읽습니다 |
| `workspace_get_feedback` | 제출된 피드백과 실제 이미지를 읽습니다 |
| `workspace_publish_scene` | 새 GLB를 검증하고 게시하며, 예상 버전을 확인하고 페이지에 새로고침을 알립니다 |
| `workspace_request_feedback` | 기본 모드: 페이지에서 검토를 요청하고 즉시 반환합니다. 답변은 다음 사용자 메시지가 됩니다 |
| `request_visual_feedback` / `wait_visual_feedback` | 외부 MCP 모드: 검토를 시작하고 브라우저 제출을 기다려 글과 이미지를 원래 작업에 반환합니다 |
| `get_visual_feedback` | 외부 MCP 모드: 제출된 시각 피드백을 다시 읽습니다 |

저장소의 [시각 재구성 Skill](.agents/skills/visual-reconstruction-feedback/SKILL.md)은 Codex에게 원본 이미지와 표시를 구별하고, 프로젝트 소스 파일을 편집하며, 결과가 준비되면 GLB를 게시하도록 안내합니다. 기본 모드에서는 다음 제출을 위해 MCP 호출이 대기할 필요가 없습니다. 외부 MCP 모드에서는 `wait_visual_feedback`이 피드백을 원래 작업으로 반환합니다.

## 검증과 사용 범위

```bash
.venv/bin/python -m unittest discover -s backend/tests -v
```

두 개의 서로 다른 로컬 이미지로 실제 App Server 통합을 검증했습니다. Codex는 첫 턴에 빨간 이미지를 알아보았고, stdio를 닫고 다시 시작한 뒤 **같은 대화**의 두 번째 턴에서 파란 이미지를 알아보았습니다. `thread/read` 기록에는 두 턴의 `localImage` 항목이 모두 들어 있습니다. 따라서 이미지 경로만 전달된 것이 아니라 이미지가 실제로 모델에 입력되었음을 확인했습니다.

실제 브라우저→Codex→MCP 흐름도 Selenium으로 두 차례 검증했습니다. 첫 번째 라운드에서 사용자가 참고 이미지와 장면 이미지에 각각 번호가 있는 사각형을 그리고 보냈고, App Server 턴에는 `localImage` 5개가 포함되었습니다. Codex는 데모의 `scene.json`을 수정해 GLB를 생성하고 프로젝트별 MCP의 `workspace_publish_scene`을 두 차례 성공적으로 호출했습니다. 페이지에서 사용자가 승인한 뒤 브라우저의 장면 버전은 2→3→4로 자동 갱신되었고, 캐비닛 중심의 `x`는 `-0.8`이 되었습니다. 두 번째 라운드에서는 사용자가 웹페이지의 참고 이미지에 선을 더 그려 보냈습니다. 같은 대화에서 Codex는 추가 `input_image` 5개를 받고 확인 답변을 했습니다. 파일을 수정하거나 다시 게시하지 않았으며 최종 장면 버전은 4입니다.

기본적으로 서비스는 `127.0.0.1`에서만 수신합니다. 위의 LAN 옵션을 지정하면 페이지에 접속 링크와 쿠키가 필요하고, 제출 작업에는 브라우저 capability도 계속 필요합니다. 로컬 MCP Gateway와 통신할 수 있도록 Codex의 `workspaceWrite` 턴에 `networkAccess: true`가 설정됩니다. 실행 승인은 사용자가 페이지에서 결정하며 작업대는 자동으로 승인하지 않습니다. 실행 데이터와 데모 출력은 Git에 포함되지 않습니다.

프로젝트 코드는 [MIT 라이선스](LICENSE)를 따릅니다. 저장소의 Three.js 파일에는 [원래의 MIT 라이선스](web/vendor/three/LICENSE)가 유지됩니다.
