# human-on-the-loop-GPT

要件承認後の仕様化・設計・開発・検証を Codex が進める、ローカル開発スキル。
Claude Code 向けの `human-on-the-loop` を参考にした GPT/Codex 版です。

```text
ヒアリング → 要件定義 → 人間の承認 → 仕様化 → 設計 → 実装・独立検証 → 完了
```

人間は要件と自律実行範囲を決め、進捗を受け取り、必要なら途中で指示・停止できます。
要件が変わった場合は、差分を示して新しい版を承認します。

## 導入

必要: Python 3.9 以上、Git、macOS/Linux、ローカルでファイルとコマンドを扱える Codex。
このフレームワーク自体は API キーや追加の Python パッケージを必要としません。

```bash
mkdir -p ~/code/my-gpt-app
bash /Users/friday/code/human-on-the-loop-GPT/install.sh --target ~/code/my-gpt-app
```

対象フォルダを Codex で開き、`$hotl-gpt 家計簿アプリを作ってください` のように伝えます。
再開は `$hotl-gpt 続きから`、状況確認は `$hotl-gpt 進捗を教えて`。
導入先は `.agents/skills/`。認識されない場合は Codex を再起動してください。
配置と発見方法は [OpenAI のスキル公式ドキュメント](https://learn.chatgpt.com/docs/build-skills) に基づきます。

| オプション | 内容 |
|---|---|
| --target | 導入先の既存ディレクトリ |
| --pm | 複数プロジェクト用 hotl-gpt-pm も導入 |
| --link | 配布元へのシンボリックリンク。フレームワーク開発時向け |
| --force | 既存導入物を隠しバックアップへ移して更新 |

インストーラーは導入先のスキルだけを扱います。グローバル設定、API キー、既存 AGENTS.md は変更しません。
通常のコピー導入が独立して動作することをテストしています。--link では配布元を移動しないでください。
バックアップは `.agents/hotl-backups/`。スキル探索先の外に置き、旧版の重複検出を避けます。
更新が部分的に失敗した場合は結果と配置を確認し、再実行します。

## 改善した点

- 承認、フェーズ、入力、証跡を一つの状態ファイルへ原子的に保存。
- 「承認と不具合報告」のような複合メッセージを I-n ごとに処理。
- 提示した要件のハッシュと、提示後の承認入力を結び付け。
- 実装者と検証者の記録を分け、古い成果物や変更された証拠による完了を拒否。
- 再開や承認の回帰を Python の実行テストで確認。
- 再承認は差分を先に、最終報告は成果・起動方法・制限を中心に提示。
- 主要操作の早期確認と、変更リスクに応じたレビュー。微調整はまとめて独立担当1人が確認し、再検証は差分と影響先に限定。
- 独立完了版からの限定的な説明文書修正は、根拠を記録した自己検証で完了可能。詳しくは [実装・検証手順](skills/hotl-gpt/references/development.md)。

## 複数プロジェクト

```bash
mkdir -p ~/code/gpt-projects
bash /Users/friday/code/human-on-the-loop-GPT/install.sh --target ~/code/gpt-projects --pm
```

親を Codex で開き `$hotl-gpt-pm 各プロジェクトの状況を教えて`。
各子プロジェクトの状態から一覧を作り、対象を指定した指示を振り分けます。
承認済み区間はサブエージェントが使える場合に並列化します。コードを書く担当は各プロジェクト1体。
子を直接 Codex で開く場合は、その子にも通常のインストールを行ってください。

## 成果物

| パス | 内容 |
|---|---|
| docs/hotl.state.json | 状態・承認・入力・イベント・検証記録の正本 |
| docs/log.md | 状態から生成する読み取り用の判断ログ |
| docs/hearing-notes.md | 目的・利用場面・採用/見送り |
| docs/requirements.md | 承認対象。承認後は変更しない |
| docs/spec.md / docs/design.md | 要件と対応付けた仕様・設計 |
| docs/tasks.md | タスク進捗の唯一の正本 |
| docs/reviews/ | 実行した検証の証拠 |
| docs/lessons.md | 必要時に残す知見 |

状態操作の詳細は [runtime.md](skills/hotl-gpt/references/runtime.md)、設計の理由は
[PRINCIPLES.md](PRINCIPLES.md) を参照してください。

## 検証

```bash
cd /Users/friday/code/human-on-the-loop-GPT
python3 -m unittest discover -s tests -v
python3 scripts/check_consistency.py
```

実施結果と限界は [docs/validation.md](docs/validation.md) に記載します。
スキル全体が任意のアプリ開発を必ず完走することを保証するものではありません。
独立レビューが必要な変更でレビュー環境が使えない場合は、実装と自己検証まで進め、未検証を隠して done にしません。
原版の既存 state は読み替えず拒否します。新規プロジェクト用の初版です。


## 要件・仕様・進捗の最新性

[正本と照合の運用](skills/hotl-gpt/references/traceability.md)を追加しました。
`trace` は要件→仕様→タスクと各状態を読み取り専用で生成し、重複IDや参照漏れを検出します。
`align` は意味・参照を確認した文書版を記録し、後から変更された版での完了を防ぎます。
本文やチェック状態の第二の台帳は作りません。旧プロジェクトではtraceで診断してから導入します。
文章の意味の重複・完全性や実装品質を自動保証するものではありません。
