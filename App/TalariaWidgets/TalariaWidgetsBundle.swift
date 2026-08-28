import SwiftUI
import WidgetKit

@main
struct TalariaWidgetsBundle: WidgetBundle {
    var body: some Widget {
        TalariaStatusWidget()
    }
}

struct TalariaStatusWidget: Widget {
    var body: some WidgetConfiguration {
        StaticConfiguration(kind: "TalariaStatusWidget", provider: StatusProvider()) { entry in
            StatusEntryView(entry: entry)
        }
        .configurationDisplayName("Hermes Run Status")
        .description("Current Hermes agent run at a glance.")
        .supportedFamilies([.systemSmall, .systemMedium])
    }
}

struct StatusEntry: TimelineEntry {
    let date: Date
    let headline: String
}

struct StatusProvider: TimelineProvider {
    func placeholder(in _: Context) -> StatusEntry {
        StatusEntry(date: .now, headline: "Idle")
    }

    func getSnapshot(in _: Context, completion: @escaping (StatusEntry) -> Void) {
        completion(StatusEntry(date: .now, headline: "Idle"))
    }

    func getTimeline(in _: Context, completion: @escaping (Timeline<StatusEntry>) -> Void) {
        let entry = StatusEntry(date: .now, headline: "Idle")
        completion(Timeline(entries: [entry], policy: .never))
    }
}

struct StatusEntryView: View {
    let entry: StatusEntry

    var body: some View {
        VStack(alignment: .leading, spacing: 4) {
            Image(systemName: "bolt.horizontal.circle")
                .foregroundStyle(.tint)
            Text(self.entry.headline)
                .font(.headline)
            Text(Date.now, style: .time)
                .font(.caption2)
                .foregroundStyle(.secondary)
        }
        .containerBackground(for: .widget) {}
    }
}
