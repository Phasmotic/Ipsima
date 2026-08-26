import XCTest
@testable import HermesKit

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

        let reEncoded = String(data: try codec.encode(envelope), encoding: .utf8)
        XCTAssertEqual(
            reEncoded,
            #"{"future_field":{"a":1},"jsonrpc":"2.0","method":"message.delta","params":{"text":"hi"}}"#
        )
    }

    func testCanonicalFormIsSortedCompactAndStable() throws {
        let messy = #"{"params":{"zebra":1,"alpha":[true,null,"x"]},"jsonrpc":"2.0","id":7,"method":"session.list"}"#
        let envelope = try codec.decodeLine(messy)
        let first = try codec.encode(envelope)
        let second = try codec.encode(try codec.decode(first))

        XCTAssertEqual(first, second, "decode→encode must reach a fixed point")
        let text = String(data: first, encoding: .utf8)!
        XCTAssertTrue(text.hasPrefix(#"{"id":7,"jsonrpc":"2.0""#), "keys must sort: \(text)")
        XCTAssertFalse(text.contains(" "), "no insignificant whitespace: \(text)")
    }

    func testIntegerStaysIntegerAcrossRoundTrip() throws {
        let envelope = try codec.decodeLine(#"{"jsonrpc":"2.0","method":"gateway.ready","params":{"replay_epoch":3}}"#)
        XCTAssertEqual(envelope.params?["replay_epoch"], .int(3))
        let text = String(data: try codec.encode(envelope), encoding: .utf8)!
        XCTAssertTrue(text.contains(#""replay_epoch":3"#), "int must not widen: \(text)")
    }

    func testErrorObjectWithUnknownMemberRoundTrips() throws {
        let line = #"{"jsonrpc":"2.0","id":12,"error":{"code":-32601,"message":"no such method","trace_id":"abc"}}"#
        let envelope = try codec.decodeLine(line)

        XCTAssertTrue(envelope.isError)
        XCTAssertEqual(envelope.error?.code, -32601)
        XCTAssertEqual(envelope.error?.additionalMembers["trace_id"], .string("abc"))

        let text = String(data: try codec.encode(envelope), encoding: .utf8)!
        XCTAssertTrue(text.contains(#""trace_id":"abc""#))
    }

    func testIDVariantsRoundTrip() throws {
        let intID = try codec.decodeLine(#"{"jsonrpc":"2.0","id":42,"method":"ping"}"#)
        XCTAssertEqual(intID.id, .int(42))

        let stringID = try codec.decodeLine(#"{"jsonrpc":"2.0","id":"req-9f","method":"ping"}"#)
        XCTAssertEqual(stringID.id, .string("req-9f"))
    }

    // MARK: - Framing

    func testSplitFramesHandlesBatchTrailingNewlineAndBlanks() throws {
        let a = try codec.frame(.request(id: 1, method: "session.list"))
        let b = try codec.frame(.notification(method: "prompt.submit"))
        var stream = Data()
        stream.append(a)
        stream.append(b)
        stream.append(Data("\n".utf8)) // stray blank line

        let frames = codec.splitFrames(stream)
        XCTAssertEqual(frames.count, 2)
        XCTAssertEqual(try codec.decode(frames[0]).method, "session.list")
        XCTAssertEqual(try codec.decode(frames[1]).method, "prompt.submit")
    }

    // MARK: - Fixtures

    /// Every golden fixture must decode; canonical re-encodes must be stable.
    /// Zero fixture files means zero iterations until captures land (P0 wires
    /// them up later in-phase).
    func testAllGoldenFixturesDecodeAndReEncodeIdentically() throws {
        let fixtureURL = Bundle.module.url(forResource: "golden", withExtension: "jsonl")
        guard let fixtureURL else {
            return  // captures not yet present
        }
        let text = try String(contentsOf: fixtureURL, encoding: .utf8)
        for line in text.split(separator: "\n") where !line.isEmpty {
            let envelope = try codec.decodeLine(String(line))
            let canonical = try codec.encode(envelope)
            let again = try codec.encode(try codec.decode(canonical))
            XCTAssertEqual(canonical, again, "unstable canonical form for: \(line)")
        }
    }
}
