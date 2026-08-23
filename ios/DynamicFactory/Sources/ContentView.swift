import SwiftUI

struct ContentView: View {
    var body: some View {
        NavigationStack {
            VStack(spacing: 20) {
                Image(systemName: "livephoto")
                    .font(.system(size: 72))

                Text("動圖工廠")
                    .font(.largeTitle.bold())

                Text("此版本提供給 iOS 捷徑使用。")
                    .foregroundStyle(.secondary)

                VStack(alignment: .leading, spacing: 10) {
                    Label("捷徑動作：生成 Live Photo", systemImage: "checkmark.circle.fill")
                    Text("輸入一張圖片與一段影片，動圖工廠會配對成 Live Photo 並儲存到照片圖庫。")
                        .font(.footnote)
                        .foregroundStyle(.secondary)
                }
                .padding()
                .frame(maxWidth: .infinity, alignment: .leading)
                .background(.thinMaterial)
                .clipShape(RoundedRectangle(cornerRadius: 18))

                Spacer()
            }
            .padding()
            .navigationTitle("動圖工廠")
        }
    }
}
