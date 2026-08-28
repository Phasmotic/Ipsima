import Foundation

/// A Talaria-owned resource whose construction could add work before the first app frame.
///
/// This audit deliberately covers only constructions initiated by Talaria source. It does not
/// claim to intercept resources that Apple frameworks or linked packages create internally.
enum TalariaOwnedLaunchResource: String, CaseIterable, Sendable {
    case dispatchTimer
    case networkPathMonitor
    case reachability
    case scheduledTimer
    case timer
    case urlSession
    case webSocketTransport
}

struct LaunchActivityRecord: Equatable, Sendable {
    let resource: TalariaOwnedLaunchResource
    let occurredBeforeFirstFrame: Bool
}

struct LaunchActivitySnapshot: Equatable, Sendable {
    let firstFrameRecorded: Bool
    let records: [LaunchActivityRecord]

    var resourcesConstructedBeforeFirstFrame: [TalariaOwnedLaunchResource] {
        self.records.compactMap { record in
            record.occurredBeforeFirstFrame ? record.resource : nil
        }
    }

    var isClearAtFirstFrame: Bool {
        self.firstFrameRecorded && self.resourcesConstructedBeforeFirstFrame.isEmpty
    }
}

/// Thread-safe launch-order evidence for constructions routed through
/// `AuditedLaunchResourceFactory`.
final class LaunchActivityAudit: @unchecked Sendable {
    static let shared = LaunchActivityAudit()

    private struct State {
        var firstFrameRecorded = false
        var records: [LaunchActivityRecord] = []
    }

    private let lock = NSLock()
    private var state = State()

    func recordTalariaOwnedConstruction(_ resource: TalariaOwnedLaunchResource) {
        self.withLockedState { state in
            state.records.append(
                LaunchActivityRecord(
                    resource: resource,
                    occurredBeforeFirstFrame: !state.firstFrameRecorded
                )
            )
        }
    }

    @discardableResult
    func recordFirstFrame() -> LaunchActivitySnapshot {
        self.withLockedState { state in
            state.firstFrameRecorded = true
            return Self.snapshot(of: state)
        }
    }

    func snapshot() -> LaunchActivitySnapshot {
        self.withLockedState { state in
            Self.snapshot(of: state)
        }
    }

    private static func snapshot(of state: State) -> LaunchActivitySnapshot {
        LaunchActivitySnapshot(
            firstFrameRecorded: state.firstFrameRecorded,
            records: state.records
        )
    }

    private func withLockedState<Result>(_ body: (inout State) -> Result) -> Result {
        self.lock.lock()
        defer { self.lock.unlock() }
        return body(&self.state)
    }
}
