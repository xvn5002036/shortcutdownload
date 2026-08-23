import AVFoundation
import Photos
import Foundation
import ImageIO

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

            let saved: Bool = await withCheckedContinuation { continuation in
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

private enum LivePhotoMetadata {
    static func writePhoto(data: Data, assetIdentifier: String, to output: URL) throws {
        guard let source = CGImageSourceCreateWithData(data as CFData, nil),
              let sourceType = CGImageSourceGetType(source),
              let destination = CGImageDestinationCreateWithURL(output as CFURL, sourceType, 1, nil) else {
            throw NSError(domain: "DynamicFactory.LivePhoto", code: 1)
        }

        var properties = (CGImageSourceCopyPropertiesAtIndex(source, 0, nil) as? [CFString: Any]) ?? [:]
        var makerApple = (properties[kCGImagePropertyMakerAppleDictionary] as? [String: Any]) ?? [:]
        makerApple["17"] = assetIdentifier
        properties[kCGImagePropertyMakerAppleDictionary] = makerApple

        CGImageDestinationAddImageFromSource(destination, source, 0, properties as CFDictionary)

        guard CGImageDestinationFinalize(destination) else {
            throw NSError(domain: "DynamicFactory.LivePhoto", code: 2)
        }
    }

    static func writeVideo(source: URL, assetIdentifier: String, to output: URL) async throws {
        let asset = AVURLAsset(url: source)
        let duration = try await asset.load(.duration)
        let tracks = try await asset.load(.tracks)

        let composition = AVMutableComposition()
        let range = CMTimeRange(start: .zero, duration: duration)

        for track in tracks {
            guard let destinationTrack = composition.addMutableTrack(
                withMediaType: track.mediaType,
                preferredTrackID: kCMPersistentTrackID_Invalid
            ) else {
                continue
            }

            try destinationTrack.insertTimeRange(range, of: track, at: .zero)

            if track.mediaType == .video {
                destinationTrack.preferredTransform = try await track.load(.preferredTransform)
            }
        }

        let contentIdentifier = AVMutableMetadataItem()
        contentIdentifier.keySpace = .quickTimeMetadata
        contentIdentifier.key = "com.apple.quicktime.content.identifier" as NSString
        contentIdentifier.value = assetIdentifier as NSString

        guard let exporter = AVAssetExportSession(
            asset: composition,
            presetName: AVAssetExportPresetPassthrough
        ) else {
            throw NSError(domain: "DynamicFactory.LivePhoto", code: 3)
        }

        exporter.outputURL = output
        exporter.outputFileType = .mov
        exporter.metadata = [contentIdentifier]

        await exporter.export()

        guard exporter.status == .completed else {
            throw exporter.error ?? NSError(domain: "DynamicFactory.LivePhoto", code: 4)
        }
    }
}
