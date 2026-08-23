import AVFoundation
import Photos
import Foundation

actor ShortcutMediaBridge {
    static let shared = ShortcutMediaBridge()

    func makeLivePhoto(photoData: Data, videoData: Data, videoFilename: String) async -> String {
        guard !photoData.isEmpty, !videoData.isEmpty else {
            return "圖片或影片內容為空"
        }

        let assetIdentifier = UUID().uuidString
        let temp = FileManager.default.temporaryDirectory
        let sourceVideoExt = URL(fileURLWithPath: videoFilename).pathExtension.isEmpty ? "mov" : URL(fileURLWithPath: videoFilename).pathExtension
        let sourceVideo = temp.appendingPathComponent("source-\(assetIdentifier).\(sourceVideoExt)")
        let pairedPhoto = temp.appendingPathComponent("\(assetIdentifier).jpg")
        let pairedVideo = temp.appendingPathComponent("\(assetIdentifier).mov")

        do {
            try videoData.write(to: sourceVideo, options: .atomic)
            try LivePhotoMetadata.writePhoto(data: photoData, assetIdentifier: assetIdentifier, to: pairedPhoto)
            try await LivePhotoMetadata.writeVideo(source: sourceVideo, assetIdentifier: assetIdentifier, to: pairedVideo)

            let permission = await requestPhotoPermission()
            guard permission else { return "沒有照片圖庫寫入權限" }

            let saved = await withCheckedContinuation { continuation in
                PHPhotoLibrary.shared().performChanges {
                    let request = PHAssetCreationRequest.forAsset()
                    let photoOptions = PHAssetResourceCreationOptions()
                    photoOptions.originalFilename = "IMG_\(assetIdentifier).jpg"
                    let videoOptions = PHAssetResourceCreationOptions()
                    videoOptions.originalFilename = "IMG_\(assetIdentifier).mov"
                    request.addResource(with: .photo, fileURL: pairedPhoto, options: photoOptions)
                    request.addResource(with: .pairedVideo, fileURL: pairedVideo, options: videoOptions)
                } completionHandler: { success, _ in
                    continuation.resume(returning: success)
                }
            }

            return saved ? "Live Photo 已儲存到照片" : "Live Photo 儲存失敗"
        } catch {
            return "Live Photo 生成失敗：\(error.localizedDescription)"
        }
    }

    private func requestPhotoPermission() async -> Bool {
        let current = PHPhotoLibrary.authorizationStatus(for: .addOnly)
        if current == .authorized || current == .limited { return true }
        if current == .denied || current == .restricted { return false }
        let newStatus = await PHPhotoLibrary.requestAuthorization(for: .addOnly)
        return newStatus == .authorized || newStatus == .limited
    }
}
