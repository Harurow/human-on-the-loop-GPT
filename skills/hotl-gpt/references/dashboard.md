# 開発ダッシュボード

全HOTLプロジェクトの標準確認入口。ローカル生成物は `workbench/` に置く。新規開始、タスクの区切り、中断前、報告前に正本を更新して `dashboard` または `sync` を実行する。状態を初期化し直さない。静的な生成日時を表示し、ライブ監視と称さない。

```bash
python3 /absolute/skill/scripts/hotl.py --project /absolute/project dashboard
```

設定なしで `workbench/dashboard/index.html` を生成し、`docs/handoff.md` に固定リンクを追加する。CLIはstate・tasks・承認を変更しない。既存handoff本文は保持し、専用マーカー内だけ更新する。`sync` はログ・確認事項一覧とこのビューを再生成する。最終レビューのsnapshotより前に初回生成と設定更新を済ませる。

## 表示の契約と正本

| 表示 | 情報源・意味 |
|---|---|
| 左上のアプリ/プロジェクト名 | 設定のproject_name、未指定ならstate.project |
| 最初に見えるユーザー確認事項 | stateのquestionsとpresented。未回答だけ表示、blocking優先。理由・おすすめ・選択肢・関連先を展開表示。AIの通常作業を確認依頼にしない |
| 現在の作業 | tasks.mdの実行中 `[>]`。プロセスが生きている証拠とはしない |
| 今後の順番 | tasks.mdの未着手 `[ ]` の記載順。担当が優先度・依存を考慮して正本を並べる |
| 待ち・保留 | stateの停止/確認待ちと、タスク行の `待ち: 理由` / `保留: 理由` / 未完了の `依存: T-n`。新しい進捗記号や別台帳は作らない |
| UI一覧 | 設定の画面名・関連T-n・仕様/詳細リンク。進捗はT-nから導出。UIなしならキャプチャ不要 |
| 画面資料 | 明示したcurrentだけ画像・動画を表示。過去候補は折りたたんだリンクに退避。資料の採用/未採用、本編接続、テスト/目視/実機を別々に表示 |
| 詳細と記録 | 要件・仕様・設計・tasks・state・ログ・レビュー・固定handoffへリンク。登録済み検証を現在の合格と推測しない |

タスクの欄が空・不正、資料が別端末にない場合は未登録/未配置と表示し、完了扱いしない。確認事項の登録は [user-checks.md](user-checks.md) の既存コマンドを使う。再開欄・過去の判断の全文を新しい設定へ複写しない。

## プロジェクト単位の設定

任意の `docs/dashboard.json` をGit管理する。省略した値には標準が適用される。versionは1、未知のキーは誤記として拒否する。パスはプロジェクト相対で、外部パス・シンボリックリンクは扱わない。設定は表示と画面資料の索引であり、承認やタスク進捗の正本ではない。

```json
{
  "version": 1,
  "project_name": "アプリ名",
  "title": "開発ダッシュボード",
  "workbench_dir": "workbench",
  "ui": true,
  "screens": [{
    "id": "home",
    "name": "ホーム画面",
    "tasks": ["T-1"],
    "details": "docs/spec.md",
    "current": ["workbench/captures/home.png"],
    "captures": [{
      "path": "workbench/captures/home.png",
      "kind": "image",
      "adoption": "candidate",
      "connected": false,
      "verification": "visual",
      "evidence": "docs/reviews/home.md",
      "recorded_at": "2026-09-12T10:00:00+09:00",
      "retention": "keep"
    }]
  }]
}
```

- UIなしは `{"version":1,"ui":false}`。意味のない撮影を要求しない。
- capturesのkindはimage/video、adoptionはaccepted/candidate/rejected/archived（既定candidate）。採用記録と正式な要件承認は別。
- connectedはtrue/false/null（未確認）。verificationはtest/visual/device/not_checked（既定not_checked）。複数の確認を一つの合格ラベルで代用せず、詳細はevidenceの実記録へ。
- currentは同じ画面のcaptures.pathを選ぶ。rejected/archivedは指定不可。current以外の資料を削除せず、過去資料のリンクで保持する。
- retentionはkeep/reproducible（既定keep）。これは保全の分類であり、自動削除の許可ではない。
- workbench_dirは専用のトップレベル出力フォルダ。既にGit追跡ファイルが入っている場合は拒否し、保存対象を手順に沿って明示的に整理する。自動でGit管理から外さない。

## 既存の独自ダッシュボードを使う

既存生成コード・独自レイアウトはプロジェクト側の定義として保てる。

```json
{
  "version": 1,
  "renderer": "project",
  "entrypoint": "workbench/review-dashboard/index.html",
  "regenerate": "python3 scripts/build_dashboard.py"
}
```

この場合、共通CLIはGit除外とhandoffの入口だけを整え、独自HTMLや生成コードを上書きしない。regenerateは案内文であり、CLIは任意コマンドを自動実行しない。担当が先にプロジェクト側の生成手順を実行して結果を検証し、共通CLIを実行する。共通CLIの返値はgenerated=falseで、独自ビューを更新したとは報告しない。独自版でも基本の表示契約を満たすか確認し、差異をプロジェクトの仕様に記す。

## Gitと保全は別

CLIは `.gitignore` に出力フォルダの除外を追加する。HTML・スクリーンショット・録画などの作業生成物はGitに追加せず、LFSにも自動登録しない。既存の独自HTMLが標準出力先にある場合は上書きを拒否する。

再構築用の生成コード・テンプレート・軽量設定・仕様・state/tasks正本はGit管理。採用した製品素材は製品側の素材フォルダなどへ明示的に配置して管理する。採用素材・再撮影不能な重要記録・検証証拠の保持先を確認し、workbenchや旧artifactsを一括削除しない。Git除外は削除可能の意味ではない。

再生成はHTMLを再構築するだけで、失われたキャプチャや動画を復元しない。再現可能なものは記録した撮影手順で再生成し、再撮影不能なものは別途バックアップを保持する。旧出力の移動・削除・Git追跡解除は共通コマンドの対象外。

## 導入範囲

`--link` 導入先は配布元の生成コード・スキル更新を直接参照する。コピー導入先はインストーラーの `--force` でスキルを更新する（旧版バックアップあり）。どちらも各プロジェクトで生成コマンドを実行して初めてHTML・Git除外・handoffへ反映される。配布元を更新しただけで全プロジェクトの生成物が更新されたとは報告しない。PM親にアプリstateを作らず、子ごとに生成する。
