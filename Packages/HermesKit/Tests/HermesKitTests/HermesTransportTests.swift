import Foundation
#if canImport(FoundationNetworking)
    import FoundationNetworking
#endif
@testable import HermesKit
import XCTest

enum StubProviderFailure: Error {
    case unavailable
}

struct StubBearerProvider: HermesBearerTokenProvider {
    enum Result: Sendable {
        case value(String)
        case failure
    }

    let result: Result

    func bearerToken() async throws -> String {
        switch self.result {
        case let .value(value):
            return value
        case .failure:
            throw StubProviderFailure.unavailable
        }
    }
}

@MainActor
final class HermesTransportTests: XCTestCase {
    func testConfigurationRequiresSecureOrLoopbackRootURL() throws {
        let secure = try self.configuration()
        XCTAssertEqual(secure.baseURL.scheme, "https")
        XCTAssertEqual(secure.readinessTimeout, .seconds(10))

        let loopback = try HermesWebSocketConfiguration(
            baseURL: self.url(scheme: "http", host: "localhost")
        )
        XCTAssertEqual(loopback.baseURL.host, "localhost")

        XCTAssertThrowsError(
            try HermesWebSocketConfiguration(
                baseURL: self.url(scheme: "http", host: "gateway.example.com")
            )
        )
        XCTAssertThrowsError(
            try HermesWebSocketConfiguration(
                baseURL: self.url(scheme: "https", host: "gateway.example.com", path: "/nested")
            )
        )
        XCTAssertThrowsError(
            try HermesWebSocketConfiguration(
                baseURL: self.url(scheme: "https", host: "gateway.example.com", query: "mode=1")
            )
        )
        XCTAssertThrowsError(
            try HermesWebSocketConfiguration(
                baseURL: self.url(scheme: "https", host: "gateway.example.com"),
                readinessTimeout: .zero
            )
        )
    }

    func testGoldenHandshakeTicketRequestAndBidirectionalFraming() async throws {
        let gateway = MockGateway()
        let fixture = try self.goldenEnvelopes()
        let unusualTicket = ["ticket", "value with/+?"].joined(separator: "-")
        await gateway.enqueueTicketReply(.issued(ticket: unusualTicket, ttlSeconds: 30))
        try await gateway.enqueueEnvelope(fixture[0])

        let transport = try gateway.makeTransport(
            configuration: self.configuration(),
            tokenProvider: self.provider()
        )
        let ready = try await transport.connect()
        guard case .object = ready.payload else {
            return XCTFail("golden gateway.ready payload must be an object")
        }
        XCTAssertEqual(ready.parameters["type"], .string("gateway.ready"))
        let connectedState = await transport.state
        XCTAssertEqual(connectedState, .ready)

        let requests = await gateway.requestRecordsForTesting()
        XCTAssertEqual(requests.count, 1)
        XCTAssertEqual(requests[0].method, "POST")
        XCTAssertEqual(requests[0].path, "/api/auth/ws-ticket")
        XCTAssertTrue(requests[0].bodyIsNil)
        XCTAssertEqual(requests[0].accept, "application/json")
        XCTAssertEqual(requests[0].authorization, "Bearer " + "unit-material")

        let dialURLs = await gateway.dialURLsForTesting()
        let dialURL = try XCTUnwrap(dialURLs.first)
        XCTAssertEqual(dialURL.scheme, "wss")
        XCTAssertEqual(dialURL.path, "/api/ws")
        let queryItems = try XCTUnwrap(
            URLComponents(url: dialURL, resolvingAgainstBaseURL: false)?.queryItems
        )
        XCTAssertEqual(queryItems, [URLQueryItem(name: "ticket", value: unusualTicket)])

        try await transport.send(fixture[1])
        let sentTexts = await gateway.sentTextsForTesting()
        XCTAssertEqual(sentTexts.count, 1)
        XCTAssertFalse(sentTexts[0].contains("\n"))
        XCTAssertEqual(try WireCodec.shared.decodeLine(sentTexts[0]), fixture[1])

        try await gateway.enqueueEnvelope(fixture[2])
        let received = try await transport.receive()
        XCTAssertEqual(received, fixture[2])

        await transport.disconnect()
        await transport.disconnect()
        let snapshot = await gateway.snapshot()
        XCTAssertEqual(snapshot.ticketRequestCount, 1)
        XCTAssertEqual(snapshot.connectionAttemptCount, 1)
        XCTAssertEqual(snapshot.closeCount, 1)
        XCTAssertEqual(snapshot.sentEnvelopes, [fixture[1]])
    }

    func testTicketFailureClassificationIsBoundedAndFresh() async throws {
        try await self.assertConnectError(
            reply: .rejected(statusCode: 401),
            expected: .reauthenticationRequired
        )
        try await self.assertConnectError(
            reply: .rejected(statusCode: 403),
            expected: .reauthenticationRequired
        )
        try await self.assertConnectError(
            reply: .rejected(statusCode: 503),
            expected: .ticketRequestFailed(statusCode: 503)
        )
        try await self.assertConnectError(
            reply: .malformed(Data("{}".utf8)),
            expected: .invalidTicketResponse
        )
        try await self.assertConnectError(
            reply: .issued(ticket: "zero-ttl", ttlSeconds: 0),
            expected: .invalidTicketResponse
        )
        try await self.assertConnectError(
            reply: .transportFailure,
            expected: .ticketRequestFailed(statusCode: nil)
        )
        try await self.assertConnectError(
            reply: nil,
            provider: StubBearerProvider(result: .failure),
            expected: .credentialUnavailable
        )
        try await self.assertConnectError(
            reply: nil,
            provider: StubBearerProvider(result: .value("")),
            expected: .credentialUnavailable
        )
        try await self.assertConnectError(
            reply: nil,
            provider: StubBearerProvider(result: .value(" invalid ")),
            expected: .credentialUnavailable
        )
    }

    func testEveryConnectionAttemptMintsAndConsumesFreshTicket() async throws {
        let gateway = MockGateway()
        await gateway.enqueueTicketReply(.issued(ticket: "dial-one", ttlSeconds: 30))
        await gateway.enqueueTicketReply(.issued(ticket: "dial-two", ttlSeconds: 30))
        await gateway.enqueueDisconnect()
        try await gateway.enqueueReady(payload: .object(["attempt": .int(2)]))
        let transport = try gateway.makeTransport(
            configuration: self.configuration(),
            tokenProvider: self.provider()
        )

        let firstError = await self.caughtError {
            _ = try await transport.connect()
        }
        XCTAssertEqual(firstError, .connectionFailed)
        let disconnectedState = await transport.state
        XCTAssertEqual(disconnectedState, .disconnected)

        let ready = try await transport.connect()
        XCTAssertEqual(ready.payload["attempt"], .int(2))
        let dialURLs = await gateway.dialURLsForTesting()
        XCTAssertEqual(dialURLs.count, 2)
        let values = dialURLs.compactMap {
            URLComponents(url: $0, resolvingAgainstBaseURL: false)?
                .queryItems?.first(where: { $0.name == "ticket" })?.value
        }
        XCTAssertEqual(values, ["dial-one", "dial-two"])
        let snapshot = await gateway.snapshot()
        XCTAssertEqual(snapshot.ticketRequestCount, 2)
        await transport.disconnect()
    }

    private func configuration() throws -> HermesWebSocketConfiguration {
        let url = try XCTUnwrap(URL(string: "https://gateway.example.com"))
        return try HermesWebSocketConfiguration(baseURL: url)
    }

    private func url(
        scheme: String,
        host: String,
        path: String = "",
        query: String? = nil
    ) throws -> URL {
        var components = URLComponents()
        components.scheme = scheme
        components.host = host
        components.path = path
        components.query = query
        return try XCTUnwrap(components.url)
    }

    private func provider() -> StubBearerProvider {
        StubBearerProvider(result: .value("unit-material"))
    }

    private func goldenEnvelopes() throws -> [JSONRPCEnvelope] {
        let fixtureURL = try XCTUnwrap(
            Bundle.module.url(forResource: "golden", withExtension: "jsonl")
        )
        let text = try String(contentsOf: fixtureURL, encoding: .utf8)
        return try text.split(separator: "\n").map {
            try WireCodec.shared.decodeLine(String($0))
        }
    }

    private func assertConnectError(
        reply: MockGateway.TicketReply?,
        provider: StubBearerProvider? = nil,
        expected: HermesTransportError
    ) async throws {
        let gateway = MockGateway()
        if let reply {
            await gateway.enqueueTicketReply(reply)
        }
        let transport = try gateway.makeTransport(
            configuration: self.configuration(),
            tokenProvider: provider ?? self.provider()
        )
        let error = await self.caughtError {
            _ = try await transport.connect()
        }
        XCTAssertEqual(error, expected)
        let state = await transport.state
        XCTAssertEqual(state, .disconnected)
    }

    private func caughtError(
        _ operation: () async throws -> Void
    ) async -> HermesTransportError? {
        do {
            try await operation()
            XCTFail("operation unexpectedly succeeded")
            return nil
        } catch let error as HermesTransportError {
            return error
        } catch {
            XCTFail("operation returned an unexpected error: \(error)")
            return nil
        }
    }
}
