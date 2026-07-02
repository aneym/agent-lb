import Foundation

// MARK: - Errors

enum APIError: Error, @unchecked Sendable {
  case serviceDown
  case timeout
  case httpStatus(Int, body: String?)
  case decoding(DecodingError, endpoint: String)
  case transport(URLError)
}

// MARK: - Client

struct APIClient: @unchecked Sendable {
  let base: URL
  let isRemote: Bool
  private let session: URLSession
  private let probeSession: URLSession

  init() {
    let raw = UserDefaults.standard.string(forKey: "baseURL") ?? "http://127.0.0.1:2455"
    let resolved = URL(string: raw) ?? URL(string: "http://127.0.0.1:2455")!
    self.base = resolved
    let host = resolved.host ?? "127.0.0.1"
    self.isRemote = !["127.0.0.1", "localhost", "::1"].contains(host)

    let config = URLSessionConfiguration.ephemeral
    config.timeoutIntervalForRequest = 3
    config.timeoutIntervalForResource = 5
    config.waitsForConnectivity = false
    self.session = URLSession(configuration: config)

    // Probe/subscription-check POSTs round-trip the upstream vendor (token
    // refresh + completion probe + usage refresh) — far slower than the 3 s
    // dashboard reads, so they get their own timeout envelope.
    let probeConfig = URLSessionConfiguration.ephemeral
    probeConfig.timeoutIntervalForRequest = 30
    probeConfig.timeoutIntervalForResource = 45
    probeConfig.waitsForConnectivity = false
    self.probeSession = URLSession(configuration: probeConfig)
  }

  // MARK: - Decoder (also used in tests)

  static func makeDecoder() -> JSONDecoder {
    let decoder = JSONDecoder()
    decoder.dateDecodingStrategy = .custom { d in
      let container = try d.singleValueContainer()
      let s = try container.decode(String.self)
      if let date = Format.iso8601Frac.date(from: s) { return date }
      if let date = Format.iso8601.date(from: s) { return date }
      throw DecodingError.dataCorruptedError(
        in: container,
        debugDescription: "Cannot parse date: \(s)"
      )
    }
    return decoder
  }

  // MARK: - Health

  func health() async throws {
    let (_, response) = try await fetch(path: "/health")
    try assertOK(response, endpoint: "/health")
  }

  func ready() async throws -> Bool {
    do {
      let (_, response) = try await fetch(path: "/health/ready")
      guard let http = response as? HTTPURLResponse else { return false }
      return http.statusCode == 200
    } catch APIError.httpStatus(503, _) {
      return false
    }
  }

  func startupComplete() async throws -> Bool {
    do {
      let (_, response) = try await fetch(path: "/health/startup")
      guard let http = response as? HTTPURLResponse else { return false }
      return http.statusCode == 200
    } catch APIError.httpStatus(503, _) {
      return false
    }
  }

  // MARK: - API endpoints

  func accounts() async throws -> AccountsResponse {
    try await get("/api/accounts")
  }

  func usageSummary() async throws -> UsageSummary {
    try await get("/api/usage/summary")
  }

  func projections() async throws -> ProjectionsResponse {
    try await get("/api/dashboard/projections")
  }

  func requestLogs(limit: Int) async throws -> RequestLogsResponse {
    try await get("/api/request-logs?limit=\(limit)")
  }

  func runtimeVersion() async throws -> RuntimeVersion {
    try await get("/api/runtime/version")
  }

  func pauseAccount(_ id: String) async throws {
    try await post("/api/accounts/\(id)/pause")
  }

  func reactivateAccount(_ id: String) async throws {
    try await post("/api/accounts/\(id)/reactivate")
  }

  func probeAccount(_ id: String) async throws -> AccountProbeResponse {
    try await postDecoding("/api/accounts/\(id)/probe")
  }

  func checkSubscription(_ id: String) async throws -> AccountSubscriptionCheckResponse {
    try await postDecoding("/api/accounts/\(id)/subscription/check")
  }

  // MARK: - Generic helpers

  private func get<T: Decodable>(_ path: String) async throws -> T {
    let (data, response) = try await fetch(path: path)
    try assertOK(response, endpoint: path)
    do {
      return try Self.makeDecoder().decode(T.self, from: data)
    } catch let e as DecodingError {
      throw APIError.decoding(e, endpoint: path)
    }
  }

  private func post(_ path: String) async throws {
    let url = URL(string: path, relativeTo: base) ?? base.appendingPathComponent(path)
    var request = URLRequest(url: url)
    request.httpMethod = "POST"
    let (_, response) = try await executeRequest(request, on: session)
    try assertOK(response, endpoint: path)
  }

  /// POST that decodes its response body — on the probe session, because
  /// both callers block on an upstream vendor round-trip.
  private func postDecoding<T: Decodable>(_ path: String) async throws -> T {
    let url = URL(string: path, relativeTo: base) ?? base.appendingPathComponent(path)
    var request = URLRequest(url: url)
    request.httpMethod = "POST"
    let (data, response) = try await executeRequest(request, on: probeSession)
    try assertOK(response, endpoint: path)
    do {
      return try Self.makeDecoder().decode(T.self, from: data)
    } catch let e as DecodingError {
      throw APIError.decoding(e, endpoint: path)
    }
  }

  private func fetch(path: String) async throws -> (Data, URLResponse) {
    let url = URL(string: path, relativeTo: base) ?? base.appendingPathComponent(path)
    let request = URLRequest(url: url)
    return try await executeRequest(request, on: session)
  }

  private func executeRequest(
    _ request: URLRequest,
    on session: URLSession
  ) async throws -> (Data, URLResponse) {
    do {
      return try await session.data(for: request)
    } catch let urlError as URLError {
      switch urlError.code {
      case .cannotConnectToHost, .networkConnectionLost, .cannotFindHost:
        throw APIError.serviceDown
      case .timedOut:
        throw APIError.timeout
      default:
        throw APIError.transport(urlError)
      }
    }
  }

  private func assertOK(_ response: URLResponse, endpoint: String) throws {
    guard let http = response as? HTTPURLResponse else { return }
    guard (200..<300).contains(http.statusCode) else {
      throw APIError.httpStatus(http.statusCode, body: nil)
    }
  }
}
