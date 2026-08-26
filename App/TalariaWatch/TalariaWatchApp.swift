import SwiftUI

@main
struct TalariaWatchApp: App {
    var body: some Scene {
        WindowGroup {
            WatchRootView()
        }
    }
}

struct WatchRootView: View {
    var body: some View {
        VStack(spacing: 6) {
            Image(systemName: "bolt.horizontal.circle")
                .foregroundStyle(.tint)
            Text("Talaria")
                .font(.headline)
            Text("Scaffold")
                .font(.caption2)
                .foregroundStyle(.secondary)
        }
    }
}
