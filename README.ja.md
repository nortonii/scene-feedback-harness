**语言 / Languages:** [简体中文](README.md) · [English](README.en.md) · **日本語** · [한국어](README.ko.md) · [Русский](README.ru.md)

# Codex ビジュアル再構築ワークベンチ

標準モードでは、参照画像と現在の 3D シーンに印を付け、一言を書いて「送信」をクリックします。ワークベンチは文章、元画像、注釈付き画像、シーンのスナップショットを、**ワークベンチが管理する同じ Codex スレッドのユーザーメッセージ**として送ります。Codex がプロジェクトを編集して GLB を公開すると、結果がこのページに戻ります。もう一度読み取らせるためにターミナルで操作する必要はありません。既存の Codex デスクトップタスクでも、後述する外部 MCP モードで同じ確認画面を使用できます。

![参照画像とシーンを並べて注釈した画面](preview.png)

## 動作の流れ

```text
ブラウザー：参照画像 + 3D シーン + 注釈 + 文章
                    │ ユーザーが「送信」をクリック
                    ▼
           ローカル Workspace Gateway
                    │ 実際の画像入力と実行イベント
                    ▼
     Codex App Server：同じプロジェクト、同じスレッド
                    │ ソース編集、GLB の書き出し、MCP ツールの呼び出し
                    ▼
             ワークベンチがシーンを更新
```

標準モードでは、Gateway がこのプロジェクトの Codex スレッドを一つ永続化します。`codex app-server` を stdio 経由で起動し、画像を `localImage` 入力項目として送ります。このモードは、すでに開いている別の Codex デスクトップやターミナルのセッションにメッセージを注入しません。MCP はコンテキストの取得、ユーザーへの確認依頼、シーンの公開に使用します。**Web ページの送信ボタンがユーザーターンを直接開始します。**

この画面は、人が問題を指し示すためのものです。再構築アルゴリズムを定めたり、座標や幾何拘束の入力を求めたりしません。Codex はプロジェクトで利用できる既存のモデリング、再構築、編集ツールを使えます。

## ページでできること

- 左側で参照画像を切り替え、拡大・縮小・移動できます。右側では GLB シーンを回転・拡大縮小し、ノードを選択できます。
- 両側に点、矩形、線、矢印、フリーハンドの線、文字を描けます。対応する印には同じ番号を付けられます。まだ存在しない物体は参照画像だけに印を付けられます。
- 「現在の視点に注釈」をクリックすると、シーン側の印が**その時点のスクリーンショット、カメラ、選択オブジェクト、シーンのリビジョン**に結び付けられます。ライブの 3D ビューを回転しても、古い印が別の物体へ移動することはありません。新しいシーンが公開されても、注釈作業中のスナップショットは上書きされません。
- 送信時に、変更されないフィードバック一式を保存します。ユーザーの原文、元の参照画像と注釈付き画像、注釈のないシーンと注釈付きシーンのスクリーンショット、選択ノード、カメラ、シーンのリビジョンが含まれます。印は人からのヒントであり、元画像は別に保持されます。
- Codex の実行中に送信した新しいフィードバックは、次のターンのキューに入ります。待機中にシーンが変わった場合、ページは古いリビジョンに対するフィードバックを確認するよう先に求めます。承認依頼と停止操作もページで扱います。ページを更新しても、プロジェクト、スレッド、下書きは残ります。

現在、シーン入力には自己完結型の `.glb` ファイルを使用します。公開時の検証では、ジオメトリとテクスチャのリソースが GLB の BIN チャンクに埋め込まれている必要があります。外部 URI と data URI はどちらも拒否されます。data URI でも自己完結にはできますが、このバージョンでは BIN への埋め込みだけを受け付けます。初期シーンがなくても、参照画像だけを送信できます。参照画像の重ね合わせは目視比較用であり、カメラの自動位置合わせは行いません。

## クイックスタート：部屋とキャビネット

現在のブラウザー画面のボタン表示は中国語です。`标注当前视角` が「現在の視点に注釈」、`发送到 Codex` が「Codex に送信」に当たります。

Python 3.11 以降、WebGL 対応ブラウザー、ログイン済みの **`codex-cli 0.156.1`** が必要です。App Server のリクエストとレスポンスの形式は、このバージョンから生成した JSON Schema と照合済みです。ほかのバージョンでは、互換性のないフィールドを黙って使わないよう、明示的なエラーを出します。

クローンしたリポジトリのルートで以下を実行します。

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r backend/requirements.txt
codex --version
.venv/bin/python examples/room_demo/seed_demo.py
.venv/bin/python backend/server.py --project-dir "$PWD" --data-dir "$PWD/examples/room_demo/output/data"
```

Gateway は、このプロジェクトの Codex スレッドに 5 つの `scene_feedback` MCP ツールを自動設定します。`codex mcp add` を手動で実行する必要はありません。Gateway はポート、データディレクトリ、プロジェクトディレクトリをそのスレッドに渡し、別のプロジェクトやグローバル MCP 設定が誤ったワークベンチを参照することを防ぎます。

<http://127.0.0.1:18765/> を開きます。左側に目標のイメージ図、右側に初期状態の部屋の GLB が表示されます。キャビネットを選び、画像上で目標位置を示し、「キャビネットを左の壁にもっと近づけ、左の画像に合わせてください」と入力して「送信」をクリックします。デモの元パラメーターは [`examples/room_demo/scene.json`](examples/room_demo/scene.json) にあります。[`build_scene.py`](examples/room_demo/build_scene.py) はそのパラメーターから GLB を生成します。Codex が `workspace_publish_scene` を呼び出すと、結果がページに表示されます。`seed_demo.py` は独立した `examples/room_demo/output/data` を使用するため、通常のワークスペースのデータを上書きしません。

自分のプロジェクトでは、次のコマンドを使用します。

```bash
.venv/bin/python backend/server.py --project-dir "/absolute/path/to/your/project" --data-dir "/absolute/path/to/private/workspace-data"
```

参照画像はブラウザーから追加します。Codex にプロジェクトのソースファイルから自己完結型の GLB を構築または書き出させ、MCP 経由で公開します。プロジェクトのディレクトリとスレッド ID はデータディレクトリに保存され、再起動後も同じスレッドを復元できます。一つのデータディレクトリは一つのプロジェクトに紐付き、黙って別のプロジェクトに切り替わることはありません。

## 既存の Codex デスクトップタスクから確認する（外部 MCP モード）

すでに Codex デスクトップでプロジェクトを扱っている場合は、`--external-review` でワークベンチをそのタスクから呼び出せる MCP ツールとして起動できます。このモードは**別の Codex スレッドを作成・引き継ぎません**。Codex が `request_visual_feedback` で確認を開始します。ブラウザーが正常に開けば、この呼び出しが標準で入力を待ってフィードバックを返します。URL とセッション情報だけが返った場合は、`wait_visual_feedback` を呼んで待機します。ブラウザーで「送信」をクリックすると、文章と実際の画像データがツールの結果として**元のタスク**に戻り、そのタスクがシーンの編集を続けます。`get_visual_feedback` で送信済みのフィードバックを再取得できます。

上記の依存関係を先にインストールしてください。ターミナルで絶対パスを指定してグローバル MCP サーバーを登録し、独立した確認用サービスを起動します。

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

`codex mcp add` が作成した `~/.codex/config.toml` の `[mcp_servers.scene_feedback_external]` に `tool_timeout_sec = 900` を設定します。画像転送の余裕を残すため、確認ツールの `timeout_sec` は既定値の 600 以下にしてください。既存タスクの MCP ツール一覧を更新するため、**Codex デスクトップアプリを再起動**します。そのタスクで「进入人工调试模式」（人による確認モードに入る）と伝えるか、プロジェクト内の参照画像のパスと現在の GLB のパスを渡して `request_visual_feedback` を呼ぶよう明示します（初期シーンがなければ GLB は省略できます）。ツールが `session_id`、`next_cursor`、URL だけを返した場合は <http://127.0.0.1:18768/> を開き、その `session_id` と `cursor=next_cursor` で `wait_visual_feedback` を呼びます。参照画像を追加して印を付けてください。このモードでブラウザーの送信ボタンが完了させるのは待機中の MCP 確認だけであり、**別のユーザーターンは開始しません**。

`PROJECT` は確認する再構成プロジェクトのルートです。参照画像と GLB はその中に置きます。Codex タスクの作業ディレクトリのサブディレクトリでも構いません。`DATA` にはそのプロジェクト専用の非公開ディレクトリを指定してください。例では標準モードと混ざらないよう、ポート、データディレクトリ、MCP 名を分けています。公開したシーンは引き続き `workspace_publish_scene` でページへ反映できます。

## LAN 上の別の端末からページを開く

サービスはプロジェクトを保存し Codex と MCP を実行するホストに置いたままにします。別の端末にはブラウザーだけあれば十分です。同じポートで動作中のサービスを停止し、上記と同じ `REPO`、`PROJECT`、`DATA` を使います。例の IP アドレスは**サービスホスト**の到達可能な LAN IPv4 アドレスに置き換えてください。

```bash
LAN_IP=192.168.1.10
"$REPO/.venv/bin/python" "$REPO/backend/server.py" \
  --project-dir "$PROJECT" --data-dir "$DATA" --port 18768 --external-review \
  --listen-host 0.0.0.0 --public-base-url "http://$LAN_IP:18768"
```

起動時の出力、または MCP の `request_visual_feedback` / `workspace_open` の結果に `access_token` を含む完全なリンクが表示されます。別の端末のブラウザーでその**リンク全体**を開いてください。検証後、トークンはアドレスバーから消え、ブラウザー Cookie でアクセスが維持されます。`http://$LAN_IP:18768/` だけを入力してもアクセスできません。リンクは公開しないでください。標準のワークベンチモードでも、従来のポートとデータディレクトリに同じ二つのオプションを追加できます。その場合は `--external-review` を省きます。常駐させる場合は同じ起動コマンドを systemd のユーザーサービスで実行できます。LAN ではワークベンチの Web ページと認証済み API が利用でき、MCP は引き続きプロジェクトホスト上の Codex が `127.0.0.1` 経由で呼び出します。接続できない場合はホストのファイアウォールで選択した TCP ポートを許可してください。平文 HTTP の LAN モードは信頼できるネットワーク向けです。

## MCP ツール

| ツール | 用途 |
| --- | --- |
| `workspace_open` | このプロジェクトのワークベンチ URL を返す、または開く |
| `workspace_get_context` | 現在の参照画像、シーンのリビジョン、プロジェクトのコンテキストを読み取る |
| `workspace_get_feedback` | 送信済みフィードバックと実際の画像を読み取る |
| `workspace_publish_scene` | 新しい GLB を検証して公開し、想定リビジョンを確認してページの更新を通知する |
| `workspace_request_feedback` | 標準モード：ページで確認を依頼してすぐに返る。返信は次のユーザーメッセージになる |
| `request_visual_feedback` / `wait_visual_feedback` | 外部 MCP モード：確認を始め、ブラウザーからの送信を待ち、文章と画像を元のタスクへ返す |
| `get_visual_feedback` | 外部 MCP モード：送信済みの視覚フィードバックを再取得する |

リポジトリ内の[ビジュアル再構築 Skill](.agents/skills/visual-reconstruction-feedback/SKILL.md)は、元画像と注釈の区別、プロジェクトのソースファイルの編集、結果が準備できたときの GLB 公開を Codex に促します。標準モードでは次の送信まで MCP 呼び出しを待機させる必要はありません。外部 MCP モードでは、`wait_visual_feedback` が元のタスクにフィードバックを返します。

## 検証と適用範囲

```bash
.venv/bin/python -m unittest discover -s backend/tests -v
```

実際の App Server 連携は、異なる 2 枚のローカル画像で検証しました。Codex は最初のターンで赤い画像を認識し、stdio を閉じて再起動した後、**同じスレッド**の 2 回目のターンで青い画像を認識しました。`thread/read` の履歴には、両ターンの `localImage` 項目が含まれています。これは画像のパスを渡しただけでなく、画像が実際にモデルへ届いたことを示します。

さらに Selenium で、同じスレッドにおける 2 回のブラウザー → Codex ターンを検証しました。最初のターンでは参照画像とシーン画像にそれぞれ番号付きの矩形を描き、ブラウザーから送信しました。Codex は実際の画像入力を 5 件受け取り、デモの `scene.json` を編集して GLB を生成し、プロジェクト単位の MCP ツール `workspace_publish_scene` を使って 2 回の公開に成功しました。承認はページ上で処理され、ワークベンチはシーンのリビジョンが 2 → 3 → 4 と進むたびに更新されました。キャビネットの最終的な中心位置は `x=-0.8` です。2 回目のターンでは参照画像に線を追加し、ページから送信しました。Codex は同じスレッドでさらに 5 件の `input_image` を受け取り、確認の返答をしました。このターンではファイルの編集もシーンの公開も行わなかったため、最終リビジョンは 4 のままでした。

標準ではサービスは `127.0.0.1` でのみ待ち受けます。上記の LAN オプションを指定すると、ページにはアクセスリンクと Cookie が必要になり、送信操作にはブラウザー capability も引き続き必要です。Codex の `workspace-write` サンドボックスで実行するターンには、ローカル MCP Gateway へ接続できるよう `networkAccess: true` を設定します。実行承認はユーザーがページ上で判断し、ワークベンチが自動承認することはありません。実行時データとデモ出力は Git の管理対象外です。

プロジェクトのコードには [MIT ライセンス](LICENSE)が適用されます。同梱の Three.js ファイルには[元の MIT ライセンス](web/vendor/three/LICENSE)が引き続き適用されます。
