import Foundation

// Line framing + canonical encoding for the tui_gateway WebSocket protocol.
//
// Canonical form: UTF-8, compact separators, lexicographically sorted object
// keys, forward slashes unescaped. Every committed fixture is stored in this
// form, which is what makes the G3 byte-identity gate well-defined.

public struct WireCodec: Sendable {
    public static let shared = WireCodec()

    private let decoder: JSONDecoder
    private let encoder: JSONEncoder

    public init() {
        self.decoder = JSONDecoder()
        self.encoder = JSONEncoder()
        self.encoder.outputFormatting = [.sortedKeys, .withoutEscapingSlashes]
    }

    /// Decode one newline-delimited frame.
    public func decodeLine(_ line: String) throws -> JSONRPCEnvelope {
        guard let data = line.data(using: .utf8) else {
            throw CodecError.invalidUTF8
        }
        return try self.decode(data)
    }

    public func decode(_ data: Data) throws -> JSONRPCEnvelope {
        do {
            return try self.decoder.decode(JSONRPCEnvelope.self, from: data)
        } catch {
            throw CodecError.underlying(message: "decode failed: \(error)", cause: error)
        }
    }

    /// Canonical encoding (sorted keys, no whitespace).
    public func encode(_ envelope: JSONRPCEnvelope) throws -> Data {
        do {
            return try self.encoder.encode(envelope)
        } catch {
            throw CodecError.underlying(message: "encode failed: \(error)", cause: error)
        }
    }

    /// Canonical encoding plus the newline terminator.
    public func frame(_ envelope: JSONRPCEnvelope) throws -> Data {
        var data = try encode(envelope)
        data.append(contentsOf: [0x0A])
        return data
    }

    /// Split a received byte stream into logical frames. Blank lines are
    /// skipped; a missing trailing newline on the final line is tolerated.
    public func splitFrames(_ payload: Data) -> [Data] {
        guard let text = String(data: payload, encoding: .utf8) else { return [] }
        return text
            .split(separator: "\n", omittingEmptySubsequences: true)
            .compactMap { $0.data(using: .utf8) }
    }
}

public enum CodecError: Error, CustomStringConvertible {
    case invalidUTF8
    case underlying(message: String, cause: Error?)

    public var description: String {
        switch self {
        case .invalidUTF8:
            "frame is not valid UTF-8"
        case let .underlying(message, _):
            message
        }
    }
}
