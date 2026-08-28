@testable import HermesKit
import XCTest

/// Generated canonical request tests: decode → encode must remain stable.
final class ProtocolConformanceTests: XCTestCase {
    private let codec = WireCodec()

    func assertRoundTrip(
        _ kind: String, name: String, id: JSONRPCID?, file: StaticString = #filePath, line: UInt = #line
    ) throws {
        var envelope = JSONRPCEnvelope(method: name)
        envelope.id = id
        envelope.params = .object(["__probe": .string(name)])
        let data = try codec.encode(envelope)
        let decoded = try codec.decode(data)
        XCTAssertEqual(decoded, envelope, "\(kind) \(name) changed on decode", file: file, line: line)
        XCTAssertEqual(decoded.method, name, "\(kind) \(name) lost its name", file: file, line: line)
        XCTAssertEqual(decoded.params?["__probe"], .string(name), file: file, line: line)
        let again = try codec.decode(self.codec.encode(decoded))
        XCTAssertEqual(again, decoded, "\(kind) \(name) not a fixed point", file: file, line: line)
    }
}
