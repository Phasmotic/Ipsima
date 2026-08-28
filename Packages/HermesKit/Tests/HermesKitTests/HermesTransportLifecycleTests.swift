import Foundation
#if canImport(FoundationNetworking)
    import FoundationNetworking
#endif
@testable import HermesKit
import XCTest

private enum HandshakeInput: Sendable {
    case envelope(JSONRPCEnvelope)
    case text(String)
    case binary(Data)
}

private actor CancellableTestSuspension {
    private var continuation: CheckedContinuation<Void, Error>?
    private var isCancelled = false

    func wait() async throws {
        try await withTaskCancellationHandler {
            try Task.checkCancellation()
            try await withCheckedThrowingContinuation { (continuation: CheckedContinuation<Void, Error>) in
                if self.isCancelled {
                    continuation.resume(throwing: CancellationError())
                } else {
                    self.continuation = continuation
                }
            }
        } onCancel: {
            Task {
                await self.cancel()
            }
        }
    }

    private func cancel() {
        self.isCancelled = true
        self.continuation?.resume(throwing: CancellationError())
        self.continuation = nil
    }
}

@MainActor
final class HermesTransportLifecycleTests: XCTestCase {
    func testHandshakeRejectsAnythingExceptWrappedReadyEvent() async throws {
        try await self.assertInvalidReady(
            .envelope(.notification(method: "gateway.ready", params: .object([:])))
        )
        try await self.assertInvalidReady(
            .envelope(
                JSONRPCEnvelope(
                    jsonrpc: "1.0",
                    method: "event",
                    params: self.readyParameters()
                )
            )
        )
        try await self.assertInvalidReady(
            .envelope(
                .notification(
                    method: "event",
                    params: .object(["type": .string("gateway.ready")])
                )
            )
        )
        try await self.assertInvalidReady(
            .envelope(
                .notification(
                    method: "event",
                    params: .object([
                        "type": .string("gateway.ready"),
                        "session_id": .string("live-session"),
                        "payload": .object([:]),
                    ])
                )
            )
        )
        try await self.assertInvalidReady(
            .envelope(
                .notification(
                    method: "event",
                    params: .object([
                        "type": .string("message.delta"),
                        "payload": .object([:]),
                    ])
                )
            )
        )
        try await self.assertInvalidReady(.text("{"))
        try await self.assertInvalidReady(.binary(Data([0x01])))
    }

    func testReadinessTimeoutClosesSocket() async throws {
        let gateway = MockGateway()
        await gateway.enqueueTicketReply(.issued(ticket: "timeout-ticket", ttlSeconds: 30))
        let transport = try gateway.makeTransport(
            configuration: self.configuration(),
            tokenProvider: self.provider(),
            sleep: { _ in }
        )

        let error = await self.caughtError {
            _ = try await transport.connect()
        }
        XCTAssertEqual(error, .readinessTimedOut)
        let state = await transport.state
        let snapshot = await gateway.snapshot()
        XCTAssertEqual(state, .disconnected)
        XCTAssertEqual(snapshot.closeCount, 1)
    }

    func testCancellationDuringHandshakeClosesSocket() async throws {
        let gateway = MockGateway()
        let readinessDeadline = CancellableTestSuspension()
        await gateway.enqueueTicketReply(.issued(ticket: "cancel-ticket", ttlSeconds: 30))
        let transport = try gateway.makeTransport(
            configuration: self.configuration(),
            tokenProvider: self.provider(),
            sleep: { _ in
                try await readinessDeadline.wait()
            }
        )
        let task = Task {
            try await transport.connect()
        }

        try await gateway.waitForPendingReceiveForTesting()
        let waitingCount = await gateway.waitingReceiveCountForTesting()
        XCTAssertEqual(waitingCount, 1)
        task.cancel()
        do {
            _ = try await task.value
            XCTFail("cancelled handshake unexpectedly completed")
        } catch is CancellationError {
            // Expected: cancellation is preserved after deterministic socket teardown.
        } catch {
            XCTFail("cancelled handshake returned an unexpected error: \(error)")
        }
        let state = await transport.state
        let snapshot = await gateway.snapshot()
        XCTAssertEqual(state, .disconnected)
        XCTAssertEqual(snapshot.closeCount, 1)
    }

    func testReceiveIsSingleConsumerAndInvalidFramesDisconnect() async throws {
        let gateway = MockGateway()
        await gateway.enqueueTicketReply(.issued(ticket: "receive-ticket", ttlSeconds: 30))
        try await gateway.enqueueReady()
        let transport = try gateway.makeTransport(
            configuration: self.configuration(),
            tokenProvider: self.provider()
        )
        _ = try await transport.connect()

        let firstReceive = Task {
            try await transport.receive()
        }
        try await gateway.waitForPendingReceiveForTesting()
        let duplicateError = await self.caughtError {
            _ = try await transport.receive()
        }
        XCTAssertEqual(duplicateError, .receiveAlreadyPending)

        let response = JSONRPCEnvelope(id: .int(7), result: .object(["ok": .bool(true)]))
        try await gateway.enqueueEnvelope(response)
        let firstEnvelope = try await firstReceive.value
        XCTAssertEqual(firstEnvelope, response)

        await gateway.enqueueBinary(Data([0x7B, 0x7D]))
        let binaryError = await self.caughtError {
            _ = try await transport.receive()
        }
        XCTAssertEqual(binaryError, .invalidFrame)
        let state = await transport.state
        XCTAssertEqual(state, .disconnected)
    }

    func testSendAndReceiveRequireReadyValidEnvelopes() async throws {
        let gateway = MockGateway()
        let transport = try gateway.makeTransport(
            configuration: self.configuration(),
            tokenProvider: self.provider()
        )
        let request = JSONRPCEnvelope.request(id: 4, method: "ping")

        let sendBeforeReady = await self.caughtError { try await transport.send(request) }
        XCTAssertEqual(sendBeforeReady, .notReady)
        let receiveBeforeReady = await self.caughtError { _ = try await transport.receive() }
        XCTAssertEqual(receiveBeforeReady, .notReady)

        await gateway.enqueueTicketReply(.issued(ticket: "ready-ticket", ttlSeconds: 30))
        try await gateway.enqueueReady()
        _ = try await transport.connect()
        let invalid = JSONRPCEnvelope(jsonrpc: "1.0", id: .int(4), method: "ping")
        let invalidSend = await self.caughtError { try await transport.send(invalid) }
        XCTAssertEqual(invalidSend, .invalidFrame)
        let readyState = await transport.state
        XCTAssertEqual(readyState, .ready)

        await gateway.enqueueDisconnect()
        let failedReceive = await self.caughtError { _ = try await transport.receive() }
        XCTAssertEqual(failedReceive, .receiveFailed)
        let disconnectedState = await transport.state
        XCTAssertEqual(disconnectedState, .disconnected)
    }

    func testErrorsNeverExposeCredentialMaterial() {
        let protectedMaterial = ["private", "material"].joined(separator: "-")
        let errors: [HermesTransportError] = [
            .invalidConfiguration,
            .alreadyConnected,
            .notReady,
            .receiveAlreadyPending,
            .credentialUnavailable,
            .reauthenticationRequired,
            .ticketRequestFailed(statusCode: nil),
            .ticketRequestFailed(statusCode: 500),
            .invalidTicketResponse,
            .webSocketUnavailable,
            .connectionFailed,
            .readinessTimedOut,
            .invalidReadyEvent,
            .invalidFrame,
            .sendFailed,
            .receiveFailed,
        ]

        for error in errors {
            XCTAssertFalse(error.description.contains(protectedMaterial))
            XCTAssertFalse(error.localizedDescription.contains(protectedMaterial))
            XCTAssertFalse(error.description.contains("ticket="))
            XCTAssertEqual(error.errorDescription, error.description)
        }
    }

    func testLinuxURLSessionWebSocketAvailabilityFailsExplicitly() async throws {
        #if os(Linux)
            let connector = URLSessionWebSocketConnector(session: .shared)
            let url = try XCTUnwrap(URL(string: "wss://gateway.example.com/api/ws"))
            do {
                _ = try await connector.connect(to: url)
                XCTFail("the pinned Linux libcurl unexpectedly opened a WebSocket")
            } catch let error as HermesTransportError {
                XCTAssertEqual(error, .webSocketUnavailable)
            } catch {
                XCTFail("unexpected Linux WebSocket error: \(error)")
            }
        #endif
    }

    func testMockGatewayRejectsUnissuedTicketAndReportsSafeSnapshot() async throws {
        let gateway = MockGateway()
        let url = try XCTUnwrap(URL(string: "wss://gateway.example.com/api/ws?ticket=unknown"))
        do {
            _ = try await gateway.connect(to: url)
            XCTFail("an unissued ticket unexpectedly opened a mock connection")
        } catch {
            // The mock deliberately exposes no credential-bearing diagnostic.
        }

        let snapshot = await gateway.snapshot()
        XCTAssertEqual(snapshot.ticketRequestCount, 0)
        XCTAssertEqual(snapshot.connectionAttemptCount, 1)
        XCTAssertEqual(snapshot.closeCount, 0)
        XCTAssertTrue(snapshot.sentEnvelopes.isEmpty)
    }

    private func configuration() throws -> HermesWebSocketConfiguration {
        let url = try XCTUnwrap(URL(string: "https://gateway.example.com"))
        return try HermesWebSocketConfiguration(baseURL: url)
    }

    private func provider() -> StubBearerProvider {
        StubBearerProvider(result: .value("unit-material"))
    }

    private func readyParameters() -> JSONValue {
        .object([
            "type": .string("gateway.ready"),
            "payload": .object([:]),
        ])
    }

    private func assertInvalidReady(_ input: HandshakeInput) async throws {
        let gateway = MockGateway()
        await gateway.enqueueTicketReply(.issued(ticket: "invalid-ready", ttlSeconds: 30))
        switch input {
        case let .envelope(envelope):
            try await gateway.enqueueEnvelope(envelope)
        case let .text(text):
            await gateway.enqueueText(text)
        case let .binary(data):
            await gateway.enqueueBinary(data)
        }
        let transport = try gateway.makeTransport(
            configuration: self.configuration(),
            tokenProvider: self.provider()
        )
        let error = await self.caughtError {
            _ = try await transport.connect()
        }
        XCTAssertEqual(error, .invalidReadyEvent)
        let state = await transport.state
        let snapshot = await gateway.snapshot()
        XCTAssertEqual(state, .disconnected)
        XCTAssertEqual(snapshot.closeCount, 1)
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
