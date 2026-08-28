import Foundation
#if canImport(FoundationNetworking)
    import FoundationNetworking
#endif

private struct WebSocketTicketResponseBody: Decodable {
    let ticket: String
    let ttlSeconds: Int

    private enum CodingKeys: String, CodingKey {
        case ticket
        case ttlSeconds = "ttl_seconds"
    }
}

struct HermesHTTPResponse: Sendable {
    let data: Data
    let statusCode: Int
}

protocol HermesHTTPDataLoading: Sendable {
    func data(for request: URLRequest) async throws -> HermesHTTPResponse
}

struct URLSessionHTTPDataLoader: HermesHTTPDataLoading {
    let session: URLSession

    func data(for request: URLRequest) async throws -> HermesHTTPResponse {
        let (data, response) = try await self.session.data(for: request)
        guard let response = response as? HTTPURLResponse else {
            throw HermesTransportError.ticketRequestFailed(statusCode: nil)
        }
        return HermesHTTPResponse(data: data, statusCode: response.statusCode)
    }
}

struct HermesWebSocketTicket: Sendable, Equatable {
    let value: String
    let ttlSeconds: Int
}

protocol HermesWebSocketTicketAcquiring: Sendable {
    func acquireTicket() async throws -> HermesWebSocketTicket
}

struct URLSessionWebSocketTicketAcquirer: HermesWebSocketTicketAcquiring {
    let baseURL: URL
    let tokenProvider: any HermesBearerTokenProvider
    let loader: any HermesHTTPDataLoading

    func acquireTicket() async throws -> HermesWebSocketTicket {
        let token = try await self.authorizationMaterial()
        let request = try self.ticketRequest(authorizationMaterial: token)
        let response = try await self.ticketResponse(for: request)
        try Self.validateStatus(response.statusCode)
        return try Self.decodeTicket(response.data)
    }

    private func authorizationMaterial() async throws -> String {
        let value: String
        do {
            value = try await self.tokenProvider.bearerToken()
        } catch is CancellationError {
            throw CancellationError()
        } catch {
            throw HermesTransportError.credentialUnavailable
        }

        guard
            !value.isEmpty,
            value == value.trimmingCharacters(in: .whitespacesAndNewlines),
            !value.contains("\r"),
            !value.contains("\n")
        else {
            throw HermesTransportError.credentialUnavailable
        }
        return value
    }

    private func ticketRequest(authorizationMaterial: String) throws -> URLRequest {
        guard let ticketURL = Self.endpointURL(baseURL: self.baseURL, path: "/api/auth/ws-ticket")
        else {
            throw HermesTransportError.invalidConfiguration
        }

        var request = URLRequest(url: ticketURL)
        request.httpMethod = "POST"
        request.httpBody = nil
        request.setValue("application/json", forHTTPHeaderField: "Accept")
        request.setValue("Bearer \(authorizationMaterial)", forHTTPHeaderField: "Authorization")
        return request
    }

    private func ticketResponse(for request: URLRequest) async throws -> HermesHTTPResponse {
        do {
            return try await self.loader.data(for: request)
        } catch is CancellationError {
            throw CancellationError()
        } catch let error as HermesTransportError {
            throw error
        } catch {
            throw HermesTransportError.ticketRequestFailed(statusCode: nil)
        }
    }

    private static func validateStatus(_ statusCode: Int) throws {
        switch statusCode {
        case 200:
            return
        case 401, 403:
            throw HermesTransportError.reauthenticationRequired
        default:
            throw HermesTransportError.ticketRequestFailed(statusCode: statusCode)
        }
    }

    private static func decodeTicket(_ data: Data) throws -> HermesWebSocketTicket {
        let body: WebSocketTicketResponseBody
        do {
            body = try JSONDecoder().decode(WebSocketTicketResponseBody.self, from: data)
        } catch {
            throw HermesTransportError.invalidTicketResponse
        }

        guard
            !body.ticket.isEmpty,
            body.ticket == body.ticket.trimmingCharacters(in: .whitespacesAndNewlines),
            !body.ticket.contains("\r"),
            !body.ticket.contains("\n"),
            body.ttlSeconds > 0
        else {
            throw HermesTransportError.invalidTicketResponse
        }

        return HermesWebSocketTicket(value: body.ticket, ttlSeconds: body.ttlSeconds)
    }

    private static func endpointURL(baseURL: URL, path: String) -> URL? {
        guard var components = URLComponents(url: baseURL, resolvingAgainstBaseURL: false) else {
            return nil
        }
        components.path = path
        components.query = nil
        components.fragment = nil
        return components.url
    }
}

enum HermesWebSocketFrame: Sendable, Equatable {
    case text(String)
    case data(Data)
}

protocol HermesWebSocketConnection: Sendable {
    func send(text: String) async throws
    func receive() async throws -> HermesWebSocketFrame
    func close() async
}

protocol HermesWebSocketConnecting: Sendable {
    func connect(to url: URL) async throws -> any HermesWebSocketConnection
}

struct URLSessionWebSocketConnector: HermesWebSocketConnecting {
    let session: URLSession

    func connect(to url: URL) async throws -> any HermesWebSocketConnection {
        #if os(Linux)
            // Swift 6.3.3 FoundationNetworking exposes this API, but the pinned Ubuntu runtime's
            // libcurl has WebSocket support disabled. Linux exercises the real transport actor via
            // MockGateway; shipping Apple platforms use this URLSession adapter.
            _ = url
            throw HermesTransportError.webSocketUnavailable
        #else
            let task = self.session.webSocketTask(with: url)
            task.resume()
            return URLSessionHermesWebSocketConnection(task: task)
        #endif
    }
}

private actor URLSessionHermesWebSocketConnection: HermesWebSocketConnection {
    private let task: URLSessionWebSocketTask
    private var isClosed = false

    init(task: URLSessionWebSocketTask) {
        self.task = task
    }

    func send(text: String) async throws {
        guard !self.isClosed else {
            throw HermesTransportError.sendFailed
        }
        do {
            try await self.task.send(.string(text))
        } catch is CancellationError {
            throw CancellationError()
        } catch {
            throw HermesTransportError.sendFailed
        }
    }

    func receive() async throws -> HermesWebSocketFrame {
        guard !self.isClosed else {
            throw HermesTransportError.receiveFailed
        }
        do {
            switch try await self.task.receive() {
            case let .string(text):
                return .text(text)
            case let .data(data):
                return .data(data)
            @unknown default:
                throw HermesTransportError.receiveFailed
            }
        } catch is CancellationError {
            throw CancellationError()
        } catch let error as HermesTransportError {
            throw error
        } catch {
            throw HermesTransportError.receiveFailed
        }
    }

    func close() async {
        guard !self.isClosed else { return }
        self.isClosed = true
        self.task.cancel(with: .normalClosure, reason: nil)
    }
}

typealias HermesSleep = @Sendable (Duration) async throws -> Void

let hermesContinuousSleep: HermesSleep = { duration in
    try await Task<Never, Never>.sleep(for: duration)
}
