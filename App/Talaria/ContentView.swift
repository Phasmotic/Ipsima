import SwiftUI

/// Phase-0 shell. Real surfaces (connection registry, sessions, streaming
/// chat, approvals) arrive in P2 behind HermesKit.
struct ContentView: View {
    var body: some View {
        VStack(spacing: 12) {
            Image(systemName: "bolt.horizontal.circle")
                .font(.system(size: 56))
                .foregroundStyle(.tint)
            Text("Talaria")
                .font(.largeTitle.bold())
            Text("Hermes Agent client — scaffold")
                .font(.footnote)
                .foregroundStyle(.secondary)
        }
        .padding()
    }
}

#Preview {
    ContentView()
}
