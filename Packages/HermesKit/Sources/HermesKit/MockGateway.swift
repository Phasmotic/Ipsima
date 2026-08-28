import Foundation
#if canImport(FoundationNetworking)
    import FoundationNetworking
#endif

private struct MockGatewayTicketBody: Encodable {
    let ticket: String
    let ttlSeconds: Int

    private enum CodingKeys: String, CodingKey {
        case ticket
        case ttlSeconds = "ttl_seconds"
    }
}

/// Reusable, deterministic gateway driver for HermesKit state machines and Linux tests.
///
/// It sits below `WebSocketHermesTransport`: production ticket parsing, URL construction,
/// readiness validation, canonical framing, and lifecycle logic all still execute. P1.8 will
/// expand its fixture vocabulary; P1.2 intentionally provides only the transport primitives.
public actor MockGateway: HermesHTTPDataLoading, HermesWebSocketConnecting {
    public enum TicketReply: Sendable, Equatable {
        case issued(ticket: String, ttlSeconds: Int)
        case rejected(statusCode: Int)
        case malformed(Data)
        case transportFailure
    }

    public struct Snapshot: Sendable, Equatable {
        public let ticketRequestCount: Int
        public let connectionAttemptCount: Int
        public let closeCount: Int
        public let sentEnvelopes: [JSONRPCEnvelope]
    }

    private enum Inbound: Sendable {
        case frame(HermesWebSocketFrame)
        case failure
    }

    struct RequestRecord: Sendable {
        let method: String?
        let path: String
        let bodyIsNil: Bool
        let accept: String?
        let authorization: String?
    }

    private struct WaitingReceive {
        let connectionID: UInt64
        let continuation: CheckedContinuation<HermesWebSocketFrame, Error>
    }

    private enum MockFailure: Error {
        case transport
    }

    private let codec = WireCodec.shared
    private var ticketReplies: [TicketReply] = []
    private var inbound: [Inbound] = []
    private var waitingReceives: [WaitingReceive] = []
    private var issuedTickets: Set<String> = []
    private var requestRecords: [RequestRecord] = []
    private var dialURLs: [URL] = []
    private var sentTexts: [String] = []
    private var closedConnections: Set<UInt64> = []
    private var nextConnectionID: UInt64 = 1
    private var closeCount = 0

    public init() {}

    public nonisolated func makeTransport(
        configuration: HermesWebSocketConfiguration,
        tokenProvider: any HermesBearerTokenProvider
    ) -> WebSocketHermesTransport {
        self.makeTransport(
            configuration: configuration,
            tokenProvider: tokenProvider,
            sleep: hermesContinuousSleep
        )
    }

    nonisolated func makeTransport(
        configuration: HermesWebSocketConfiguration,
        tokenProvider: any HermesBearerTokenProvider,
        sleep: @escaping HermesSleep
    ) -> WebSocketHermesTransport {
        WebSocketHermesTransport(
            configuration: configuration,
            ticketAcquirer: URLSessionWebSocketTicketAcquirer(
                baseURL: configuration.baseURL,
                tokenProvider: tokenProvider,
                loader: self
            ),
            connector: self,
            sleep: sleep
        )
    }

    public func enqueueTicketReply(_ reply: TicketReply) {
        self.ticketReplies.append(reply)
    }

    public func enqueueReady(payload: JSONValue = .object([:])) throws {
        try self.enqueueEnvelope(
            .notification(
                method: "event",
                params: .object([
                    "type": .string("gateway.ready"),
                    "payload": payload,
                ])
            )
        )
    }

    public func enqueueEnvelope(_ envelope: JSONRPCEnvelope) throws {
        let data = try self.codec.encode(envelope)
        guard let text = String(data: data, encoding: .utf8) else {
            throw HermesTransportError.invalidFrame
        }
        self.enqueue(.frame(.text(text)))
    }

    public func enqueueText(_ text: String) {
        self.enqueue(.frame(.text(text)))
    }

    public func enqueueBinary(_ data: Data) {
        self.enqueue(.frame(.data(data)))
    }

    public func enqueueDisconnect() {
        self.enqueue(.failure)
    }

    public func snapshot() -> Snapshot {
        let envelopes = self.sentTexts.compactMap { text -> JSONRPCEnvelope? in
            guard let data = text.data(using: .utf8) else { return nil }
            return try? self.codec.decode(data)
        }
        return Snapshot(
            ticketRequestCount: self.requestRecords.count,
            connectionAttemptCount: self.dialURLs.count,
            closeCount: self.closeCount,
            sentEnvelopes: envelopes
        )
    }

    func data(for request: URLRequest) async throws -> HermesHTTPResponse {
        self.requestRecords.append(
            RequestRecord(
                method: request.httpMethod,
                path: request.url?.path ?? "",
                bodyIsNil: request.httpBody == nil,
                accept: request.value(forHTTPHeaderField: "Accept"),
                authorization: request.value(forHTTPHeaderField: "Authorization")
            )
        )

        guard !self.ticketReplies.isEmpty else {
            throw MockFailure.transport
        }
        switch self.ticketReplies.removeFirst() {
        case let .issued(ticket, ttlSeconds):
            self.issuedTickets.insert(ticket)
            let body = try JSONEncoder().encode(
                MockGatewayTicketBody(ticket: ticket, ttlSeconds: ttlSeconds)
            )
            return HermesHTTPResponse(data: body, statusCode: 200)
        case let .rejected(statusCode):
            return HermesHTTPResponse(data: Data(), statusCode: statusCode)
        case let .malformed(data):
            return HermesHTTPResponse(data: data, statusCode: 200)
        case .transportFailure:
            throw MockFailure.transport
        }
    }

    func connect(to url: URL) async throws -> any HermesWebSocketConnection {
        self.dialURLs.append(url)
        guard
            let components = URLComponents(url: url, resolvingAgainstBaseURL: false),
            let queryItems = components.queryItems,
            queryItems.count == 1,
            queryItems[0].name == "ticket",
            let ticket = queryItems[0].value,
            self.issuedTickets.remove(ticket) != nil
        else {
            throw MockFailure.transport
        }

        let connectionID = self.nextConnectionID
        self.nextConnectionID &+= 1
        return MockGatewayConnection(gateway: self, connectionID: connectionID)
    }

    private func enqueue(_ item: Inbound) {
        guard !self.waitingReceives.isEmpty else {
            self.inbound.append(item)
            return
        }
        let waiting = self.waitingReceives.removeFirst()
        self.resume(waiting.continuation, with: item)
    }

    fileprivate func send(text: String, connectionID: UInt64) throws {
        guard !self.closedConnections.contains(connectionID) else {
            throw MockFailure.transport
        }
        self.sentTexts.append(text)
    }

    fileprivate func receive(connectionID: UInt64) async throws -> HermesWebSocketFrame {
        guard !self.closedConnections.contains(connectionID) else {
            throw MockFailure.transport
        }
        if !self.inbound.isEmpty {
            let item = self.inbound.removeFirst()
            return try Self.resolve(item)
        }
        return try await withCheckedThrowingContinuation { continuation in
            self.waitingReceives.append(
                WaitingReceive(connectionID: connectionID, continuation: continuation)
            )
        }
    }

    fileprivate func close(connectionID: UInt64) {
        guard self.closedConnections.insert(connectionID).inserted else { return }
        self.closeCount += 1

        var retained: [WaitingReceive] = []
        for waiting in self.waitingReceives {
            if waiting.connectionID == connectionID {
                waiting.continuation.resume(throwing: MockFailure.transport)
            } else {
                retained.append(waiting)
            }
        }
        self.waitingReceives = retained
    }

    func requestRecordsForTesting() -> [RequestRecord] {
        self.requestRecords
    }

    func dialURLsForTesting() -> [URL] {
        self.dialURLs
    }

    func sentTextsForTesting() -> [String] {
        self.sentTexts
    }

    func waitingReceiveCountForTesting() -> Int {
        self.waitingReceives.count
    }

    private func resume(
        _ continuation: CheckedContinuation<HermesWebSocketFrame, Error>,
        with item: Inbound
    ) {
        switch item {
        case let .frame(frame):
            continuation.resume(returning: frame)
        case .failure:
            continuation.resume(throwing: MockFailure.transport)
        }
    }

    private static func resolve(_ item: Inbound) throws -> HermesWebSocketFrame {
        switch item {
        case let .frame(frame):
            return frame
        case .failure:
            throw MockFailure.transport
        }
    }
}

private struct MockGatewayConnection: HermesWebSocketConnection {
    let gateway: MockGateway
    let connectionID: UInt64

    func send(text: String) async throws {
        try await self.gateway.send(text: text, connectionID: self.connectionID)
    }

    func receive() async throws -> HermesWebSocketFrame {
        try await self.gateway.receive(connectionID: self.connectionID)
    }

    func close() async {
        await self.gateway.close(connectionID: self.connectionID)
    }
}
