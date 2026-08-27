// swift-tools-version:6.0
import PackageDescription

let package = Package(
    name: "HermesKit",
    products: [
        .library(name: "HermesKit", targets: ["HermesKit"]),
    ],
    targets: [
        // Pure Swift. Zero Apple-framework imports: builds and tests on Linux
        // (native Swift 6.3.3 in WSL) and on the macOS CI runner alike.
        .target(
            name: "HermesKit",
            path: "Sources/HermesKit"
        ),
        .testTarget(
            name: "HermesKitTests",
            dependencies: ["HermesKit"],
            path: "Tests/HermesKitTests",
            resources: [
                .process("Fixtures"),
            ]
        ),
    ]
)
