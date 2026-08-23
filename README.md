# 小紅書助手 Pro｜自架後端

這是一套為 iPhone「捷徑」設計的小紅書媒體處理後端，主要提供公開筆記的媒體解析、原圖／影片／Live Photo 資料取得、高清保存、筆記資訊，以及啟用碼與裝置驗證功能。

> 本專案僅供個人、自有內容或已取得授權的內容使用。請遵守平台條款、著作權與所在地法律。

---

## 專案用途

目前後端主要配合「小紅書助手 Pro」捷徑使用。

支援的核心流程：

- 普通照片／原圖
- 普通影片
- Live Photo／實況圖
- 高清保存
- 筆記標題、描述、作者資訊
- 啟用碼驗證
- 裝置綁定
- API 使用紀錄

目前穩定版本的原則是：

> 一個網址只處理該網址對應的單篇小紅書筆記，不混入留言、推薦內容或其他筆記媒體。

---

## 系統架構

```text
iPhone 捷徑
    ↓
Render Web Service
    ↓
FastAPI / Python
    ↓
媒體解析與轉送
    ↓
Supabase PostgreSQL
    ├─ licenses
    ├─ devices
    └─ api_logs
```

### GitHub

GitHub 保存程式碼與版本紀錄。

### Render

Render 負責執行 FastAPI 後端與 FFmpeg 等伺服器端功能。

目前正式服務網址：

```text
https://shortcutdownload.onrender.com
```

### Supabase

Supabase PostgreSQL 用來保存啟用碼、裝置綁定與 API 紀錄。

資料表：

```text
licenses
devices
api_logs
```

---

## 主要 API

### 首頁

```http
GET /
```

### 健康檢查

```http
GET /health
```

正常情況會回傳服務狀態。

### 小紅書捷徑入口

```http
GET /xhszshq
```

這是目前捷徑主要使用的相容介面。

### 啟用碼驗證

```http
POST /api/verify
```

用於啟用碼、裝置與平台驗證。

### 通用下載 API

```http
POST /api/download
```

### 圖片代理

```http
GET /media/image?url=<encoded-url>
```

### 影片代理

```http
GET /media/video?url=<encoded-url>
```

影片輸出會經過 FFmpeg 重新封裝，以提高 iPhone「照片」App 的相容性。

---

## 捷徑相容欄位

後端為了維持既有捷徑相容性，會保留以下主要欄位。

### 類型判斷

```text
notetype
nt
```

常見值包含：

```text
video
pic
livepic
```

### 普通／原圖

```text
gigl
```

### 高清圖片

```text
eigl
```

### Live Photo

```text
ligl
```

每一組 Live Photo 通常包含：

```json
{
  "cover": "...",
  "livevideo": "..."
}
```

### Live 筆記中的普通圖片

```text
nigl
```

### 筆記資訊

```text
title
desc
author
```

另外保留部分相容別名：

```text
description
note_title
note_desc
nickname
```

---

## 啟用碼系統

後端內建授權與裝置管理功能。

### licenses

保存：

- 啟用碼
- 狀態
- 最大裝置數
- 建立時間
- 到期時間
- 備註

### devices

保存：

- 啟用碼對應裝置
- device_id
- 平台
- 第一次使用時間
- 最後使用時間

### api_logs

保存 API 驗證與使用紀錄。

> 請勿把資料庫密碼、API 密鑰、Cookie、Apple 憑證或其他秘密資訊提交到 GitHub。

---

## 環境變數

至少需要設定：

```text
DATABASE_URL
```

目前正式環境使用 Supabase PostgreSQL Session Pooler。

Render 等 IPv4 主機建議使用 Supabase 的 Session Pooler，而不是只支援 IPv6 的 Direct Connection。

連線格式範例：

```text
postgresql://USER:PASSWORD@HOST:5432/postgres
```

請把實際密碼只放在 Render Environment Variables，不要寫進程式碼或 README。

---

## Render 部署

專案使用 Docker 部署。

主要執行方式：

```text
uvicorn app.compat11:app --host 0.0.0.0 --port $PORT
```

Docker 環境包含：

- Python
- FastAPI
- Uvicorn
- FFmpeg
- yt-dlp
- gallery-dl
- Deno
- PostgreSQL driver

Render 設定 `DATABASE_URL` 後，服務啟動時會連接 PostgreSQL。

---

## 資料庫

目前 PostgreSQL 主要資料表：

```sql
licenses
devices
api_logs
```

後端啟動時會檢查並建立必要資料表。

正式環境目前使用 Supabase，因此即使未來更換 Render，也可以繼續沿用同一套授權資料庫。

---

## 小紅書媒體處理原則

### 圖片

優先取得同一篇筆記中的原始圖片來源，避免使用帶浮水印或不必要縮圖版本。

### 影片

優先處理同一篇筆記中的原始影片來源。

回傳到 iPhone 前可透過 FFmpeg 重新封裝為標準 MP4，以提高捷徑與照片 App 相容性。

### Live Photo

後端負責取得同一篇筆記內正確配對的封面與 Live Video。

Live Photo 最終在 iPhone 上的建立／寫入仍可能依賴 iOS 原生功能或相容 App。

### 高清保存

高清圖片使用：

```text
eigl
```

請勿任意改名，否則既有捷徑會無法讀取。

---

## 穩定功能請勿隨意修改

目前以下流程已視為穩定：

1. 普通照片／原圖
2. 普通影片
3. Live Photo／實況圖
4. 高清保存
5. 筆記資訊

開發新功能時，建議採用新的相容層或獨立 endpoint，不要直接改壞既有媒體欄位與輸出格式。

---

## 專案主要檔案

```text
app/
├─ compat.py
├─ compat7.py
├─ compat8.py
├─ compat9.py
├─ compat10.py
├─ compat11.py
├─ db.py
├─ main.py
├─ admin.html
├─ index.html
└─ join.html

Dockerfile
requirements.txt
README.md
```

目前 Docker 正式入口使用：

```text
app.compat11:app
```

`compat11` 的設計原則是沿用已穩定的下載流程，只補充筆記 metadata，不重寫已穩定媒體解析。

---

## 管理頁面

部署完成後可使用：

```text
https://你的網域/admin
```

以及：

```text
https://你的網域/join
```

實際管理權限與驗證方式以目前後端設定為準。

---

## 本機啟動

### Docker

```bash
docker build -t shortcutdownload .
docker run --rm -p 8080:8080 \
  -e DATABASE_URL="你的 PostgreSQL 連線字串" \
  shortcutdownload
```

瀏覽：

```text
http://localhost:8080/health
```

---

## 安全注意事項

請勿提交以下內容：

- PostgreSQL 密碼
- Supabase database password
- Render secret environment variables
- Apple Developer 憑證
- `.p12`
- Provisioning Profile
- API Key
- Cookie
- 使用者帳號密碼

如果秘密資訊曾公開或提交到 repository，請立即更換／撤銷。

---

## 使用限制

- 僅處理可公開存取或你有權使用的內容。
- 不保證平台改版後所有解析永遠有效。
- 小紅書可能調整頁面結構、CDN、驗證方式或媒體格式。
- Render 免費服務可能休眠或有資源限制。
- Supabase 免費方案也可能有流量、容量或閒置限制。
- 大型影片與大量下載建議使用更高規格主機。

---

## 目前狀態

```text
GitHub        ✅ 程式碼版本管理
Render        ✅ FastAPI 後端
Supabase      ✅ PostgreSQL
照片原圖      ✅
普通影片      ✅
Live Photo   ✅ 媒體配對資料
高清保存      ✅
筆記資訊      ✅
啟用碼系統    ✅
裝置綁定      ✅
```

---

## Disclaimer

本專案為個人技術研究與自架工具，不隸屬於、未獲得小紅書或其他第三方平台官方背書。

使用者應自行確認對下載、保存、轉換或再利用內容具有合法權利。