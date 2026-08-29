import Foundation

/// The raw JSON-RPC transport boundary consumed by P1's later routing and state layers.
///
/// A transport is not connected until `connect()` has received and validated the server's
/// wrapped `gateway.ready` event. Callers get one ordered receive stream and remain responsible
/// for request/response correlation in the routing layer.
public protocol HermesTransport: Actor {
    var state: HermesTransportState { get }

    @discardableResult
    func connect() async throws -> HermesGatewayReady

    func send(_ envelope: JSONRPCEnvelope) async throws
    func receive() async throws -> JSONRPCEnvelope
    func disconnect() async
}

public enum HermesTransportState: String, Sendable, Equatable {
    case disconnected
    case acquiringTicket
    case connecting
    case awaitingReady
    case ready
}

/// The source-defined readiness event that completes Talaria's WebSocket handshake policy.
public struct HermesGatewayReady: Sendable, Equatable {
    public let payload: JSONValue
    public let parameters: [String: JSONValue]
}

/// Supplies the native-app provider credential used only to mint a single-use WebSocket ticket.
/// P2 owns durable credential storage; the transport never logs or includes this value in errors.
public protocol HermesBearerTokenProvider: Sendable {
    func bearerToken() async throws -> String
}

public struct HermesWebSocketConfiguration: Sendable, Equatable {
    public let baseURL: URL
    public let readinessTimeout: Duration

    public init(baseURL: URL, readinessTimeout: Duration = .seconds(10)) throws {
        let scheme = baseURL.scheme?.lowercased()
        let host = baseURL.host?.lowercased()
        let isSecure = scheme == "https"
        let isLoopbackHTTP = scheme == "http" && Self.isLoopback(host)
        guard
            isSecure || isLoopbackHTTP,
            host != nil,
            baseURL.user == nil,
            baseURL.password == nil,
            baseURL.query == nil,
            baseURL.fragment == nil,
            baseURL.path.isEmpty || baseURL.path == "/",
            readinessTimeout > .zero
        else {
            throw HermesTransportError.invalidConfiguration
        }

        self.baseURL = baseURL
        self.readinessTimeout = readinessTimeout
    }

    private static func isLoopback(_ host: String?) -> Bool {
        guard let host else { return false }
        return host == "localhost" || host == "127.0.0.1" || host == "::1"
    }
}

/// Deliberately bounded transport errors. Underlying URLSession errors and response bodies can
/// contain bearer credentials or ticket-bearing URLs, so they never cross this API boundary.
public enum HermesTransportError: Error, Sendable, Equatable {
    case invalidConfiguration
    case alreadyConnected
    case notReady
    case receiveAlreadyPending
    case credentialUnavailable
    case reauthenticationRequired
    case ticketRequestFailed(statusCode: Int?)
    case invalidTicketResponse
    case webSocketUnavailable
    case connectionFailed
    case readinessTimedOut
    case invalidReadyEvent
    case invalidFrame
    case sendFailed
    case receiveFailed
}

extension HermesTransportError: CustomStringConvertible {
    public var description: String {
        switch self {
        case .invalidConfiguration:
            "invalid secure gateway configuration"
        case .alreadyConnected:
            "transport is already connecting or connected"
        case .notReady:
            "transport has not completed the gateway.ready handshake"
        case .receiveAlreadyPending:
            "transport already has a pending receive"
        case .credentialUnavailable:
            "gateway credential is unavailable"
        case .reauthenticationRequired:
            "gateway authentication must be renewed"
        case let .ticketRequestFailed(statusCode):
            statusCode.map { "WebSocket ticket request failed with HTTP status \($0)" }
                ?? "WebSocket ticket request failed"
        case .invalidTicketResponse:
            "WebSocket ticket response is invalid"
        case .webSocketUnavailable:
            "WebSocket networking is unavailable on this runtime"
        case .connectionFailed:
            "WebSocket connection failed"
        case .readinessTimedOut:
            "gateway.ready was not received before the readiness deadline"
        case .invalidReadyEvent:
            "the first WebSocket message was not a valid gateway.ready event"
        case .invalidFrame:
            "the gateway sent an invalid WebSocket JSON-RPC frame"
        case .sendFailed:
            "WebSocket send failed"
        case .receiveFailed:
            "WebSocket receive failed"
        }
    }
}

extension HermesTransportError: LocalizedError {
    public var errorDescription: String? {
        self.description
    }
}
