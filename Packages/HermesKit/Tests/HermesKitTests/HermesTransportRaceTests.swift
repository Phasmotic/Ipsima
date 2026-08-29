import Foundation
@testable import HermesKit
import XCTest

private enum AttemptSuspension: Sendable, Equatable {
    case ticket
    case connector
    case none
}

private struct RequestRegistrationWaiter {
    let expectedCount: Int
    let suspension: AttemptSuspension
    let continuation: CheckedContinuation<Void, Never>
}

private actor TransportRaceHarness: HermesWebSocketTicketAcquiring, HermesWebSocketConnecting {
    private let suspension: AttemptSuspension
    private let closeResumesSend: Bool
    private var ticketRequestCount = 0
    private var connectorRequestCount = 0
    private var ticketWaiters: [Int: CheckedContinuation<HermesWebSocketTicket, Error>] = [:]
    private var connectorWaiters: [Int: CheckedContinuation<RaceConnection, Error>] = [:]
    private var sendWaiters: [Int: CheckedContinuation<Void, Error>] = [:]
    private var requestRegistrationWaiters: [RequestRegistrationWaiter] = []
    private var sendRegistrationWaiters: [Int: [CheckedContinuation<Void, Never>]] = [:]
    private var sendCounts: [Int: Int] = [:]
    private var closedAttempts: Set<Int> = []

    init(suspension: AttemptSuspension, closeResumesSend: Bool = false) {
        self.suspension = suspension
        self.closeResumesSend = closeResumesSend
    }

    func acquireTicket() async throws -> HermesWebSocketTicket {
        let attempt = self.ticketRequestCount
        self.ticketRequestCount += 1
        guard self.suspension == .ticket else {
            return Self.ticket(for: attempt)
        }
        return try await withCheckedThrowingContinuation { continuation in
            self.ticketWaiters[attempt] = continuation
            self.resumeSatisfiedRequestRegistrationWaiters()
        }
    }

    func connect(to url: URL) async throws -> any HermesWebSocketConnection {
        let attempt = try Self.attempt(from: url)
        self.connectorRequestCount += 1
        guard self.suspension == .connector else {
            return RaceConnection(harness: self, attempt: attempt)
        }
        let connection: RaceConnection = try await withCheckedThrowingContinuation { continuation in
            self.connectorWaiters[attempt] = continuation
            self.resumeSatisfiedRequestRegistrationWaiters()
        }
        return connection
    }

    func waitUntilRequestIsSuspended(_ expectedCount: Int, at suspension: AttemptSuspension) async {
        guard self.requestCount(at: suspension) < expectedCount else { return }
        await withCheckedContinuation { continuation in
            self.requestRegistrationWaiters.append(
                RequestRegistrationWaiter(
                    expectedCount: expectedCount,
                    suspension: suspension,
                    continuation: continuation
                )
            )
        }
    }

    func resolve(_ suspension: AttemptSuspension, attempt: Int, succeeds: Bool) -> Bool {
        switch suspension {
        case .ticket:
            guard let continuation = self.ticketWaiters.removeValue(forKey: attempt) else {
                return false
            }
            if succeeds {
                continuation.resume(returning: Self.ticket(for: attempt))
            } else {
                continuation.resume(
                    throwing: HermesTransportError.ticketRequestFailed(statusCode: nil)
                )
            }
        case .connector:
            guard let continuation = self.connectorWaiters.removeValue(forKey: attempt) else {
                return false
            }
            if succeeds {
                continuation.resume(returning: RaceConnection(harness: self, attempt: attempt))
            } else {
                continuation.resume(throwing: HermesTransportError.connectionFailed)
            }
        case .none:
            return false
        }
        return true
    }

    func send(text: String, attempt: Int) async throws {
        _ = text
        let count = self.sendCounts[attempt, default: 0] + 1
        self.sendCounts[attempt] = count
        guard count == 1 else {
            throw HermesTransportError.sendFailed
        }
        try await withCheckedThrowingContinuation { continuation in
            self.sendWaiters[attempt] = continuation
            let registrationWaiters = self.sendRegistrationWaiters.removeValue(forKey: attempt) ?? []
            for registrationWaiter in registrationWaiters {
                registrationWaiter.resume()
            }
        }
    }

    func waitUntilSendIsSuspended(attempt: Int) async {
        guard self.sendWaiters[attempt] == nil else { return }
        await withCheckedContinuation { continuation in
            self.sendRegistrationWaiters[attempt, default: []].append(continuation)
        }
    }

    func resolveSend(attempt: Int, succeeds: Bool) -> Bool {
        guard let continuation = self.sendWaiters.removeValue(forKey: attempt) else {
            return false
        }
        if succeeds {
            continuation.resume()
        } else {
            continuation.resume(throwing: HermesTransportError.sendFailed)
        }
        return true
    }

    func receive(attempt: Int) -> HermesWebSocketFrame {
        let ordinal = attempt + 1
        let text = """
        {"jsonrpc":"2.0","method":"event","params":{"payload":{"attempt":\(ordinal)},"type":"gateway.ready"}}
        """
        return .text(text)
    }

    func close(attempt: Int) {
        guard self.closedAttempts.insert(attempt).inserted else {
            return
        }
        if self.closeResumesSend, let continuation = self.sendWaiters.removeValue(forKey: attempt) {
            continuation.resume(throwing: HermesTransportError.sendFailed)
        }
    }

    func sendCount(for attempt: Int) -> Int {
        self.sendCounts[attempt, default: 0]
    }

    func closeCount(for attempt: Int) -> Int {
        self.closedAttempts.contains(attempt) ? 1 : 0
    }

    private func requestCount(at suspension: AttemptSuspension) -> Int {
        switch suspension {
        case .ticket:
            self.ticketRequestCount
        case .connector:
            self.connectorRequestCount
        case .none:
            0
        }
    }

    private func resumeSatisfiedRequestRegistrationWaiters() {
        var retained: [RequestRegistrationWaiter] = []
        for waiter in self.requestRegistrationWaiters {
            if self.requestCount(at: waiter.suspension) >= waiter.expectedCount {
                waiter.continuation.resume()
            } else {
                retained.append(waiter)
            }
        }
        self.requestRegistrationWaiters = retained
    }

    private static func ticket(for attempt: Int) -> HermesWebSocketTicket {
        HermesWebSocketTicket(value: "race-\(attempt)", ttlSeconds: 30)
    }

    private static func attempt(from url: URL) throws -> Int {
        let value = URLComponents(url: url, resolvingAgainstBaseURL: false)?
            .queryItems?.first(where: { $0.name == "ticket" })?.value
        guard let value, value.hasPrefix("race-"), let attempt = Int(value.dropFirst(5)) else {
            throw HermesTransportError.connectionFailed
        }
        return attempt
    }
}

private struct RaceConnection: HermesWebSocketConnection {
    let harness: TransportRaceHarness
    let attempt: Int

    func send(text: String) async throws {
        try await self.harness.send(text: text, attempt: self.attempt)
    }

    func receive() async throws -> HermesWebSocketFrame {
        await self.harness.receive(attempt: self.attempt)
    }

    func close() async {
        await self.harness.close(attempt: self.attempt)
    }
}

@MainActor
final class HermesTransportRaceTests: XCTestCase {
    func testSupersededTicketSuccessCannotMutateReplacementAttempt() async throws {
        try await self.assertSupersededCompletion(at: .ticket, succeeds: true)
    }

    func testSupersededTicketFailureCannotMutateReplacementAttempt() async throws {
        try await self.assertSupersededCompletion(at: .ticket, succeeds: false)
    }

    func testSupersededConnectorSuccessCannotMutateReplacementAttempt() async throws {
        try await self.assertSupersededCompletion(at: .connector, succeeds: true)
    }

    func testSupersededConnectorFailureCannotMutateReplacementAttempt() async throws {
        try await self.assertSupersededCompletion(at: .connector, succeeds: false)
    }

    func testStaleSendCompletionDoesNotClearReplacementSendOwnership() async throws {
        let harness = TransportRaceHarness(suspension: .none)
        let transport = try self.transport(harness: harness)
        _ = try await transport.connect()
        let oldSend = Task {
            try await transport.send(.request(id: 1, method: "old.send"))
        }
        await harness.waitUntilSendIsSuspended(attempt: 0)

        await transport.disconnect()
        _ = try await transport.connect()
        let replacementSend = Task {
            try await transport.send(.request(id: 2, method: "replacement.send"))
        }
        await harness.waitUntilSendIsSuspended(attempt: 1)
        let oldSendResolved = await harness.resolveSend(attempt: 0, succeeds: true)
        XCTAssertTrue(oldSendResolved)
        await self.assertFailure(oldSend)

        let thirdError = await self.caughtError {
            try await transport.send(.request(id: 3, method: "must.not.write"))
        }
        XCTAssertEqual(thirdError, .sendFailed)
        let replacementSendCount = await harness.sendCount(for: 1)
        XCTAssertEqual(replacementSendCount, 1)

        let replacementSendResolved = await harness.resolveSend(attempt: 1, succeeds: true)
        XCTAssertTrue(replacementSendResolved)
        try await replacementSend.value
        let state = await transport.state
        XCTAssertEqual(state, .ready)
        await transport.disconnect()
    }

    func testPostReadyReceiveCancellationClosesAndReleasesOwnership() async throws {
        let gateway = MockGateway()
        await gateway.enqueueTicketReply(.issued(ticket: "receive-cancel", ttlSeconds: 30))
        try await gateway.enqueueReady()
        let transport = try gateway.makeTransport(
            configuration: self.configuration(),
            tokenProvider: StubBearerProvider(result: .value("unit-material"))
        )
        _ = try await transport.connect()
        let receive = Task {
            try await transport.receive()
        }
        try await gateway.waitForPendingReceiveForTesting()

        receive.cancel()
        await self.assertCancellation(receive)
        let state = await transport.state
        let snapshot = await gateway.snapshot()
        let waitingCount = await gateway.waitingReceiveCountForTesting()
        XCTAssertEqual(state, .disconnected)
        XCTAssertEqual(snapshot.closeCount, 1)
        XCTAssertEqual(waitingCount, 0)
        let nextReceiveError = await self.caughtError { _ = try await transport.receive() }
        XCTAssertEqual(nextReceiveError, .notReady)
    }

    func testHandshakeCancellationWinsAgainstQueuedReady() async throws {
        let gateway = MockGateway()
        await gateway.enqueueTicketReply(.issued(ticket: "queued-ready", ttlSeconds: 30))
        let transport = try gateway.makeTransport(
            configuration: self.configuration(),
            tokenProvider: StubBearerProvider(result: .value("unit-material"))
        )
        let connect = Task {
            try await transport.connect()
        }
        try await gateway.waitForPendingReceiveForTesting()

        connect.cancel()
        try await gateway.enqueueReady()
        await self.assertCancellation(connect)
        let state = await transport.state
        let snapshot = await gateway.snapshot()
        XCTAssertEqual(state, .disconnected)
        XCTAssertEqual(snapshot.closeCount, 1)
    }

    func testInFlightSendCancellationClosesAndPreservesCancellation() async throws {
        let harness = TransportRaceHarness(suspension: .none, closeResumesSend: true)
        let transport = try self.transport(harness: harness)
        _ = try await transport.connect()
        let send = Task {
            try await transport.send(.request(id: 4, method: "cancel.send"))
        }
        await harness.waitUntilSendIsSuspended(attempt: 0)

        send.cancel()
        await self.assertCancellation(send)
        let state = await transport.state
        let closeCount = await harness.closeCount(for: 0)
        XCTAssertEqual(state, .disconnected)
        XCTAssertEqual(closeCount, 1)
    }

    func testPreCancelledSendWritesNothingAndKeepsReady() async throws {
        let harness = TransportRaceHarness(suspension: .none, closeResumesSend: true)
        let transport = try self.transport(harness: harness)
        _ = try await transport.connect()
        let send = Task {
            while !Task.isCancelled {
                await Task.yield()
            }
            try await transport.send(.request(id: 5, method: "never.write"))
        }
        send.cancel()

        await self.assertCancellation(send)
        let sendCount = await harness.sendCount(for: 0)
        let state = await transport.state
        XCTAssertEqual(sendCount, 0)
        XCTAssertEqual(state, .ready)
        await transport.disconnect()
    }

    private func assertSupersededCompletion(
        at suspension: AttemptSuspension,
        succeeds: Bool
    ) async throws {
        let harness = TransportRaceHarness(suspension: suspension)
        let transport = try self.transport(harness: harness)
        let superseded = Task {
            try await transport.connect()
        }
        await harness.waitUntilRequestIsSuspended(1, at: suspension)
        await transport.disconnect()

        let replacement = Task {
            try await transport.connect()
        }
        await harness.waitUntilRequestIsSuspended(2, at: suspension)
        let supersededResolved = await harness.resolve(
            suspension,
            attempt: 0,
            succeeds: succeeds
        )
        XCTAssertTrue(supersededResolved)
        await self.assertFailure(superseded)
        let expectedState: HermesTransportState = suspension == .ticket ? .acquiringTicket : .connecting
        let stateAfterStaleCompletion = await transport.state
        XCTAssertEqual(stateAfterStaleCompletion, expectedState)

        let replacementResolved = await harness.resolve(
            suspension,
            attempt: 1,
            succeeds: true
        )
        XCTAssertTrue(replacementResolved)
        let ready = try await replacement.value
        XCTAssertEqual(ready.payload["attempt"], .int(2))
        let finalState = await transport.state
        let replacementCloseCount = await harness.closeCount(for: 1)
        XCTAssertEqual(finalState, .ready)
        XCTAssertEqual(replacementCloseCount, 0)
        if suspension == .connector, succeeds {
            let supersededCloseCount = await harness.closeCount(for: 0)
            XCTAssertEqual(supersededCloseCount, 1)
        }
        await transport.disconnect()
    }

    private func transport(
        harness: TransportRaceHarness
    ) throws -> WebSocketHermesTransport {
        try WebSocketHermesTransport(
            configuration: self.configuration(),
            ticketAcquirer: harness,
            connector: harness
        )
    }

    private func configuration() throws -> HermesWebSocketConfiguration {
        let url = try XCTUnwrap(URL(string: "https://gateway.example.com"))
        return try HermesWebSocketConfiguration(baseURL: url)
    }

    private func assertCancellation(_ task: Task<some Sendable, Error>) async {
        do {
            _ = try await task.value
            XCTFail("cancelled operation unexpectedly completed")
        } catch is CancellationError {
            // Expected.
        } catch {
            XCTFail("cancelled operation returned an unexpected error: \(error)")
        }
    }

    private func assertFailure(_ task: Task<some Sendable, Error>) async {
        do {
            _ = try await task.value
            XCTFail("superseded operation unexpectedly completed")
        } catch {
            // Any bounded transport failure is valid after the attempt was superseded.
        }
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
