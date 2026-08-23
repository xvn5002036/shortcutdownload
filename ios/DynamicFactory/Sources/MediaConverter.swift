import AVFoundation
import ImageIO
import Photos
import PhotosUI
import UniformTypeIdentifiers
import UIKit

@MainActor
final class MediaConverter {
    static let shared = MediaConverter()

    enum ImageFormat { case jpeg, png }
    enum LiveExport { case video, gif, image }

    func convertImage(_ item: PhotosPickerItem?, format: ImageFormat) async -> String {
        guard let item,
              let data = try? await item.loadTransferable(type: Data.self),
              let image = UIImage(data: data) else {
            return "無法讀取圖片"
        }

        let output: Data?
        let ext: String
        switch format {
        case .jpeg:
            output = image.jpegData(compressionQuality: 0.95)
            ext = "jpg"
        case .png:
            output = image.pngData()
            ext = "png"
        }

        guard let output else { return "轉換失敗" }
        return await saveDataToPhotos(output, extension: ext) ? "已儲存到照片" : "儲存失敗"
    }

    func extractCover(_ item: PhotosPickerItem?) async -> String {
        guard let url = await temporaryURL(from: item, extension: "mov") else {
            return "無法讀取影片"
        }

        let asset = AVURLAsset(url: url)
        let generator = AVAssetImageGenerator(asset: asset)
        generator.appliesPreferredTrackTransform = true

        do {
            let cg = try generator.copyCGImage(at: .zero, actualTime: nil)
            guard let data = UIImage(cgImage: cg).jpegData(compressionQuality: 0.95) else {
                return "封面轉換失敗"
            }
            return await saveDataToPhotos(data, extension: "jpg") ? "封面已儲存" : "儲存失敗"
        } catch {
            return "擷取封面失敗：\(error.localizedDescription)"
        }
    }

    func videoToGIF(_ item: PhotosPickerItem?, quality: Double) async -> String {
        guard let url = await temporaryURL(from: item, extension: "mov") else {
            return "無法讀取影片"
        }

        let asset = AVURLAsset(url: url)
        let duration = asset.duration.seconds
        guard duration.isFinite, duration > 0 else { return "影片長度無效" }

        let fps = max(4, min(12, Int(8 * quality)))
        let frameCount = min(120, max(2, Int(duration * Double(fps))))
        let generator = AVAssetImageGenerator(asset: asset)
        generator.appliesPreferredTrackTransform = true
        generator.maximumSize = CGSize(width: 1080 * quality, height: 1080 * quality)

        let out = FileManager.default.temporaryDirectory
            .appendingPathComponent(UUID().uuidString)
            .appendingPathExtension("gif")

        guard let dest = CGImageDestinationCreateWithURL(
            out as CFURL,
            UTType.gif.identifier as CFString,
            frameCount,
            nil
        ) else {
            return "GIF 建立失敗"
        }

        CGImageDestinationSetProperties(
            dest,
            [kCGImagePropertyGIFDictionary: [kCGImagePropertyGIFLoopCount: 0]] as CFDictionary
        )

        let delay = max(0.06, 1.0 / Double(fps))
        for i in 0..<frameCount {
            let t = CMTime(seconds: duration * Double(i) / Double(frameCount), preferredTimescale: 600)
            if let cg = try? generator.copyCGImage(at: t, actualTime: nil) {
                let props = [
                    kCGImagePropertyGIFDictionary: [kCGImagePropertyGIFDelayTime: delay]
                ] as CFDictionary
                CGImageDestinationAddImage(dest, cg, props)
            }
        }

        guard CGImageDestinationFinalize(dest),
              let data = try? Data(contentsOf: out) else {
            return "GIF 輸出失敗"
        }

        return await saveDataToPhotos(data, extension: "gif") ? "GIF 已儲存" : "儲存失敗"
    }

    func makeLivePhoto(photo: PhotosPickerItem?, video: PhotosPickerItem?) async -> String {
        guard let photo,
              let video,
              let photoData = try? await photo.loadTransferable(type: Data.self),
              let videoURL = await temporaryURL(from: video, extension: "mov") else {
            return "請選擇圖片與影片"
        }

        let id = UUID().uuidString
        let pairedPhoto = FileManager.default.temporaryDirectory.appendingPathComponent("\(id).jpg")
        let pairedVideo = FileManager.default.temporaryDirectory.appendingPathComponent("\(id).mov")

        do {
            try LivePhotoMetadata.writePhoto(data: photoData, assetIdentifier: id, to: pairedPhoto)
            try await LivePhotoMetadata.writeVideo(source: videoURL, assetIdentifier: id, to: pairedVideo)

            let ok: Bool = await withCheckedContinuation { continuation in
                PHPhotoLibrary.shared().performChanges {
                    let request = PHAssetCreationRequest.forAsset()
                    request.addResource(with: .photo, fileURL: pairedPhoto, options: nil)
                    request.addResource(with: .pairedVideo, fileURL: pairedVideo, options: nil)
                } completionHandler: { success, _ in
                    continuation.resume(returning: success)
                }
            }

            return ok ? "Live Photo 已儲存" : "Live Photo 儲存失敗"
        } catch {
            return "Live Photo 生成失敗：\(error.localizedDescription)"
        }
    }

    func exportLivePhoto(_ item: PhotosPickerItem?, as kind: LiveExport) async -> String {
        guard let item else { return "請選擇 Live Photo" }

        switch kind {
        case .image:
            return await convertImage(item, format: .jpeg)
        case .video:
            guard let url = await temporaryURL(from: item, extension: "mov") else {
                return "無法取得 Live Photo 影片"
            }
            return await saveVideo(url) ? "影片已儲存" : "影片匯出失敗"
        case .gif:
            return await videoToGIF(item, quality: 0.8)
        }
    }

    private func temporaryURL(from item: PhotosPickerItem?, extension ext: String) async -> URL? {
        guard let item,
              let data = try? await item.loadTransferable(type: Data.self) else {
            return nil
        }

        let url = FileManager.default.temporaryDirectory
            .appendingPathComponent(UUID().uuidString)
            .appendingPathExtension(ext)

        do {
            try data.write(to: url)
            return url
        } catch {
            return nil
        }
    }

    private func saveDataToPhotos(_ data: Data, extension ext: String) async -> Bool {
        let url = FileManager.default.temporaryDirectory
            .appendingPathComponent(UUID().uuidString)
            .appendingPathExtension(ext)

        do {
            try data.write(to: url)
        } catch {
            return false
        }

        return await withCheckedContinuation { continuation in
            PHPhotoLibrary.shared().performChanges {
                PHAssetCreationRequest.forAsset().addResource(with: .photo, fileURL: url, options: nil)
            } completionHandler: { ok, _ in
                continuation.resume(returning: ok)
            }
        }
    }

    private func saveVideo(_ url: URL) async -> Bool {
        await withCheckedContinuation { continuation in
            PHPhotoLibrary.shared().performChanges {
                PHAssetChangeRequest.creationRequestForAssetFromVideo(atFileURL: url)
            } completionHandler: { ok, _ in
                continuation.resume(returning: ok)
            }
        }
    }
}

enum LivePhotoMetadata {
    static func writePhoto(data: Data, assetIdentifier: String, to output: URL) throws {
        guard let source = CGImageSourceCreateWithData(data as CFData, nil),
              let type = CGImageSourceGetType(source),
              let dest = CGImageDestinationCreateWithURL(output as CFURL, type, 1, nil) else {
            throw NSError(domain: "LivePhoto", code: 1)
        }

        let props = (CGImageSourceCopyPropertiesAtIndex(source, 0, nil) as? [CFString: Any]) ?? [:]
        var mutable = props
        var maker = (mutable[kCGImagePropertyMakerAppleDictionary] as? [String: Any]) ?? [:]
        maker["17"] = assetIdentifier
        mutable[kCGImagePropertyMakerAppleDictionary] = maker

        CGImageDestinationAddImageFromSource(dest, source, 0, mutable as CFDictionary)
        guard CGImageDestinationFinalize(dest) else {
            throw NSError(domain: "LivePhoto", code: 2)
        }
    }

    static func writeVideo(source: URL, assetIdentifier: String, to output: URL) async throws {
        let asset = AVURLAsset(url: source)
        let composition = AVMutableComposition()
        let duration = asset.duration
        let range = CMTimeRange(start: .zero, duration: duration)

        for track in asset.tracks {
            guard let dst = composition.addMutableTrack(
                withMediaType: track.mediaType,
                preferredTrackID: kCMPersistentTrackID_Invalid
            ) else {
                continue
            }
            try dst.insertTimeRange(range, of: track, at: .zero)
            dst.preferredTransform = track.preferredTransform
        }

        let item = AVMutableMetadataItem()
        item.keySpace = .quickTimeMetadata
        item.key = "com.apple.quicktime.content.identifier" as NSString
        item.value = assetIdentifier as NSString

        guard let exporter = AVAssetExportSession(
            asset: composition,
            presetName: AVAssetExportPresetPassthrough
        ) else {
            throw NSError(domain: "LivePhoto", code: 3)
        }

        exporter.outputURL = output
        exporter.outputFileType = .mov
        exporter.metadata = [item]

        await exporter.export()
        if exporter.status != .completed {
            throw exporter.error ?? NSError(domain: "LivePhoto", code: 4)
        }
    }
}
