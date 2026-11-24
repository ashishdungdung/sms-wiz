// swift-tools-version: 5.9
// SMSCore — platform-neutral models shared by the app and the filter extension.
//
// This package deliberately has **no** dependency on UIKit, SwiftUI, or
// UserNotifications, so it builds on Linux and macOS. That is what lets the
// harness run real Swift tests in CI without Xcode or a Mac
// (`make swift-test`). Only the app target imports the OS frameworks.

import PackageDescription

let package = Package(
    name: "SMSCore",
    platforms: [
        .iOS(.v17),
        .macOS(.v14),
    ],
    products: [
        .library(name: "SMSCore", targets: ["SMSCore"]),
    ],
    targets: [
        .target(
            name: "SMSCore",
            path: "Sources/SMSCore"
        ),
        .testTarget(
            name: "SMSCoreTests",
            dependencies: ["SMSCore"],
            path: "Tests/SMSCoreTests"
        ),
    ]
)
