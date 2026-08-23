import AppIntents

struct DynamicFactoryShortcuts: AppShortcutsProvider {
    static var appShortcuts: [AppShortcut] {
        AppShortcut(intent: OpenDynamicFactoryIntent(), phrases: ["開啟 \(.applicationName)", "使用 \(.applicationName)"], shortTitle: "開啟動圖工廠", systemImageName: "photo.on.rectangle")
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
