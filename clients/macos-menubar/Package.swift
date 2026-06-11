// swift-tools-version: 6.2
import PackageDescription

let package = Package(
  name: "AgentLB",
  platforms: [.macOS("26.0")],
  targets: [
    .executableTarget(
      name: "AgentLB",
      path: "Sources/AgentLB",
      linkerSettings: [
        .linkedFramework("ServiceManagement"),
      ]
    ),
    .testTarget(
      name: "AgentLBTests",
      dependencies: ["AgentLB"],
      path: "Tests/AgentLBTests",
      resources: [.copy("Fixtures")]
    ),
  ]
)
