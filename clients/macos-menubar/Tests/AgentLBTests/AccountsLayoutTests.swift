import AppKit
import SwiftUI
import XCTest

@testable import AgentLB

/// Layout regression tests for the v1.2 "no account rows" bug.
///
/// Root cause was window-level: macOS 26's MenuBarExtra panel never
/// re-measures after its first layout, so any height-flexible list absorbed
/// the stale-size deficit and collapsed. Sizing is now deterministic
/// (PanelLayout computes; the list renders at an exact `.frame(height:)`).
/// These tests lock in the unit-testable layer: given the PanelLayout list
/// height, the section must render at exactly header (22) + spacing (6) +
/// listHeight — never less — in every provider scope with default filters.
final class AccountsLayoutTests: XCTestCase {

  private func listHeight(rows: Int) -> Double {
    Double(min(rows, PanelMetrics.maxListRows)) * PanelMetrics.accountRow
      + PanelMetrics.listPadding
  }

  /// Header row (22) + VStack spacing (6) + list frame.
  private func expectedHeight(rows: Int) -> Double {
    PanelMetrics.accountsHeader + PanelMetrics.accountsSpacing + listHeight(rows: rows)
  }

  @MainActor
  private func sectionHeight(accounts: [Account], isScoped: Bool) -> Double {
    let section = AccountsSection(
      accounts: accounts,
      isScoped: isScoped,
      listHeight: listHeight(rows: accounts.count),
      status: .constant(.all),
      query: .constant(""),
      searchVisible: .constant(false),
      hasError: false,
      retry: {}
    )
    .environment(AppState())
    .frame(width: 460)
    let controller = NSHostingController(rootView: AnyView(section))
    return controller.sizeThatFits(in: NSSize(width: 460, height: 10000)).height
  }

  @MainActor
  func testListClaimsFullHeightInEveryScope() throws {
    let all = try loadAccountsFixture()
    for scope in ProviderScope.allCases {
      let scoped = scope.filter(all)
      XCTAssertFalse(scoped.isEmpty, "fixture must cover scope \(scope)")
      let height = sectionHeight(accounts: scoped, isScoped: scope != .all)
      XCTAssertEqual(
        height,
        expectedHeight(rows: scoped.count),
        accuracy: 2,
        "accounts list must claim its exact height (scope \(scope), \(scoped.count) rows)"
      )
    }
  }

  @MainActor
  func testListCapsAtEightRows() throws {
    let base = try loadAccountsFixture()
    let nine = base + [makeTestAccount(id: "extra", displayName: "extra@example.com")]
    XCTAssertEqual(nine.count, 9)
    let height = sectionHeight(accounts: nine, isScoped: false)
    XCTAssertEqual(height, expectedHeight(rows: 8), accuracy: 2, "list caps at 8 rows (§9.1)")
  }

  @MainActor
  func testRowHeightIs52() throws {
    let account = try XCTUnwrap(loadAccountsFixture().first)
    let row = AccountRow(account: account, now: .now)
      .environment(AppState())
      .frame(width: 460)
    let controller = NSHostingController(rootView: AnyView(row))
    let height = controller.sizeThatFits(in: NSSize(width: 460, height: 10000)).height
    XCTAssertEqual(height, PanelMetrics.accountRow, accuracy: 0.5)
  }
}
