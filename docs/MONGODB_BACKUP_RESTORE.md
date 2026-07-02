# MongoDB 備份與還原

本專案使用 MongoDB `thesis_system` database。所有備份固定放在專案根目錄：

```text
backups/mongodb/
```

資料夾命名方式：

```text
backups/mongodb/before_experiment_YYYYMMDD_HHMM/
backups/mongodb/after_experiment_YYYYMMDD_HHMM/
backups/mongodb/daily_YYYYMMDD_HHMM/
```

實際 BSON 檔案會位於：

```text
backups/mongodb/<備份名稱>/thesis_system/*.bson
```

請先安裝 MongoDB Database Tools，確認 PowerShell 可以執行 `mongodump`
與 `mongorestore`。以下指令都必須在專案根目錄執行。

## 建立備份

正式實驗前：

```powershell
$stamp = Get-Date -Format "yyyyMMdd_HHmm"
$backupDir = "backups\mongodb\before_experiment_$stamp"
New-Item -ItemType Directory -Force $backupDir | Out-Null

mongodump `
  --uri="mongodb://127.0.0.1:27017" `
  --db="thesis_system" `
  --out="$backupDir"
```

正式實驗後只需更換資料夾名稱：

```powershell
$stamp = Get-Date -Format "yyyyMMdd_HHmm"
$backupDir = "backups\mongodb\after_experiment_$stamp"
New-Item -ItemType Directory -Force $backupDir | Out-Null

mongodump `
  --uri="mongodb://127.0.0.1:27017" `
  --db="thesis_system" `
  --out="$backupDir"
```

每日備份使用：

```powershell
$stamp = Get-Date -Format "yyyyMMdd_HHmm"
$backupDir = "backups\mongodb\daily_$stamp"
New-Item -ItemType Directory -Force $backupDir | Out-Null

mongodump `
  --uri="mongodb://127.0.0.1:27017" `
  --db="thesis_system" `
  --out="$backupDir"
```

不要省略 `--out`，避免備份被寫入 MongoDB Tools 預設的 `dump/` 資料夾。

## 確認備份成功

`mongodump` 完成後先確認結束代碼：

```powershell
if ($LASTEXITCODE -eq 0) {
  Write-Host "MongoDB 備份成功：$backupDir"
} else {
  Write-Error "MongoDB 備份失敗，請檢查上方錯誤訊息。"
}
```

確認 `thesis_system` 資料夾及 BSON 檔案存在：

```powershell
Test-Path "$backupDir\thesis_system"
Get-ChildItem "$backupDir\thesis_system" -Filter "*.bson"
```

建議同時產生檔案雜湊清單：

```powershell
Get-ChildItem "$backupDir\thesis_system" -File -Recurse |
  Get-FileHash -Algorithm SHA256 |
  Export-Csv "$backupDir\SHA256SUMS.csv" -NoTypeInformation -Encoding UTF8
```

判斷成功的基本條件：

1. `mongodump` 結束代碼為 `0`。
2. `$backupDir\thesis_system` 存在。
3. 資料夾內有 `.bson` 與 `.metadata.json` 檔案。
4. `users`、`parsons_attempts_v2`、`learning_logs` 等主要 collection 有對應 BSON。

## 列出備份資料夾

列出所有備份，最新的顯示在最前面：

```powershell
Get-ChildItem "backups\mongodb" -Directory |
  Sort-Object LastWriteTime -Descending |
  Select-Object Name, LastWriteTime, FullName
```

列出指定備份中的 collection 檔案：

```powershell
$backupDir = "backups\mongodb\before_experiment_YYYYMMDD_HHMM"
Get-ChildItem "$backupDir\thesis_system"
```

## 先還原到驗證資料庫

正式還原前，先還原到 `thesis_system_restore_check`，避免覆蓋正式資料：

```powershell
$backupDir = "backups\mongodb\before_experiment_YYYYMMDD_HHMM"

mongorestore `
  --uri="mongodb://127.0.0.1:27017" `
  --nsFrom="thesis_system.*" `
  --nsTo="thesis_system_restore_check.*" `
  --drop `
  "$backupDir"
```

還原後應人工比較主要 collection 的資料筆數與抽樣內容。確認無誤後，再決定是否
刪除 `thesis_system_restore_check`。

## 還原正式資料庫

正式還原會改寫資料，必須在維護時段進行：

1. 停止 Flask/Waitress 後端，避免還原期間產生新寫入。
2. 先為目前資料庫再做一次備份。
3. 先將目標備份還原到驗證資料庫並確認內容。
4. 確認 `$backupDir` 指向正確備份後再執行：

```powershell
$backupDir = "backups\mongodb\before_experiment_YYYYMMDD_HHMM"

mongorestore `
  --uri="mongodb://127.0.0.1:27017" `
  --nsInclude="thesis_system.*" `
  --drop `
  "$backupDir"
```

`--drop` 會先刪除備份中對應的既有 collection，再以備份內容還原。執行前務必
確認 database 名稱、備份資料夾及時間標記。還原後先確認主要 collection 筆數，
再重新啟動後端。

## 異地備份建議

每次備份成功並完成驗證後，建議將整個備份資料夾再複製一份到：

- 外接硬碟或 USB 儲存裝置。
- 具權限控管的雲端硬碟。
- 學校或實驗室提供的安全儲存空間。

不要只保留專案電腦上的單一副本，也不要將備份加入 Git。若 MongoDB 啟用帳號
密碼驗證，請使用受保護的環境變數提供連線資訊，不要把密碼寫入文件或指令歷程。

官方文件：

- https://www.mongodb.com/docs/database-tools/mongodump/
- https://www.mongodb.com/docs/database-tools/mongorestore/
