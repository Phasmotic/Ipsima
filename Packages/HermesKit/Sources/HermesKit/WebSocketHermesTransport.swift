import Foundation
#if canImport(FoundationNetworking)
    import FoundationNetworking
#endif

public actor WebSocketHermesTransport: HermesTransport {
    public private(set) var state: HermesTransportState = .disconnected

    private enum ReadyRace: Sendable {
        case frame(HermesWebSocketFrame)
        case deadline
    }

    private let configuration: HermesWebSocketConfiguration
    private let ticketAcquirer: any HermesWebSocketTicketAcquiring
    private let connector: any HermesWebSocketConnecting
    private let codec: WireCodec
    private let sleep: HermesSleep

    private var connection: (any HermesWebSocketConnection)?
    private var generation: UInt64 = 0
    private var pendingReceiveGeneration: UInt64?
    private var pendingSendGeneration: UInt64?

    public init(
        configuration: HermesWebSocketConfiguration,
        tokenProvider: any HermesBearerTokenProvider
    ) {
        let session = URLSession(configuration: .ephemeral)
        self.configuration = configuration
        self.ticketAcquirer = URLSessionWebSocketTicketAcquirer(
            baseURL: configuration.baseURL,
            tokenProvider: tokenProvider,
            loader: URLSessionHTTPDataLoader(session: session)
        )
        self.connector = URLSessionWebSocketConnector(session: session)
        self.codec = .shared
        self.sleep = hermesContinuousSleep
    }

    init(
        configuration: HermesWebSocketConfiguration,
        ticketAcquirer: any HermesWebSocketTicketAcquiring,
        connector: any HermesWebSocketConnecting,
        codec: WireCodec = .shared,
        sleep: @escaping HermesSleep = hermesContinuousSleep
    ) {
        self.configuration = configuration
        self.ticketAcquirer = ticketAcquirer
        self.connector = connector
        self.codec = codec
        self.sleep = sleep
    }

    @discardableResult
    public func connect() async throws -> HermesGatewayReady {
        let connectionGeneration = try self.beginConnectionAttempt()
        let ticket = try await self.acquireTicket(generation: connectionGeneration)
        let connection = try await self.openConnection(
            ticket: ticket,
            generation: connectionGeneration
        )
        return try await self.awaitReady(
            connection: connection,
            generation: connectionGeneration
        )
    }
}

public extension WebSocketHermesTransport {
    func send(_ envelope: JSONRPCEnvelope) async throws {
        try Task.checkCancellation()
        guard
            self.state == .ready,
            let connection = self.connection
        else {
            throw HermesTransportError.notReady
        }
        guard self.pendingSendGeneration == nil else {
            throw HermesTransportError.sendFailed
        }
        let text = try self.encodedText(envelope)

        let connectionGeneration = self.generation
        self.pendingSendGeneration = connectionGeneration
        do {
            try await withTaskCancellationHandler {
                try Task.checkCancellation()
                try await connection.send(text: text)
                try Task.checkCancellation()
            } onCancel: {
                Task {
                    await connection.close()
                }
            }
            if self.pendingSendGeneration == connectionGeneration {
                self.pendingSendGeneration = nil
            }
            guard self.generation == connectionGeneration, self.state == .ready else {
                throw HermesTransportError.notReady
            }
        } catch {
            if self.pendingSendGeneration == connectionGeneration {
                self.pendingSendGeneration = nil
            }
            if self.generation == connectionGeneration {
                await self.closeIfCurrent(connection, generation: connectionGeneration)
            }
            if Task.isCancelled {
                throw CancellationError()
            }
            throw Self.normalized(error, fallback: .sendFailed)
        }
    }

    func receive() async throws -> JSONRPCEnvelope {
        guard
            self.state == .ready,
            let connection = self.connection
        else {
            throw HermesTransportError.notReady
        }
        guard self.pendingReceiveGeneration == nil else {
            throw HermesTransportError.receiveAlreadyPending
        }

        let connectionGeneration = self.generation
        self.pendingReceiveGeneration = connectionGeneration
        do {
            let frame = try await withTaskCancellationHandler {
                let frame = try await connection.receive()
                try Task.checkCancellation()
                return frame
            } onCancel: {
                Task {
                    await connection.close()
                }
            }
            if self.pendingReceiveGeneration == connectionGeneration {
                self.pendingReceiveGeneration = nil
            }
            guard self.generation == connectionGeneration, self.state == .ready else {
                throw HermesTransportError.notReady
            }
            return try self.decodeEnvelope(frame)
        } catch {
            if self.pendingReceiveGeneration == connectionGeneration {
                self.pendingReceiveGeneration = nil
            }
            if self.generation == connectionGeneration {
                await self.closeIfCurrent(connection, generation: connectionGeneration)
            }
            if Task.isCancelled {
                throw CancellationError()
            }
            throw Self.normalized(error, fallback: .receiveFailed)
        }
    }

    func disconnect() async {
        self.generation &+= 1
        let connection = self.connection
        self.connection = nil
        self.pendingReceiveGeneration = nil
        self.pendingSendGeneration = nil
        self.state = .disconnected
        await connection?.close()
    }
}

private extension WebSocketHermesTransport {
    func beginConnectionAttempt() throws -> UInt64 {
        try Task.checkCancellation()
        guard self.state == .disconnected else {
            throw HermesTransportError.alreadyConnected
        }

        self.generation &+= 1
        self.state = .acquiringTicket
        return self.generation
    }

    func acquireTicket(generation: UInt64) async throws -> HermesWebSocketTicket {
        do {
            let ticket = try await self.ticketAcquirer.acquireTicket()
            try Task.checkCancellation()
            guard self.isCurrent(generation, state: .acquiringTicket) else {
                throw HermesTransportError.connectionFailed
            }
            return ticket
        } catch {
            self.disconnectAttemptIfCurrent(generation: generation)
            if Task.isCancelled {
                throw CancellationError()
            }
            throw Self.normalized(error, fallback: .ticketRequestFailed(statusCode: nil))
        }
    }

    func openConnection(
        ticket: HermesWebSocketTicket,
        generation: UInt64
    ) async throws -> any HermesWebSocketConnection {
        guard self.isCurrent(generation, state: .acquiringTicket) else {
            throw HermesTransportError.connectionFailed
        }
        let url = try self.makeWebSocketURL(ticket: ticket.value, generation: generation)
        self.state = .connecting

        let connection: any HermesWebSocketConnection
        do {
            try Task.checkCancellation()
            connection = try await self.connector.connect(to: url)
        } catch {
            self.disconnectAttemptIfCurrent(generation: generation)
            if Task.isCancelled {
                throw CancellationError()
            }
            throw Self.normalized(error, fallback: .connectionFailed)
        }

        if Task.isCancelled {
            await connection.close()
            self.disconnectAttemptIfCurrent(generation: generation)
            throw CancellationError()
        }
        guard self.isCurrent(generation, state: .connecting) else {
            await connection.close()
            throw HermesTransportError.connectionFailed
        }
        return connection
    }

    func awaitReady(
        connection: any HermesWebSocketConnection,
        generation: UInt64
    ) async throws -> HermesGatewayReady {
        self.connection = connection
        self.state = .awaitingReady

        do {
            let frame = try await self.firstFrame(from: connection)
            guard
                self.isCurrent(generation, state: .awaitingReady),
                self.connection != nil
            else {
                throw HermesTransportError.connectionFailed
            }
            let ready = try self.decodeReady(frame)
            try Task.checkCancellation()
            self.state = .ready
            return ready
        } catch {
            await self.closeIfCurrent(connection, generation: generation)
            if Task.isCancelled {
                throw CancellationError()
            }
            throw Self.normalized(error, fallback: .connectionFailed)
        }
    }

    func makeWebSocketURL(ticket: String, generation: UInt64) throws -> URL {
        do {
            return try self.webSocketURL(ticket: ticket)
        } catch {
            self.disconnectAttemptIfCurrent(generation: generation)
            throw Self.normalized(error, fallback: .invalidConfiguration)
        }
    }

    func isCurrent(_ generation: UInt64, state: HermesTransportState) -> Bool {
        self.generation == generation && self.state == state
    }

    func encodedText(_ envelope: JSONRPCEnvelope) throws -> String {
        guard Self.isValidEnvelope(envelope) else {
            throw HermesTransportError.invalidFrame
        }
        let data: Data
        do {
            data = try self.codec.encode(envelope)
        } catch {
            throw HermesTransportError.invalidFrame
        }
        guard let text = String(data: data, encoding: .utf8), !text.contains("\n") else {
            throw HermesTransportError.invalidFrame
        }
        return text
    }

    private func firstFrame(
        from connection: any HermesWebSocketConnection
    ) async throws -> HermesWebSocketFrame {
        let sleep = self.sleep
        let timeout = self.configuration.readinessTimeout
        return try await withTaskCancellationHandler {
            try await withThrowingTaskGroup(of: ReadyRace.self) { group in
                group.addTask {
                    let frame = try await connection.receive()
                    try Task.checkCancellation()
                    return .frame(frame)
                }
                group.addTask {
                    try await sleep(timeout)
                    return .deadline
                }

                guard let first = try await group.next() else {
                    group.cancelAll()
                    throw HermesTransportError.connectionFailed
                }

                switch first {
                case let .frame(frame):
                    group.cancelAll()
                    return frame
                case .deadline:
                    await connection.close()
                    group.cancelAll()
                    throw HermesTransportError.readinessTimedOut
                }
            }
        } onCancel: {
            Task {
                await connection.close()
            }
        }
    }

    private func decodeReady(_ frame: HermesWebSocketFrame) throws -> HermesGatewayReady {
        guard case let .text(text) = frame, let data = text.data(using: .utf8) else {
            throw HermesTransportError.invalidReadyEvent
        }

        let envelope: JSONRPCEnvelope
        do {
            envelope = try self.codec.decode(data)
        } catch {
            throw HermesTransportError.invalidReadyEvent
        }

        guard
            envelope.jsonrpc == "2.0",
            envelope.id == nil,
            envelope.method == "event",
            envelope.result == nil,
            envelope.error == nil,
            case let .object(parameters)? = envelope.params,
            parameters["type"] == .string("gateway.ready"),
            parameters["session_id"] == nil,
            case let .object(payload)? = parameters["payload"]
        else {
            throw HermesTransportError.invalidReadyEvent
        }

        return HermesGatewayReady(payload: .object(payload), parameters: parameters)
    }

    private func decodeEnvelope(_ frame: HermesWebSocketFrame) throws -> JSONRPCEnvelope {
        guard case let .text(text) = frame, let data = text.data(using: .utf8) else {
            throw HermesTransportError.invalidFrame
        }
        let envelope: JSONRPCEnvelope
        do {
            envelope = try self.codec.decode(data)
        } catch {
            throw HermesTransportError.invalidFrame
        }
        guard Self.isValidEnvelope(envelope) else {
            throw HermesTransportError.invalidFrame
        }
        return envelope
    }

    private static func isValidEnvelope(_ envelope: JSONRPCEnvelope) -> Bool {
        guard envelope.jsonrpc == "2.0" else { return false }
        if envelope.method != nil {
            return envelope.result == nil && envelope.error == nil
        }
        guard envelope.id != nil else { return false }
        return (envelope.result != nil) != (envelope.error != nil)
    }

    private func webSocketURL(ticket: String) throws -> URL {
        guard
            var components = URLComponents(
                url: self.configuration.baseURL,
                resolvingAgainstBaseURL: false
            )
        else {
            throw HermesTransportError.invalidConfiguration
        }
        components.scheme = components.scheme?.lowercased() == "https" ? "wss" : "ws"
        components.path = "/api/ws"
        components.queryItems = [URLQueryItem(name: "ticket", value: ticket)]
        components.fragment = nil
        guard let url = components.url else {
            throw HermesTransportError.invalidConfiguration
        }
        return url
    }

    private func closeIfCurrent(
        _ connection: any HermesWebSocketConnection,
        generation: UInt64
    ) async {
        if self.generation == generation {
            self.generation &+= 1
            self.connection = nil
            self.pendingReceiveGeneration = nil
            self.pendingSendGeneration = nil
            self.state = .disconnected
        }
        await connection.close()
    }

    private func disconnectAttemptIfCurrent(generation: UInt64) {
        guard self.generation == generation else { return }
        self.connection = nil
        self.pendingReceiveGeneration = nil
        self.pendingSendGeneration = nil
        self.state = .disconnected
    }

    private static func normalized(
        _ error: Error,
        fallback: HermesTransportError
    ) -> Error {
        if error is CancellationError {
            return CancellationError()
        }
        return (error as? HermesTransportError) ?? fallback
    }
}
