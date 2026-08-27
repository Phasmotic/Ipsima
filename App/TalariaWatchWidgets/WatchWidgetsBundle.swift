import SwiftUI
import WidgetKit

@main
struct TalariaWatchWidgetsBundle: WidgetBundle {
    var body: some Widget {
        WatchStatusAccessory()
    }
}

struct RunSnapshot: TimelineEntry {
    let date: Date
    let state: String
}

struct SnapshotProvider: TimelineProvider {
    func placeholder(in _: Context) -> RunSnapshot {
        RunSnapshot(date: .now, state: "Idle")
    }

    func getSnapshot(in _: Context, completion: @escaping (RunSnapshot) -> Void) {
        completion(RunSnapshot(date: .now, state: "Idle"))
    }

    func getTimeline(in _: Context, completion: @escaping (Timeline<RunSnapshot>) -> Void) {
        completion(Timeline(entries: [RunSnapshot(date: .now, state: "Idle")], policy: .never))
    }
}

struct WatchStatusAccessory: Widget {
    var body: some WidgetConfiguration {
        StaticConfiguration(kind: "WatchStatusAccessory", provider: SnapshotProvider()) { entry in
            AccessoryRoot(entry: entry)
        }
        .configurationDisplayName("Hermes Status")
        .description("Agent run state on your wrist.")
        .supportedFamilies([
            .accessoryCircular,
            .accessoryRectangular,
            .accessoryInline,
            .accessoryCorner,
        ])
    }
}

struct AccessoryRoot: View {
    @Environment(\.widgetFamily) private var family
    let entry: RunSnapshot

    var body: some View {
        switch self.family {
        case .accessoryCircular, .accessoryCorner:
            ZStack {
                AccessoryWidgetBackground()
                Image(systemName: "bolt.horizontal.circle")
            }
        case .accessoryInline:
            Label(self.entry.state, systemImage: "bolt.horizontal.circle")
        default: // accessoryRectangular
            VStack(alignment: .leading, spacing: 2) {
                Text("Talaria").font(.headline)
                Text(self.entry.state).font(.caption2)
            }
        }
    }
}
