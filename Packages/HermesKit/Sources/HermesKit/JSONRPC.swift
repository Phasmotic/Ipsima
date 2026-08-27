import Foundation

// JSON-RPC 2.0 envelope types for the tui_gateway WebSocket protocol.
//
// Framing contract (docs/PROTOCOL.md): one JSON-RPC object per WebSocket text
// message in both directions; only stdio and JSONL fixtures are newline-delimited.
// Envelopes preserve *unknown* members through decode/encode so golden-frame
// round-trips stay byte-identical even when the gateway carries fields this
// catalog predates.

public enum JSONRPCID: Sendable, Equatable, Hashable {
    case null
    case int(Int64)
    case string(String)
}

extension JSONRPCID: Codable {
    public init(from decoder: Decoder) throws {
        let container = try decoder.singleValueContainer()
        if container.decodeNil() {
            self = .null
        } else if let value = try? container.decode(Int64.self) {
            self = .int(value)
        } else if let value = try? container.decode(String.self) {
            self = .string(value)
        } else {
            throw DecodingError.dataCorruptedError(
                in: container,
                debugDescription: "JSON-RPC id must be null, a string, or an integer"
            )
        }
    }

    public func encode(to encoder: Encoder) throws {
        var container = encoder.singleValueContainer()
        switch self {
        case .null:
            try container.encodeNil()
        case let .int(value):
            try container.encode(value)
        case let .string(value):
            try container.encode(value)
        }
    }
}

/// A coding key over any string — used to enumerate members the typed
/// schema does not know about.
private struct DynamicKey: CodingKey {
    let stringValue: String
    let intValue: Int?

    init?(stringValue: String) {
        self.stringValue = stringValue
        self.intValue = nil
    }

    init?(intValue: Int) {
        self.stringValue = "\(intValue)"
        self.intValue = intValue
    }
}

public struct JSONRPCError: Sendable, Equatable {
    public let code: Int64
    public let message: String
    public let data: JSONValue?
    /// Unknown members inside the error object, preserved verbatim.
    public let additionalMembers: [String: JSONValue]

    public init(code: Int64, message: String, data: JSONValue? = nil,
                additionalMembers: [String: JSONValue] = [:]) {
        self.code = code
        self.message = message
        self.data = data
        self.additionalMembers = additionalMembers
    }

    private enum KnownKeys: String, CodingKey, CaseIterable {
        case code, message, data
    }
}

extension JSONRPCError: Codable {
    public init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: KnownKeys.self)
        self.code = try container.decode(Int64.self, forKey: .code)
        self.message = try container.decode(String.self, forKey: .message)
        self.data = container.contains(.data)
            ? try container.decode(JSONValue.self, forKey: .data)
            : nil

        var extras: [String: JSONValue] = [:]
        let dynamic = try decoder.container(keyedBy: DynamicKey.self)
        for key in dynamic.allKeys
            where !KnownKeys.allCases.contains(where: { $0.rawValue == key.stringValue }) {
            extras[key.stringValue] = try dynamic.decode(JSONValue.self, forKey: key)
        }
        self.additionalMembers = extras
    }

    public func encode(to encoder: Encoder) throws {
        var container = encoder.container(keyedBy: KnownKeys.self)
        try container.encode(self.code, forKey: .code)
        try container.encode(self.message, forKey: .message)
        try container.encodeIfPresent(self.data, forKey: .data)

        if !self.additionalMembers.isEmpty {
            var dynamic = encoder.container(keyedBy: DynamicKey.self)
            for (key, value) in self.additionalMembers.sorted(by: { $0.key < $1.key }) {
                try dynamic.encode(value, forKey: DynamicKey(stringValue: key)!)
            }
        }
    }
}

/// One wire envelope. A request carries `method` (+ optional `params`) and an
/// `id`; an event/notification is the same shape without an `id`; a response
/// carries exactly one of `result` / `error`.
public struct JSONRPCEnvelope: Sendable, Equatable {
    public var jsonrpc: String
    public var id: JSONRPCID?
    public var method: String?
    public var params: JSONValue?
    public var result: JSONValue?
    public var error: JSONRPCError?
    /// Unknown top-level members, preserved verbatim across round-trips.
    public var additionalMembers: [String: JSONValue]

    public init(jsonrpc: String = "2.0",
                id: JSONRPCID? = nil,
                method: String? = nil,
                params: JSONValue? = nil,
                result: JSONValue? = nil,
                error: JSONRPCError? = nil,
                additionalMembers: [String: JSONValue] = [:]) {
        self.jsonrpc = jsonrpc
        self.id = id
        self.method = method
        self.params = params
        self.result = result
        self.error = error
        self.additionalMembers = additionalMembers
    }

    private enum KnownKeys: String, CodingKey, CaseIterable {
        case jsonrpc
        case id
        case method
        case params
        case result
        case error
    }

    public var isRequest: Bool {
        self.method != nil && self.id != nil
    }

    public var isNotification: Bool {
        self.method != nil && self.id == nil
    }

    public var isSuccess: Bool {
        self.method == nil && self.error == nil && self.result != nil
    }

    public var isError: Bool {
        self.method == nil && self.error != nil
    }
}

extension JSONRPCEnvelope: Codable {
    public init(from decoder: Decoder) throws {
        // Presence-based decoding: JSON-RPC allows explicit nulls where a
        // member is semantically present (notably `"result": null` on success
        // responses that carry no payload). decodeIfPresent would erase them
        // and break byte-identical re-encoding.
        let container = try decoder.container(keyedBy: KnownKeys.self)
        self.jsonrpc = try container.decode(String.self, forKey: .jsonrpc)

        if container.contains(.id) {
            self.id = try container.decode(JSONRPCID.self, forKey: .id)
        } else {
            self.id = nil
        }

        self.method = container.contains(.method)
            ? try container.decode(String.self, forKey: .method)
            : nil
        self.params = container.contains(.params)
            ? try container.decode(JSONValue.self, forKey: .params)
            : nil
        self.result = container.contains(.result)
            ? try container.decode(JSONValue.self, forKey: .result)
            : nil
        self.error = container.contains(.error)
            ? try container.decode(JSONRPCError.self, forKey: .error)
            : nil

        var extras: [String: JSONValue] = [:]
        let dynamic = try decoder.container(keyedBy: DynamicKey.self)
        for key in dynamic.allKeys
            where !KnownKeys.allCases.contains(where: { $0.rawValue == key.stringValue }) {
            extras[key.stringValue] = try dynamic.decode(JSONValue.self, forKey: key)
        }
        self.additionalMembers = extras
    }

    public func encode(to encoder: Encoder) throws {
        var container = encoder.container(keyedBy: KnownKeys.self)
        try container.encode(self.jsonrpc, forKey: .jsonrpc)
        try container.encodeIfPresent(self.id, forKey: .id)
        try container.encodeIfPresent(self.method, forKey: .method)
        try container.encodeIfPresent(self.params, forKey: .params)
        try container.encodeIfPresent(self.result, forKey: .result)
        try container.encodeIfPresent(self.error, forKey: .error)

        if !self.additionalMembers.isEmpty {
            var dynamic = encoder.container(keyedBy: DynamicKey.self)
            for (key, value) in self.additionalMembers.sorted(by: { $0.key < $1.key }) {
                try dynamic.encode(value, forKey: DynamicKey(stringValue: key)!)
            }
        }
    }
}

public extension JSONRPCEnvelope {
    /// A client request with a monotonically increasing integer id.
    static func request(id: Int64, method: String, params: JSONValue? = nil) -> JSONRPCEnvelope {
        JSONRPCEnvelope(id: .int(id), method: method, params: params)
    }

    /// A server-pushed event or a client notification (no id).
    static func notification(method: String, params: JSONValue? = nil) -> JSONRPCEnvelope {
        JSONRPCEnvelope(method: method, params: params)
    }
}
