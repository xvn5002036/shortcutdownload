# iPhone 捷徑媒體下載器

把 Facebook、Instagram、X、Bilibili、YouTube、小紅書的**公開**內容網址從 iPhone 分享至「捷徑」，由自架服務處理後存回手機。請只下載自己擁有或已獲授權的內容。

## 架構

GitHub 保存程式碼；Render（或任何支援 Docker 的主機）執行下載服務。GitHub Pages 只能放靜態網頁，無法執行 yt-dlp、gallery-dl 或 FFmpeg，因此不能單獨完成下載。

## 一、放到 GitHub

1. 在 GitHub 建立新的私人 repository。
2. 將本資料夾全部檔案推送到該 repository。
3. 不要把帳號密碼、瀏覽器 Cookie 或 `.env` 上傳至 GitHub。

## 二、部署服務（Render 範例）

1. 登入 Render，選 **New > Blueprint**，連接上述 GitHub repository。
2. Render 會讀取 `render.yaml`，建立 Docker Web Service。
3. 部署完成後，記下網址，例如 `https://iphone-media-downloader.onrender.com`。
4. 第一次建立 Blueprint 時，Render 會要求填入 `DOWNLOAD_API_KEY`。請自行輸入至少 32 個字元並另外保存；這組值只放在 Render 和你的 iPhone，不要提交到 GitHub。若服務已經建立，請到服務的 **Environment** 頁面手動新增或修改它。
5. 瀏覽 `https://你的網址/health`，看到 `{"status":"ok"}` 即完成。

請勿直接雙擊 `app/index.html` 測試。該檔案只是服務提供的介面，直接開啟時沒有後端 API。部署後請開啟 Render 的 HTTPS 網址；網頁測試時把 Render 的 `DOWNLOAD_API_KEY` 填入「API 金鑰」欄位。

免費主機可能休眠、啟動慢或限制檔案大小；大量／長時間實況建議使用付費方案或自己的 VPS。

## 三、建立 iPhone 捷徑

建立新捷徑，命名「下載社群媒體」，並在捷徑詳細資料中開啟「在分享表單中顯示」，接收類型選 **URL**。依序加入：

1. **取得「捷徑輸入」中的 URL**。
2. **字典**，加入文字鍵 `url`，值設為上一步的 URL。
3. **取得 URL 的內容**：
   - URL：`https://你的服務網址/api/download`
   - 方法：`POST`
   - 標頭：`X-API-Key` = Render 中的 `DOWNLOAD_API_KEY`
   - 要求本文：`JSON`
   - JSON：選擇上一步的字典
4. **儲存檔案**，輸入為「URL 的內容」，開啟「詢問儲存位置」。
5. 可再加入 **顯示通知**：「下載完成」。

使用時在 Facebook、Instagram、X、Bilibili、YouTube 或小紅書對公開內容點「分享」→「更多」→「下載社群媒體」。單一媒體會直接儲存；多張圖片／多個媒體會收到 ZIP，存到「檔案」App 後點一下即可解壓縮。

> 「儲存到照片」對 ZIP、某些容器格式會失敗，因此預設使用「儲存檔案」。若只下載 MP4/JPG，可把第 4 步換成「儲存到照片相簿」。

## 支援與限制

- 預期支援公開的 Facebook 影片／Reels／照片、Instagram 貼文／Reels，以及 X 圖片／影片。
- 支援公開的 Bilibili 一般影片與 YouTube 一般影片；預設只處理分享的單一影片，不會下載整份播放清單。
- 支援小紅書公開影片與圖文筆記；請分享單篇筆記網址，`/explore` 首頁本身不是下載項目。
- 小紅書公開直播僅作試驗性處理；官方解析器目前沒有直播專用支援，登入／App 驗證或未直接提供串流的直播不能下載。
- Stories 常要求登入，公開服務不會接收你的 Cookie 或帳密，因此不保證可下載。
- Bilibili 大會員／付費／地區限制內容，以及 YouTube 會員／年齡限制／私人內容不支援。
- 已結束且網站仍提供回放的實況可嘗試；正在直播、DRM、付費、私人或地區限制內容不支援。
- 社群網站會改版；部署時不要永久鎖死 `yt-dlp` 與 `gallery-dl` 版本，重新部署可取得新版解析器。
- Docker 映像已包含 Deno 與 yt-dlp 的 EJS 元件，用來處理 YouTube 的 JavaScript 播放驗證。
- API 只接受上述六個平台的 HTTPS 網址，並有限時與總檔案大小限制。

## 本機測試

```powershell
docker build -t iphone-media-downloader .
docker run --rm -p 8080:8080 -e DOWNLOAD_API_KEY=請換成自己的長金鑰 iphone-media-downloader
```

```powershell
Invoke-WebRequest -Method Post -Uri http://localhost:8080/api/download `
  -Headers @{"X-API-Key"="請換成自己的長金鑰"} `
  -ContentType "application/json" `
  -Body '{"url":"https://x.com/.../status/..."}' `
  -OutFile download.bin
```
