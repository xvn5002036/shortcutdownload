# 動圖工廠 iOS

這是給 XHS Pro 捷徑使用的原生 iOS 輔助 App 原始碼，目標對齊目前看到的「動圖工廠」功能。

## 已建立功能

- Live Photo 工具分頁
  - 選圖片
  - 選影片
  - 圖片 + 影片生成 Live Photo
  - 選 Live Photo
  - Live Photo 轉影片 / GIF / 圖片
- Video Tools
  - 影片轉 GIF
  - GIF 品質調整
  - 影片擷取封面
- Image Tools
  - 圖片轉 JPEG
  - 圖片轉 PNG
- App Intents / Shortcuts 基礎整合

## 建置需求

- iOS 17+
- Xcode 16+
- XcodeGen

在 `ios/DynamicFactory` 執行：

```bash
xcodegen generate
open DynamicFactory.xcodeproj
```

第一次在 iPhone 安裝需要在 Xcode 選擇自己的 Apple Development Team 進行簽名。

## 注意

Live Photo 的 Apple 配對 metadata 對不同 iOS 版本可能需要再微調；正式上架前要用實機測試「圖片 + paired video」是否被照片 App 正確認成 Live Photo。其餘圖片、GIF、封面擷取功能均使用 iOS 原生 ImageIO / AVFoundation / Photos API。
