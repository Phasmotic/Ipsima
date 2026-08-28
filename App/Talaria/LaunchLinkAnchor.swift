import HermesKit

/// A linker-retained, never-executed reference that makes the shipping transport slice
/// observable to launch-structure checks. The A/B diagnostic changes only the compile
/// condition selecting this metatype; it never constructs a transport at launch.
@_cdecl("talaria_launch_link_anchor")
func talariaLaunchLinkAnchor() -> UnsafeRawPointer {
    #if TALARIA_LINK_TRANSPORT
        unsafeBitCast(WebSocketHermesTransport.self, to: UnsafeRawPointer.self)
    #else
        unsafeBitCast(WireCodec.self, to: UnsafeRawPointer.self)
    #endif
}
