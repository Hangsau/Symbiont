# Memory Schema

所有 `memory/` 下的記憶條目必須遵守此格式。

## Frontmatter 欄位

```markdown
---
name: 記憶名稱
description: 一行描述，說明這條記憶的用途
type: feedback | project | reference | user | thoughts | reflection
created: YYYY-MM-DD（無法確定填 unknown）
valid_until: null（目前有效）| YYYY-MM-DD（已於該日失效）
review_by: YYYY-MM-DD（project/reference 必填，建立後 90 天）| null
superseded_by: null | 新版檔名.md
---
```

## 欄位說明

| 欄位 | 必填 | 說明 |
|------|------|------|
| `name` | 是 | 人類可讀的標題 |
| `description` | 是 | 一行摘要，MEMORY.md 索引用這行判斷要不要讀全文 |
| `type` | 是 | 分類（見下） |
| `created` | 是 | 首次建立日期 |
| `valid_until` | 是 | `null` = 有效；日期 = 已失效，local-agent 會自動歸檔 |
| `review_by` | 條件 | `project`/`reference` 類必填（建立後 90 天）；到期時 audit 會列出待確認 |
| `superseded_by` | 是 | `null` = 無繼承者；填檔名 = 被哪個新記憶取代 |

## Type 定義

| Type | 用途 |
|------|------|
| `feedback` | 使用者糾正過的行為，或確認有效的做法 |
| `project` | 專案脈絡、動機、決策、當前狀態 |
| `reference` | 外部系統的位置指針（URL、路徑、工具名稱） |
| `user` | 使用者的角色、偏好、領域背景 |
| `thoughts` | 後設認知洞見 |
| `reflection` | 重要對話或事件的紀錄 |

## MEMORY.md 格式

```markdown
# Memory Index

- [標題](檔名.md) — 一行描述（≤150 字）
```

- 只列 `valid_until: null` 的條目
- 超過 170 行時 local-agent 會警告

## Update 操作（禁止直接覆蓋）

1. 舊檔加 `valid_until: <今天>` 和 `superseded_by: <新檔名>.md`，移至 `archive/`
2. 建新版（命名加 `_v2`、`_v3`...），`valid_until: null`
3. MEMORY.md 索引改指向新檔

## Delete 操作

1. 加 `valid_until: <今天>`，`superseded_by: null`
2. 移至 `archive/`
3. 從 MEMORY.md 移除該行
