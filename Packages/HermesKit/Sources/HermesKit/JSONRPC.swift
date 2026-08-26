// JSON-RPC 2.0 envelope types for the tui_gateway WebSocket protocol.
//
// Framing contract (docs/PROTOCOL.md): newline-delimited JSON-RPC, both
// directions. Envelopes preserve *unknown* members through decode/encode so
// golden-frame round-trips stay byte-identical even when the gateway carries
// fields this catalog predates.

import Foundation

public enum JSONRPCID: Sendable, Equatable, Hashable {
    case int(Int64)
    case string(String)
}

extension JSONRPCID: Codable {
    public init(from decoder: Decoder) throws {
        let container = try decoder.singleValueContainer()
        if let value = try? container.decode(Int64.self) {
            self = .int(value)
        } else if let value = try? container.decode(String.self) {
            self = .string(value)
        } else {
            throw DecodingError.dataCorruptedError(
                in: container,
                debugDescription: "JSON-RPC id must be a string or integer"
            )
        }
    }

    public func encode(to encoder: Encoder) throws {
        var container = encoder.singleValueContainer()
        switch self {
        case .int(let value):
            try container.encode(value)
        case .string(let value):
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

    private enum KnownKeys: String, CodingKey {
        case code, message, data
    }
}

extension JSONRPCError: Codable {
    public init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: KnownKeys.self)
        code = try container.decode(Int64.self, forKey: .code)
        message = try container.decode(String.self, forKey: .message)
        data = try container.decodeIfPresent(JSONValue.self, forKey: .data)

        var extras: [String: JSONValue] = [:]
        let dynamic = try decoder.container(keyedBy: DynamicKey.self)
        for key in dynamic.allKeys
        where !KnownKeys.allCases.contains(where: { $0.rawValue == key.stringValue }) {
            extras[key.stringValue] = try dynamic.decode(JSONValue.self, forKey: key)
        }
        additionalMembers = extras
    }

    public func encode(to encoder: Encoder) throws {
        var container = encoder.container(keyedBy: KnownKeys.self)
        try container.encode(code, forKey: .code)
        try container.encode(message, forKey: .message)
        try container.encodeIfPresent(data, forKey: .data)

        if !additionalMembers.isEmpty {
            var dynamic = encoder.container(keyedBy: DynamicKey.self)
            for (key, value) in additionalMembers.sorted(by: { $0.key < $1.key }) {
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

    private enum KnownKeys: String, CodingKey {
        case jsonrpc
        case id
        case method
        case params
        case result
        case error
    }

    public var isRequest: Bool { method != nil && id != nil }
    public var isNotification: Bool { method != nil && id == nil }
    public var isSuccess: Bool { method == nil && error == nil && result != nil }
    public var isError: Bool { method == nil && error != nil }
}

extension JSONRPCEnvelope: Codable {
    public init(from decoder: Decoder) throws {
        // Presence-based decoding: JSON-RPC allows explicit nulls where a
        // member is semantically present (notably `"result": null` on success
        // responses that carry no payload). decodeIfPresent would erase them
        // and break byte-identical re-encoding.
        let container = try decoder.container(keyedBy: KnownKeys.self)
        jsonrpc = try container.decode(String.self, forKey: .jsonrpc)

        if container.contains(.id),
           case .null = try container.decode(JSONValue.self, forKey: .id) {
            id = nil // a literal null id is tolerated and treated as absent
        } else if container.contains(.id) {
            id = try container.decode(JSONRPCID.self, forKey: .id)
        } else {
            id = nil
        }

        method = container.contains(.method)
            ? try container.decode(String.self, forKey: .method)
            : nil
        params = container.contains(.params)
            ? try container.decode(JSONValue.self, forKey: .params)
            : nil
        result = container.contains(.result)
            ? try container.decode(JSONValue.self, forKey: .result)
            : nil
        error = container.contains(.error)
            ? try container.decode(JSONRPCError.self, forKey: .error)
            : nil

        var extras: [String: JSONValue] = [:]
        let dynamic = try decoder.container(keyedBy: DynamicKey.self)
        for key in dynamic.allKeys
        where !KnownKeys.allCases.contains(where: { $0.rawValue == key.stringValue }) {
            extras[key.stringValue] = try dynamic.decode(JSONValue.self, forKey: key)
        }
        additionalMembers = extras
    }

    public func encode(to encoder: Encoder) throws {
        var container = encoder.container(keyedBy: KnownKeys.self)
        try container.encode(jsonrpc, forKey: .jsonrpc)
        try container.encodeIfPresent(id, forKey: .id)
        try container.encodeIfPresent(method, forKey: .method)
        try container.encodeIfPresent(params, forKey: .params)
        try container.encodeIfPresent(result, forKey: .result)
        try container.encodeIfPresent(error, forKey: .error)

        if !additionalMembers.isEmpty {
            var dynamic = encoder.container(keyedBy: DynamicKey.self)
            for (key, value) in additionalMembers.sorted(by: { $0.key < $1.key }) {
                try dynamic.encode(value, forKey: DynamicKey(stringValue: key)!)
            }
        }
    }
}

extension JSONRPCEnvelope {
    /// A client request with a monotonically increasing integer id.
    public static func request(id: Int64, method: String, params: JSONValue? = nil) -> JSONRPCEnvelope {
        JSONRPCEnvelope(id: .int(id), method: method, params: params)
    }

    /// A server-pushed event or a client notification (no id).
    public static func notification(method: String, params: JSONValue? = nil) -> JSONRPCEnvelope {
        JSONRPCEnvelope(method: method, params: params)
    }
}
