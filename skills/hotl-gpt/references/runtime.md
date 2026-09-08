# 状態管理 CLI

```bash
python3 /absolute/skill/scripts/hotl.py --project /absolute/project status
python3 /absolute/skill/scripts/hotl.py --project /absolute/project --expect 3 receive --input /tmp/hotl-message.json
```

`--input` は JSON オブジェクトのファイル。`-` なら標準入力。
本文をシェル文字列へ埋め込まない。入力ファイルは安全な一時領域に編集ツールで作る。
`--expect` は任意の楽観ロック。直前に読んだ revision と違えば操作を拒否する。
複数コマンドへ同じ revision を使い続けず、各結果の新 revision を使う。
終了コードは成功 0、入力・状態・権限エラー 2。成功時の標準出力は JSON、エラーは標準エラーへの短い文。

| コマンド | JSON 入力 | 効果 |
|---|---|---|
| init | `{"request":"作りたいもの"}` | 新規状態のみ作成。既存の同名成果物を拒否 |
| trace | なし | 読み取り専用。要件→仕様→タスク、承認、進捗、照合・証拠の鮮度と構造上の不足を生成 |
| align | `{"actor":"実際の担当ID","detail":"意味と参照を照合した範囲・根拠"}` | 構造上の不足がない文書の版を記録。承認・実装・独立検証を付与しない |
| questions | なし | 読み取り専用。人間の確認待ち・回答済みと対象変更を再計算 |
| ask | 下記 | 判断が必要な項目をQ-nで登録。同じkeyの再送は同じ項目を返す |
| answer | 下記 | 質問後に届いたユーザー入力へ関連付け、指定した1件だけ閉じる |
| withdraw | `{"question_id":"Q-1","detail":"対象の変更で旧質問を取り下げ"}` | 理由付きで取り下げ。承認や回答を付与しない |
| status | なし | 読み取りのみ。未処理入力、承認、停止、ログの鮮度 |
| check | なし | 承認の整合性を検査。改変検知時は承認をリセットしてコード2 |
| sync | なし | 状態から log.md と user-checks.md を再生成。状態の revision は変えない |
| receive | 下記 | 一度の保存でメッセージの全 intent を登録 |
| resolve | `{"input_id":"I-2","outcome":"task","task_id":"T-3","detail":"修正を登録"}` | 指定入力だけを処理済みにする |
| dismiss | `{"input_id":"I-1","outcome":"superseded","detail":"新版を提示したため旧版への承認は適用しない"}` | 取り下げ・後続指示による無効化。理由必須 |
| resume | `{"input_id":"I-3"}` | 実際の resume 入力で停止解除 |
| note | `{"kind":"decision","text":"判断と理由","related":"I-2"}` | 判断・指摘・提案・報告を E-n で追記 |
| transition | `{"phase":"requirements"}` | hearing→requirements、specification→design、design→development のみ |
| present | `{"summary":"提示する要件の要約と前回との差分"}` | 要件ハッシュを固定し awaiting_approval へ |
| approve | `{"input_id":"I-1","by":"user","sha256":"presentの結果"}` | 提示後に受領した承認を適用し specification へ |
| reset | `{"reason":"要件追加のため","phase":"requirements"}` | 旧承認を証跡に残し解除。phase は hearing も可 |
| reopen | `{"input_id":"I-4"}` | done から bug を根拠に development へ。入力自体は未解決 |
| work | `{"actor":"実際のコンテキストID","detail":"T-3 の実装"}` | 実装者登録。以前の最終レビューを無効化 |
| snapshot | なし | 現在の成果物ハッシュ。result に返る |
| review | 下記 | 担当・対象・証拠を結び付けた検証記録 |
| complete | なし | 完了条件を満たす場合だけ done へ |

receive の例。一つの発言を一つの完了フラグにまとめない。

```json
{
  "source_key": "session-a-message-12",
  "message": "提示された要件を承認します。保存時の不具合も直してください。",
  "intents": [
    {"kind": "approval", "text": "提示された要件を承認します"},
    {"kind": "bug", "text": "保存時の不具合を修正してください"}
  ]
}
```

同じ source_key と同じ内容の再送は同じ I-n を返す。異なる内容なら拒否する。
kind: approval / change / bug / question / instruction / stop / resume。
resolve の outcome: applied / answered / task / superseded / declined。
bug はタスクへの関連付けを基本とする。誤報・取り下げなら根拠を伴う dismiss。
タスクは実際に docs/tasks.md に存在する未完了のものを使う。完了済み行への関連付けだけで
不具合を閉じない。関連付け時には過去の最終検証記録も無効化する。
change の適用は承認解除後のみ。stop は受領時に停止済みとして解決される。
最新の stop より古い resume は停止を解除できない。古い入力は理由付き dismiss で閉じる。
意味の分類、最新のユーザー意図、承認の実在はエージェントが確認する。

review の例:

```json
{
  "role": "acceptance",
  "reviewer": "実際の独立検証コンテキストID",
  "result": "pass",
  "snapshot": "snapshotコマンドの結果",
  "evidence": "docs/reviews/acceptance-1.md"
}
```

role は code / acceptance / ux / security / editorial、result は pass / fail。
editorial は独立検証済み版からの限定的な説明文書修正に対する自己検証。例:

```json
{
  "role": "editorial",
  "reviewer": "workに登録した実装コンテキストID",
  "result": "pass",
  "snapshot": "snapshotコマンドの結果",
  "evidence": "docs/reviews/editorial-1.md",
  "no_behavior_change": true,
  "reason": "READMEの誤字のみ。意味・操作手順・実行時の挙動に影響なし"
}
```

適用条件と意味の確認は [development.md](development.md)。対象パスは README.md、
docs/help/ と docs/guide/ 配下の非実行 Markdown（AGENTS.md / SKILL.md を除く）、tasks.md の記帳。
CLI は独立完了時の review_baseline に保存したファイルハッシュ集合と累積差分を比較する。
削除・他パスの変更・基準なしは拒否。tasks.md の意味と文書が挙動を変えないことは担当が確認する。
基準は独立完了時のみ更新し、reset とレビュー不合格で破棄する。
既存 version=1 の基準なし状態は通常の独立検証を経て基準を取得できる。
work は登録済みレビューを無効化するが、この基準は維持する。
証拠はコマンド、条件、実結果、要件ごとの合否、未実施項目を含む実ファイル。
complete は全入力の解決、全タスク完了/取り下げ、末尾の acceptance タスク、
code/acceptance の独立した合格（適用条件を満たす場合は editorial の自己検証合格）、
追加された各役割の最新合格、成果物と証拠のハッシュを確認。
優先度・依存・再開欄・後回し候補は tasks.md の記述であり、新しい CLI 状態ではない。
CLI は正規の T-n 行を完了判定に使う。優先度の意味や依存順はエージェントが判断する。
後回し候補表には T-n のチェックボックスを置かない。
作成者IDを変更して独立性を装わない。CLI は実際の会話分離を認証するものではない。

## 保存と障害復旧

正本は docs/hotl.state.json 一つ。承認、フェーズ、入力ごとの処理状態、E-n のイベント、
検証記録を一度にシリアライズする。一時ファイルを fsync 後に os.replace し、親も fsync。
プロセス中断時に旧版または新版の完全な JSON が残る。イベントと入力更新が別々に確定しない。
OS ロックはプロセス終了時に解放される。ロックファイルが残っても手で削除しない。
電源断時の保証は OS とファイルシステムに依存する。ネットワーク共有領域は対象外。

log.md は正本から生成する表示であり追記の正本ではない。状態保存後にログ生成だけ失敗したら
status の log_stale を確認し sync。sync は古いログで正本を上書きしない。
JSON 自体が壊れた場合は自動再初期化しない。Git/バックアップから正常な版を復元し、
その版以降の指示・コード差分を確認してから再開する。
手編集や同じ権限を持つプログラムに対する改ざん防止・人間承認の認証は提供しない。

対象ハッシュは Git の追跡ファイルと gitignore されていない未追跡ファイルから算出する。
requirements/spec/design/tasks の4文書は gitignore にかかわらず必ず含める。
状態・生成ログ・ロック・スキル導入物・docs/reviews は除外。証拠自体は個別ハッシュで照合する。
コードのシンボリックリンク、サブモジュール、親リポジトリ共有は初版の検証対象外として拒否する。
秘密情報の実値を読み込まないため .env は必ず未追跡かつ gitignore にする。
任意名の秘密ファイルまで自動検出できないので、機密ファイルの除外はプロジェクト側で設定する。

## 原版との互換性

原版の schema version=1 と本版の version=1 は framework が異なる。
同名 state を共有して動かさない。新規プロジェクト向けに導入し、原版からの移行は
旧承認・入力・タスク・進捗を調査する別の明示的な作業として扱う。


## 要件・仕様・実装状態の再構成

`trace` / `align` の文書形式と運用は [traceability.md](traceability.md)。
traceは停止中・承認失効中も読み取り可能で、状態やログを変更しない。構造問題はresultではなくトップレベルのissuesに返す（診断取得成功の終了0を、整合性合格と扱わない）。
alignは既存の楽観ロック・原子的保存を使用する。記録後の文書変更はtraceで検出され、completeは再照合まで拒否する。
旧プロジェクトの状態を初期化せず、alignを初めて成功させた時点でこの完了時検査を有効にする。既存の承認・停止・独立レビュー条件は緩和しない。


## 人間の確認待ち

[運用方針](user-checks.md)。askの例:

```json
{"key":"export-review-v1","kind":"decision","title":"出力形式を選ぶ","reason":"利用先によって保存形式が変わるため","recommendation":"既存の利用先に合わせる","options":["形式A","形式B"],"related":["R-1","T-2"],"targets":["docs/spec.md"],"blocking":true}
```

kindはdecision / permission / visual_check。blockingは必須bool。options/related/targetsは省略可。targetsはプロジェクト内の既存ファイルを相対パスで指定し、内容ハッシュを記録する。対象外の外部リビジョンは質問本文で具体的に特定する。秘密情報を本文にもファイルにも登録しない。

answerの例:

```json
{"question_id":"Q-1","input_id":"I-8","outcome":"answered","answer":"形式Aを採用する"}
```

input_idは質問後にreceiveした未処理のinstruction。回答が複数質問にまたがる場合はreceiveでintentを分ける。outcomeはaccepted / declined / answered。要件承認は従来のpresent/approveを使い、answerでは付与できない。質問の対象が変わっていれば回答登録を拒否し、withdraw後に新しいkeyで再提示する。同じkeyで内容や対象版を差し替えない。

stateのquestionsが正本。user-checks.mdは生成物としてsnapshot対象外。生成失敗はstateを巻き戻さず、statusのuser_checks_staleで検出しsyncで復旧する。手書きの同名ファイルやシンボリックリンクを上書きしない。
