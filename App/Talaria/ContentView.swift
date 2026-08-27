import SwiftUI

/// Phase-0 shell. Connection and first-run surfaces arrive in P2; sessions,
/// streaming chat, and approvals arrive in P3 behind HermesKit.
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
                .foregroundStyle(.primary)
        }
        .padding()
    }
}

#Preview {
    ContentView()
}
