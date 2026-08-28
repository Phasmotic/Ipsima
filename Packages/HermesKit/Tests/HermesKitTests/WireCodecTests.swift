import Foundation
@testable import HermesKit
import XCTest

/// Seed codec coverage. Golden-frame conformance (G3) extends these once real
/// captures land in Tests/Fixtures/.
final class WireCodecTests: XCTestCase {
    private let codec = WireCodec()

    // MARK: - Round-trips

    func testEventWithUnknownTopLevelMembersSurvivesRoundTrip() throws {
        let line = #"{"jsonrpc":"2.0","method":"message.delta","params":{"text":"hi"},"future_field":{"a":1}}"#
        let envelope = try codec.decodeLine(line)

        XCTAssertEqual(envelope.method, "message.delta")
        XCTAssertEqual(envelope.additionalMembers["future_field"],
                       .object(["a": .int(1)]))

        let reEncoded = try String(data: codec.encode(envelope), encoding: .utf8)
        XCTAssertEqual(
            reEncoded,
            #"{"future_field":{"a":1},"jsonrpc":"2.0","method":"message.delta","params":{"text":"hi"}}"#
        )
    }

    func testCanonicalFormIsSortedCompactAndStable() throws {
        let messy = #"{"params":{"zebra":1,"alpha":[true,null,"x"]},"jsonrpc":"2.0","id":7,"method":"session.list"}"#
        let envelope = try codec.decodeLine(messy)
        let first = try codec.encode(envelope)
        let second = try codec.encode(self.codec.decode(first))

        XCTAssertEqual(first, second, "decode→encode must reach a fixed point")
        let text = try XCTUnwrap(String(data: first, encoding: .utf8))
        XCTAssertTrue(text.hasPrefix(#"{"id":7,"jsonrpc":"2.0""#), "keys must sort: \(text)")
        XCTAssertFalse(text.contains(" "), "no insignificant whitespace: \(text)")
    }

    func testIntegerStaysIntegerAcrossRoundTrip() throws {
        let envelope = try codec.decodeLine(#"{"jsonrpc":"2.0","method":"gateway.ready","params":{"replay_epoch":3}}"#)
        XCTAssertEqual(envelope.params?["replay_epoch"], .int(3))
        let text = try XCTUnwrap(try String(data: codec.encode(envelope), encoding: .utf8))
        XCTAssertTrue(text.contains(#""replay_epoch":3"#), "int must not widen: \(text)")
    }

    func testErrorObjectWithUnknownMemberRoundTrips() throws {
        let line = #"{"jsonrpc":"2.0","id":12,"error":{"code":-32601,"message":"no such method","trace_id":"abc"}}"#
        let envelope = try codec.decodeLine(line)

        XCTAssertTrue(envelope.isError)
        XCTAssertEqual(envelope.error?.code, -32601)
        XCTAssertEqual(envelope.error?.additionalMembers["trace_id"], .string("abc"))

        let text = try XCTUnwrap(try String(data: codec.encode(envelope), encoding: .utf8))
        XCTAssertTrue(text.contains(#""trace_id":"abc""#))

        let parseError = try codec.decodeLine(
            #"{"jsonrpc":"2.0","id":null,"error":{"code":-32700,"message":"Parse error","data":null}}"#
        )
        XCTAssertEqual(parseError.id, .null)
        XCTAssertEqual(parseError.error?.data, .null)
        XCTAssertEqual(
            try String(data: self.codec.encode(parseError), encoding: .utf8),
            #"{"error":{"code":-32700,"data":null,"message":"Parse error"},"id":null,"jsonrpc":"2.0"}"#
        )
    }

    func testIDVariantsRoundTrip() throws {
        let intID = try codec.decodeLine(#"{"jsonrpc":"2.0","id":42,"method":"ping"}"#)
        XCTAssertEqual(intID.id, .int(42))

        let stringID = try codec.decodeLine(#"{"jsonrpc":"2.0","id":"req-9f","method":"ping"}"#)
        XCTAssertEqual(stringID.id, .string("req-9f"))

        let nullID = try codec.decodeLine(#"{"jsonrpc":"2.0","id":null,"method":"ping"}"#)
        XCTAssertEqual(nullID.id, .null)

        let absentID = try codec.decodeLine(#"{"jsonrpc":"2.0","method":"ping"}"#)
        XCTAssertNil(absentID.id)
        XCTAssertNotEqual(nullID, absentID)
    }

    func testJSONValueDoubleAndAccessorsRoundTrip() throws {
        let value: JSONValue = .object([
            "ratio": .double(1.25),
            "state": .null,
        ])
        let encoded = try JSONEncoder().encode(value)
        let decoded = try JSONDecoder().decode(JSONValue.self, from: encoded)

        XCTAssertEqual(decoded, value)
        XCTAssertEqual(decoded["ratio"], .double(1.25))
        XCTAssertNil(JSONValue.string("not-an-object")["missing"])
        XCTAssertTrue(JSONValue.null.isNull)
        XCTAssertFalse(JSONValue.bool(false).isNull)
    }

    func testEnvelopeClassificationsAndProgrammaticErrorRoundTrip() throws {
        let request = JSONRPCEnvelope.request(id: 9, method: "session.list")
        XCTAssertTrue(request.isRequest)
        XCTAssertFalse(request.isNotification)
        XCTAssertFalse(request.isSuccess)
        XCTAssertFalse(request.isError)

        let notification = JSONRPCEnvelope.notification(method: "prompt.submit")
        XCTAssertFalse(notification.isRequest)
        XCTAssertTrue(notification.isNotification)
        XCTAssertFalse(notification.isSuccess)
        XCTAssertFalse(notification.isError)

        let success = try codec.decodeLine(#"{"id":9,"jsonrpc":"2.0","result":null}"#)
        XCTAssertFalse(success.isRequest)
        XCTAssertFalse(success.isNotification)
        XCTAssertTrue(success.isSuccess)
        XCTAssertFalse(success.isError)
        XCTAssertEqual(success.result, .null)
        XCTAssertEqual(
            try String(data: self.codec.encode(success), encoding: .utf8),
            #"{"id":9,"jsonrpc":"2.0","result":null}"#
        )

        let rpcError = JSONRPCError(
            code: -32000,
            message: "synthetic failure",
            data: .object(["retry_after": .int(2)]),
            additionalMembers: ["retryable": .bool(true)]
        )
        let failure = JSONRPCEnvelope(id: .string("request-9"), error: rpcError)
        let decodedFailure = try codec.decode(self.codec.encode(failure))
        XCTAssertEqual(decodedFailure, failure)
        XCTAssertFalse(decodedFailure.isRequest)
        XCTAssertFalse(decodedFailure.isNotification)
        XCTAssertFalse(decodedFailure.isSuccess)
        XCTAssertTrue(decodedFailure.isError)
    }

    func testCodecFailurePathsExposeCausesAndRejectInvalidInputs() throws {
        XCTAssertThrowsError(try self.codec.decode(Data(#"{"#.utf8))) { error in
            guard case let CodecError.underlying(message, cause) = error else {
                return XCTFail("malformed JSON produced an unexpected error: \(error)")
            }
            XCTAssertTrue(message.hasPrefix("decode failed:"))
            XCTAssertNotNil(cause)
            XCTAssertEqual(String(describing: error), message)
        }

        XCTAssertThrowsError(
            try self.codec.decodeLine(#"{"id":1.5,"jsonrpc":"2.0","method":"ping"}"#)
        ) { error in
            guard case let CodecError.underlying(message, cause) = error else {
                return XCTFail("invalid JSON-RPC id produced an unexpected error: \(error)")
            }
            XCTAssertTrue(message.hasPrefix("decode failed:"))
            XCTAssertNotNil(cause)
        }

        let nonFinite = JSONRPCEnvelope(
            method: "event",
            params: .object(["value": .double(.infinity)])
        )
        XCTAssertThrowsError(try self.codec.encode(nonFinite)) { error in
            guard case let CodecError.underlying(message, cause) = error else {
                return XCTFail("non-finite JSON produced an unexpected error: \(error)")
            }
            XCTAssertTrue(message.hasPrefix("encode failed:"))
            XCTAssertNotNil(cause)
        }

        XCTAssertEqual(CodecError.invalidUTF8.description, "frame is not valid UTF-8")
        XCTAssertTrue(WireCodec.shared.splitFrames(Data([0xFF, 0xFE])).isEmpty)
    }

    // MARK: - Framing

    func testSplitFramesHandlesBatchTrailingNewlineAndBlanks() throws {
        let requestFrame = try codec.frame(.request(id: 1, method: "session.list"))
        let notificationFrame = try codec.frame(.notification(method: "prompt.submit"))
        var stream = Data()
        stream.append(requestFrame)
        stream.append(notificationFrame)
        stream.append(Data("\n".utf8)) // stray blank line

        let frames = self.codec.splitFrames(stream)
        XCTAssertEqual(frames.count, 2)
        XCTAssertEqual(try self.codec.decode(frames[0]).method, "session.list")
        XCTAssertEqual(try self.codec.decode(frames[1]).method, "prompt.submit")
    }

    // MARK: - Fixtures

    /// Every golden fixture must decode; canonical re-encodes must be stable.
    func testAllGoldenFixturesDecodeAndReEncodeIdentically() throws {
        let fixtureURL = Bundle.module.url(forResource: "golden", withExtension: "jsonl")
        guard let fixtureURL else {
            XCTFail("golden.jsonl is required; zero-frame conformance is invalid")
            return
        }
        let text = try String(contentsOf: fixtureURL, encoding: .utf8)
        let lines = text.split(separator: "\n")
        XCTAssertFalse(lines.isEmpty, "golden.jsonl must contain at least one frame")
        for line in lines {
            let envelope = try codec.decodeLine(String(line))
            let canonical = try codec.encode(envelope)
            XCTAssertEqual(
                canonical,
                Data(line.utf8),
                "Swift canonical encoding differs from the committed fixture: \(line)"
            )
            let again = try codec.encode(self.codec.decode(canonical))
            XCTAssertEqual(canonical, again, "unstable canonical form for: \(line)")
        }
    }
}
