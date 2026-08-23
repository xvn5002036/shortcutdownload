import AppIntents
import UniformTypeIdentifiers

struct DynamicFactoryShortcuts: AppShortcutsProvider {
    static var appShortcuts: [AppShortcut] {
        AppShortcut(
            intent: OpenDynamicFactoryIntent(),
            phrases: ["開啟 \(.applicationName)", "使用 \(.applicationName)"],
            shortTitle: "開啟動圖工廠",
            systemImageName: "photo.on.rectangle"
        )
        AppShortcut(
            intent: MakeLivePhotoIntent(),
            phrases: ["用 \(.applicationName) 生成 Live Photo", "使用 \(.applicationName) 製作實況照片"],
            shortTitle: "生成 Live Photo",
            systemImageName: "livephoto"
        )
    }
}

struct OpenDynamicFactoryIntent: AppIntent {
    static var title: LocalizedStringResource = "開啟動圖工廠"
    static var description = IntentDescription("從捷徑開啟動圖工廠，繼續處理 Live Photo、影片、GIF 或圖片。")
    static var openAppWhenRun = true

    func perform() async throws -> some IntentResult {
        .result()
    }
}

struct MakeLivePhotoIntent: AppIntent {
    static var title: LocalizedStringResource = "生成 Live Photo"
    static var description = IntentDescription("接收一張圖片與一段影片，直接生成 Live Photo 並存入照片圖庫。")
    static var openAppWhenRun = false

    @Parameter(title: "圖片", supportedTypeIdentifiers: [UTType.image.identifier])
    var photo: IntentFile

    @Parameter(title: "影片", supportedTypeIdentifiers: [UTType.movie.identifier, UTType.video.identifier])
    var video: IntentFile

    static var parameterSummary: some ParameterSummary {
        Summary("用 \(.$photo) 和 \(.$video) 生成 Live Photo")
    }

    func perform() async throws -> some IntentResult & ProvidesDialog {
        let result = await ShortcutMediaBridge.shared.makeLivePhoto(
            photoData: photo.data,
            videoData: video.data,
            videoFilename: video.filename
        )
        return .result(dialog: IntentDialog(stringLiteral: result))
    }
}
