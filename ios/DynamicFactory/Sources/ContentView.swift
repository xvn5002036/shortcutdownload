import SwiftUI
import PhotosUI

struct ContentView: View {
    @State private var selectedTab = 0

    var body: some View {
        NavigationStack {
            VStack(spacing: 0) {
                Picker("工具", selection: $selectedTab) {
                    Text("Live Photo 工具").tag(0)
                    Text("Video Tools").tag(1)
                    Text("Image Tools").tag(2)
                }
                .pickerStyle(.segmented)
                .padding()

                ScrollView {
                    Group {
                        if selectedTab == 0 { LivePhotoToolsView() }
                        if selectedTab == 1 { VideoToolsView() }
                        if selectedTab == 2 { ImageToolsView() }
                    }
                    .padding(.horizontal)
                    .padding(.bottom, 24)
                }
            }
            .navigationTitle("動圖工廠")
        }
    }
}

private struct ToolCard<Content: View>: View {
    let title: String
    @ViewBuilder let content: Content

    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            Text(title).font(.headline)
            content
        }
        .padding()
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(.thinMaterial)
        .clipShape(RoundedRectangle(cornerRadius: 18))
    }
}

struct LivePhotoToolsView: View {
    @State private var photo: PhotosPickerItem?
    @State private var video: PhotosPickerItem?
    @State private var livePhoto: PhotosPickerItem?
    @State private var status = ""

    var body: some View {
        VStack(spacing: 16) {
            ToolCard(title: "圖片 + 影片生成 Live Photo") {
                PhotosPicker("選圖片", selection: $photo, matching: .images)
                    .buttonStyle(.borderedProminent)
                PhotosPicker("選影片", selection: $video, matching: .videos)
                    .buttonStyle(.bordered)
                Button("生成 Live Photo") {
                    Task { status = await MediaConverter.shared.makeLivePhoto(photo: photo, video: video) }
                }
                .buttonStyle(.borderedProminent)
                .disabled(photo == nil || video == nil)
            }

            ToolCard(title: "Live Photo 匯出") {
                PhotosPicker("選 Live Photo", selection: $livePhoto, matching: .livePhotos)
                    .buttonStyle(.borderedProminent)
                HStack {
                    Button("轉影片") { Task { status = await MediaConverter.shared.exportLivePhoto(livePhoto, as: .video) } }
                    Button("轉 GIF") { Task { status = await MediaConverter.shared.exportLivePhoto(livePhoto, as: .gif) } }
                    Button("轉圖片") { Task { status = await MediaConverter.shared.exportLivePhoto(livePhoto, as: .image) } }
                }
                .buttonStyle(.bordered)
                .disabled(livePhoto == nil)
            }

            if !status.isEmpty { Text(status).font(.footnote).foregroundStyle(.secondary) }
        }
    }
}

struct VideoToolsView: View {
    @State private var video: PhotosPickerItem?
    @State private var quality = 0.8
    @State private var status = ""

    var body: some View {
        VStack(spacing: 16) {
            ToolCard(title: "Video Tools") {
                PhotosPicker("選影片", selection: $video, matching: .videos)
                    .buttonStyle(.borderedProminent)
                VStack(alignment: .leading) {
                    Text("GIF 品質 \(Int(quality * 100))%")
                    Slider(value: $quality, in: 0.3...1.0)
                }
                Button("影片轉 GIF") { Task { status = await MediaConverter.shared.videoToGIF(video, quality: quality) } }
                    .buttonStyle(.borderedProminent)
                    .disabled(video == nil)
                Button("從影片擷取封面") { Task { status = await MediaConverter.shared.extractCover(video) } }
                    .buttonStyle(.bordered)
                    .disabled(video == nil)
            }
            if !status.isEmpty { Text(status).font(.footnote).foregroundStyle(.secondary) }
        }
    }
}

struct ImageToolsView: View {
    @State private var image: PhotosPickerItem?
    @State private var status = ""

    var body: some View {
        VStack(spacing: 16) {
            ToolCard(title: "Image Tools") {
                PhotosPicker("選圖片或 GIF", selection: $image, matching: .images)
                    .buttonStyle(.borderedProminent)
                HStack {
                    Button("轉 JPEG") { Task { status = await MediaConverter.shared.convertImage(image, format: .jpeg) } }
                    Button("轉 PNG") { Task { status = await MediaConverter.shared.convertImage(image, format: .png) } }
                }
                .buttonStyle(.bordered)
                .disabled(image == nil)
            }
            if !status.isEmpty { Text(status).font(.footnote).foregroundStyle(.secondary) }
        }
    }
}
