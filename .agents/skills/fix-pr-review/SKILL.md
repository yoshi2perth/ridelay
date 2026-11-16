---
name: fix-pr-review
description: >-
  Fetch unresolved GitHub Pull Request review comments (such as GitHub Copilot Code Review),
  fix the issues in the codebase, run tests, and push the updates.
---

# Fix PR Review Comments

GitHub Pull Request に付いたレビュー指摘（特に GitHub Copilot Code Review）を取得し、修正・検証・プッシュまでを行う手順です。

## Prerequisites

- GitHub CLI (`gh`) がインストールされ、認証済みであること（`gh auth status` で確認）。
- `gh` がない場合はユーザーに `brew install gh && gh auth login` を案内する。

## Workflow

1. **現在のブランチと対象 PR を確認する**
   ```bash
   git branch --show-current
   gh pr view --json number,url,headRefName,baseRefName,reviewDecision
   ```
   PR 番号が指定された場合はその番号を使う。

2. **未解決のレビュースレッド（および直近のレビュー本文）を取得する**
   GraphQL API を使って `isResolved == false` のスレッドを抽出すると、対応すべき指摘だけを正確に把握できる：
   ```bash
   gh api graphql -f query='
     query($owner: String!, $repo: String!, $number: Int!) {
       repository(owner: $owner, name: $repo) {
         pullRequest(number: $number) {
           reviews(last: 10) {
             nodes {
               author { login }
               state
               body
             }
           }
           reviewThreads(first: 50) {
             nodes {
               id
               isResolved
               path
               line
               comments(first: 10) {
                 nodes {
                   author { login }
                   body
                 }
               }
             }
           }
         }
       }
     }
   ' -F owner=yoshi2perth -F repo=ridelay -F number=<PR_NUMBER>
   ```

3. **指摘内容を精査してコードを修正する**
   - `isResolved: false` の各スレッドについて、該当ファイル・行を確認する。
   - 妥当な指摘であればコードを修正し、必要なら `tests/` のテストも追加・更新する。
   - 誤検知や仕様上変更不要な指摘である場合は、修正せず理由をレビューコメントへの返信として残す準備をする。

4. **テストを実行して全件パスを確認する**
   ```bash
   .venv/bin/pytest
   ```

5. **変更をコミット・プッシュする**
   - ユーザーに確認するか、指示された範囲でコミットして `git push` する。
   - プッシュ後、対応したスレッドに簡潔な返信を残し、必要に応じてスレッドを解決（resolve）する（`Require conversation resolution before merging` を有効にしている場合は特に重要）。
