**语言 / Languages:** [简体中文](README.md) · [English](README.en.md) · **日本語** · [한국어](README.ko.md) · [Русский](README.ru.md)

# Astra ビジュアルフィードバック・ワークベンチ

人と Astra が、同じ参照写真と現在のシーンを見ながらやり取りするためのツールです。画像や 3D ビューで囲む・線を引く・指し示すといった操作をして、短いメモを添えます。MCP ツールは元画像、注釈付き画像、シーンのスクリーンショット、必要な情報をモデルへ返します。**オブジェクト、カメラ、マテリアル、再構築手法のどれを変更するかは Astra が判断します。** 人が座標や幾何拘束を入力する必要はありません。

![参照画像とシーンを並べて注釈した画面](preview.png)

## できること

- 左側で参照画像を見ながら、右側で現在の 3D シーンの回転・拡大縮小・オブジェクト選択ができます。複数の参照画像も切り替えられます。
- どちらの側にも点、矩形、線、矢印、文字を追加できます。対応を示したい場合は両側の注釈に同じ番号を付けます。シーンにまだ存在しないものは、参照画像だけに印を付けても構いません。
- 現在の参照画像を半透明にして 3D ビューへ重ね、目視で比較できます。この重ね合わせでは**カメラの自動位置合わせは行いません**。
- 「Astra に送信」をクリックした後も、同じセッションを使い続けられます。Astra がシーンを更新するとページは自動更新されます。注釈の下書きと視点はブラウザーに残り、次のラウンドでも使えます。

送信される視覚フィードバックには、元の参照画像、注釈付き参照画像、現在のシーンの元のスクリーンショットと注釈付きスクリーンショット、必要な部分画像、メモ、選択したオブジェクトの ID、シーンのリビジョン、表示カメラが含まれます。GLB 内のパーツをクリックした場合は、そのノード名とパスも含まれます。画像は MCP の image content として返され、メタデータは structured content とテキストの両方で提供されます。MCP の画像ブロックを転送しないホストが画像を読み込めるよう、ローカルの絶対パスも記載されます。

## ローカルで試す

Python 3.11 以降と WebGL 対応ブラウザーが必要です。クローンしたリポジトリのルートで以下を実行してください。

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r backend/requirements.txt
.venv/bin/python backend/server.py
```

<http://127.0.0.1:18765/> を開き、参照画像をアップロードしてください。初期状態では右側にサンプルの椅子が表示されます。自分のシーンを接続すると、その結果が表示されます。これは独立したブラウザー版ワークベンチであり、クライアントによる MCP Apps の埋め込み対応は不要です。サービスは `127.0.0.1` でのみ待ち受けます。取り込んだ画像、シーン、フィードバックは `backend/data/` に保存され、このディレクトリは Git の管理対象から除外されています。

## Codex / Astra に接続する

リポジトリのルートで MCP サーバーを登録します。`$PWD` はリポジトリの絶対パスに展開されます。

```bash
codex mcp add scene_feedback -- "$PWD/.venv/bin/python" "$PWD/backend/mcp_server.py"
```

`~/.codex/config.toml` の `[mcp_servers.scene_feedback]` に、対話を待つための長めのタイムアウトを設定します。`codex mcp add` で作成された既存の節を編集し、同名の節を重複して追加しないでください。承認ダイアログを表示しない Codex モードを使う場合は、ワークベンチを作成するツールだけを自動承認できます。フィードバックを読むツールは読み取り専用として宣言されています。その後、MCP 設定を再読み込みしてください：[Codex MCP 設定ガイド](https://learn.chatgpt.com/docs/extend/mcp)。

```toml
[mcp_servers.scene_feedback]
tool_timeout_sec = 900

[mcp_servers.scene_feedback.tools.request_visual_feedback]
approval_mode = "approve"
```

推奨する 2 段階の呼び出し方法：

```text
request_visual_feedback(
  reference_images=["/absolute/path/reference.jpg"],
  scene_glb_path="/absolute/path/current.glb",
  wait_for_submit=false
)
→ session_id、url、next_cursor

ユーザーが url 上で注釈を付け、「Astra に送信」をクリック

wait_visual_feedback(session_id, cursor=next_cursor)
→ 画像ブロック + 注釈・メモ・オブジェクト・カメラ・リビジョンなど + 新しい next_cursor
```

`current_scene={"objects": [...]}` で既存のシーンオブジェクト一覧を渡すことも、シーンを省略してワークベンチの現在のシーンを使うこともできます。`scene_glb_path` はプレビュー用に GLB を読み込むためのもので、Astra のモデリング方法を指定するものではありません。GLB と参照画像のパスは、MCP サーバーを実行するマシン上のローカルパスである必要があります。ブラウザーから参照画像を直接追加することもできます。

フィードバックを受け取った後、Astra は自身のモデリングツールでシーンを更新します。次のラウンドでは `request_visual_feedback(session_id=..., scene_glb_path="/absolute/path/updated.glb", wait_for_submit=false)` を呼び出し、返された `next_cursor` を指定して `wait_visual_feedback` を呼び出します。同じ `session_id` を再利用すれば、ページや参照画像を開き直さずに済みます。ブラウザーから送信しても、すでに終了した Codex のターンは自動では再開しません。Astra を続行させるには、フィードバックを待機している MCP 呼び出しか、次のターンを開始するカスタムホストが必要です。

ホストがローカルブラウザーを開ける場合、`request_visual_feedback()` は既定でページを開き、送信を待とうとします。ブラウザーを開けない場合は URL をすぐに返すため、その後 `wait_visual_feedback` で待機できます。待機がタイムアウトしてもセッションは開いたままです。`get_visual_feedback(session_id, cursor)` で確認するか、再び待機してください。

## フィードバック形式

注釈の座標は、各画像やビュー内で正規化されたスクリーン座標（`0–1`）です。ユーザーが画面上で指した位置を表すもので、ワールド座標でも、実行すべきモデリング命令でもありません。例：

```json
{
  "scene_revision": 12,
  "note": "キャビネットの上端は左の画像の①の線とほぼ同じ高さ。右側にはランプも足りません。",
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

`group_id` と `object_id` はどちらも任意です。GLB 内のノードを選択すると、フィードバックには `selected_scene_nodes` も含まれます。各ノードについて、所属するモデルオブジェクトの ID、GLB 内の子ノードのインデックスパス、取得できる場合はノード名が示されます。これらは画面上の位置を補足する参照情報であり、モデルに実行させる操作ではありません。元画像と注釈付き画像は別々に保持されるため、Astra は写真にもともと写っているものと人が付けた印を区別できます。シーンのリビジョンが変わると、古いシーン注釈には元のリビジョンが画面に表示されます。送信時には現在のリビジョンが確認され、古い画面を新しい画面と取り違えることを防ぎます。保持された注釈は次のラウンドでも送信されます。個別に削除するか、まとめて消去できます。

既存の `get_scene`、`update_scene`、`replace_scene`、`import_scene_model` ツールは、シーンの表示や既存の呼び出し元との互換性のために引き続き利用できます。ワークベンチ自体は GLB メッシュを変更しません。

## 検証と利用範囲

```bash
.venv/bin/python -m unittest discover -s backend/tests -v
```

これは信頼できるローカル環境向けのプロトタイプです。同じマシンの他のプロセスもページとセッション API にアクセスできるため、ローカルポートを信頼できない相手に公開しないでください。自動承認の設定は、信頼できるローカルサーバーにだけ適用してください。Codex CLI では、このツールの MCP 画像ブロックから参照画像の内容を読み取れることを確認しています。他のホストが画像をモデルへ渡すかどうかは、それぞれの実装によります。テキストしか表示しないホストでは、Astra がフィードバック内のローカル画像パスを読み取れます。

プロジェクト独自のコードは [MIT ライセンス](LICENSE)で公開しています。同梱の Three.js ファイルには[元の MIT ライセンス](web/vendor/three/LICENSE)が適用されます。
