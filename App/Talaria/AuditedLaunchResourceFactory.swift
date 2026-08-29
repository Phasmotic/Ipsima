import Foundation
#if TALARIA_LINK_TRANSPORT
    import HermesKit
#endif
#if canImport(Network)
    import Network
#endif

/// The sole Talaria source boundary for resources that can add launch work.
///
/// Each constructor records its intent immediately before construction. The source checker keeps
/// direct construction out of the rest of the app and verifies that this ordering remains intact.
enum AuditedLaunchResourceFactory {
    static func urlSession(
        configuration: URLSessionConfiguration
    ) -> URLSession {
        LaunchActivityAudit.shared.recordTalariaOwnedConstruction(.urlSession)
        return URLSession(configuration: configuration)
    }

    static func sharedURLSession() -> URLSession {
        LaunchActivityAudit.shared.recordTalariaOwnedConstruction(.urlSession)
        return URLSession.shared
    }

    #if TALARIA_LINK_TRANSPORT
        static func webSocketTransport(
            configuration: HermesWebSocketConfiguration,
            tokenProvider: any HermesBearerTokenProvider
        ) -> WebSocketHermesTransport {
            LaunchActivityAudit.shared.recordTalariaOwnedConstruction(.webSocketTransport)
            return WebSocketHermesTransport(
                configuration: configuration,
                tokenProvider: tokenProvider
            )
        }
    #endif

    #if canImport(Network)
        static func networkPathMonitor() -> NWPathMonitor {
            LaunchActivityAudit.shared.recordTalariaOwnedConstruction(.networkPathMonitor)
            return NWPathMonitor()
        }
    #endif

    static func timer(
        timeInterval: TimeInterval,
        repeats: Bool,
        block: @escaping @Sendable (Timer) -> Void
    ) -> Timer {
        LaunchActivityAudit.shared.recordTalariaOwnedConstruction(.timer)
        return Timer(timeInterval: timeInterval, repeats: repeats, block: block)
    }

    @MainActor
    static func scheduledTimer(
        timeInterval: TimeInterval,
        repeats: Bool,
        block: @escaping @Sendable (Timer) -> Void
    ) -> Timer {
        LaunchActivityAudit.shared.recordTalariaOwnedConstruction(.scheduledTimer)
        return Timer.scheduledTimer(
            withTimeInterval: timeInterval,
            repeats: repeats,
            block: block
        )
    }

    static func dispatchTimer(queue: DispatchQueue? = nil) -> any DispatchSourceTimer {
        LaunchActivityAudit.shared.recordTalariaOwnedConstruction(.dispatchTimer)
        return DispatchSource.makeTimerSource(queue: queue)
    }
}
