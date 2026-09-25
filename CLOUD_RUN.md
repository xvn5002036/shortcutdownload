# Cloud Run 搬遷：小紅書助手 Pro

目前 Dockerfile 已監聽 Cloud Run 注入的 `PORT`（`0.0.0.0`），入口為 `app.compat11:app`。請從本分支合併後的 `main` 建立服務；不要把資料庫密碼或 token 提交到 GitHub。

## 建立服務

1. 在 Google Cloud 選定專案，確認已啟用計費，打開 [Cloud Run 服務](https://console.cloud.google.com/run)。
2. 選「連結存放區」，透過 Cloud Build 連結 GitHub 的 `xvn5002036/shortcutdownload`，分支 `main`，建置類型選 **Dockerfile**，路徑 `/Dockerfile`。開啟必要 API，讓 Cloud Build 建立 Artifact Registry 映像並部署。若 Google 要求 GitHub 安裝授權，只授權這個 repo 所需範圍。
3. 服務名可用 `shortcutdownload`；區域選 `asia-east1`（台灣）；允許公開呼叫 HTTP，因 iPhone 捷徑不會帶 Google IAM 登入憑證。應用本身仍使用啟用碼驗證。
4. 初始設定：1 vCPU、**2 GiB** 記憶體、每個執行個體最多 **1** 個並行請求、最少執行個體 **0**、最多 **2** 個、請求逾時 **300 秒**、依請求計費。媒體暫存檔佔 Cloud Run 的記憶體；2 GiB 是保守起點，請依實際檔案大小與記憶體觀測調整。不要設定 `PORT`，Cloud Run 會提供。
5. 在容器「變數與密碼」設定：

| 變數 | 值 |
| --- | --- |
| `DATABASE_URL` | 現有 Supabase PostgreSQL **Session Pooler** 連線字串；建議以 Secret Manager 密碼掛入 |
| `XHS_ADMIN_PASSWORD` | 新管理登入密碼；以 Secret Manager 管理。設定後，原本的管理登入密碼不再有效 |
| `XHS_ADMIN_TOKEN` | 管理登入 Cookie 的簽署密鑰，請使用長度足夠的獨立隨機值；以 Secret Manager 管理，這不是登入密碼 |
| `PUBLIC_BASE_URL` | 建立服務後取得的 Cloud Run HTTPS 根網址，**不含結尾斜線**，例如 `https://shortcutdownload-xxxxx.asia-east1.run.app` |
| `MAX_DOWNLOAD_MB` | `250` |
| `DOWNLOAD_TIMEOUT_SECONDS` | `180` |

若目前實際使用其他 Cookie、API key 或自訂變數，對照 Render 的有效設定一起搬過去。`DOWNLOAD_API_KEY` 列於 Render 設定，但目前 `app/main.py` 的下載端點是比對程式內既有雜湊；不要以為只搬這個變數就會改變該端點的驗證規則。

`PUBLIC_BASE_URL` 用來產生 `/media/image` 的完整網址。首次部署時若 Cloud Run 根網址尚未出現，可先部署，再回到服務編輯此變數並部署新修訂版。確認其值指向**新的 Cloud Run 網域**；否則捷徑入口搬過去了，圖片仍會回到 Render。

## 驗證與切換

1. 開啟 `https://你的-cloud-run-網址/health`，確認 JSON 含 `"status":"ok"`。
2. 使用自己有效的啟用碼、裝置 ID 和一篇有權下載的公開筆記，測試 `/xhszshq?a=...&b=ios&c=...&device_id=...`。檢查回傳的 `gigl`／`eigl` 中圖片代理網址是否指向 Cloud Run，並分別實測照片與影片。
3. 成功後，只把 iPhone 捷徑中的 `https://shortcutdownload.onrender.com` 換成 Cloud Run 根網址；`/xhszshq` 與查詢參數保留。先保留 Render 作為回退，確認日常使用正常後再考慮停用。
4. 若 `/health` 失敗，先看 Cloud Run 日誌；這個專案在載入 `app.db` 時會連資料庫並建立資料表，所以錯誤的 `DATABASE_URL`、Supabase 暫停或網路連線失敗會使容器無法啟動。若影片失敗，檢查逾時、記憶體與 iPhone 捷徑自身逾時。

Cloud Run 閒置時可縮到 0，但首次請求仍可能冷啟動；Cloud Build、Artifact Registry 儲存與網路流量可能計費。連結存放庫後推送 `main` 會自動重新建置並部署，並非免費、零等待的保證。
