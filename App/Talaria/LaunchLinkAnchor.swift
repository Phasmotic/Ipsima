#if TALARIA_LINK_TRANSPORT
    import Foundation
    import HermesKit

    private typealias TransportFactoryLinkFunction =
        @convention(c) (UnsafePointer<CChar>?) -> UnsafeMutableRawPointer?

    private struct LaunchLinkBearerTokenProvider: HermesBearerTokenProvider {
        func bearerToken() async throws -> String {
            throw HermesTransportError.credentialUnavailable
        }
    }

    /// Retains the real production initializer and its networking adapters in the linked A/B
    /// variant. The launch anchor retains this function's address; neither function is executed.
    @inline(never)
    @_cdecl("talaria_transport_factory_link_anchor")
    func talariaTransportFactoryLinkAnchor(
        _ baseURLCString: UnsafePointer<CChar>?
    ) -> UnsafeMutableRawPointer? {
        guard
            let baseURLCString,
            let baseURL = URL(string: String(cString: baseURLCString)),
            let configuration = try? HermesWebSocketConfiguration(baseURL: baseURL)
        else {
            return nil
        }
        let transport = AuditedLaunchResourceFactory.webSocketTransport(
            configuration: configuration,
            tokenProvider: LaunchLinkBearerTokenProvider()
        )
        return Unmanaged.passRetained(transport).toOpaque()
    }
#endif

private enum LaunchLinkControlMarker {}

/// A linker-retained, never-executed reference that makes the A/B linkage contrast observable.
/// The linked variant retains the factory thunk above; the control retains only a local marker.
@_cdecl("talaria_launch_link_anchor")
func talariaLaunchLinkAnchor() -> UnsafeRawPointer {
    #if TALARIA_LINK_TRANSPORT
        unsafeBitCast(
            talariaTransportFactoryLinkAnchor as TransportFactoryLinkFunction,
            to: UnsafeRawPointer.self
        )
    #else
        unsafeBitCast(LaunchLinkControlMarker.self, to: UnsafeRawPointer.self)
    #endif
}
