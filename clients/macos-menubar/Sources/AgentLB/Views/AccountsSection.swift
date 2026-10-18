import AppKit
import SwiftUI










struct AccountsSection: View {
  @Environment(AppState.self) private var appState
  let accounts: [Account]
  var seats: [SeatAccount] = []
  let isScoped: Bool
  let listHeight: Double
  @Binding var status: AccountFilter.Status
  @Binding var query: String
  @Binding var searchVisible: Bool
  let hasError: Bool
  let retry: () -> Void
  var fixedNow: Date? = nil

  @FocusState private var searchFocused: Bool
  @AppStorage("accountSort") private var sortRaw = AccountFilter.Sort.resetSoonest.rawValue
  @AppStorage("allAccountSort") private var allSortRaw = AccountFilter.Sort.remainingAsc.rawValue

  private var sort: AccountFilter.Sort {
    if isScoped {
      return AccountFilter.Sort(rawValue: sortRaw) ?? .resetSoonest
    }
    return AccountFilter.Sort(rawValue: allSortRaw) ?? .remainingAsc
  }

  private var filter: AccountFilter {
    AccountFilter(provider: .all, status: status, query: query, sort: sort)
  }

  var body: some View {
    let now = fixedNow ?? Date.now
    let filtered = filter.apply(to: accounts, seats: seats, now: now)
    let isEmpty = accounts.isEmpty && seats.isEmpty
    VStack(alignment: .leading, spacing: PanelMetrics.accountsSpacing) {
      headerRow(filteredCount: filtered.count)
      if searchVisible {
        searchField
      }
      if hasError && isEmpty {
        RetryRow(retry: retry)
      } else if isEmpty {
        emptyState
      } else if filtered.isEmpty {
        noMatches
      } else {
        list(filtered)
        if hasError {
          RetryRow(retry: retry)
        }
      }
    }
  }

  private func headerRow(filteredCount: Int) -> some View {
    HStack(spacing: 6) {
      SectionLabel("ACCOUNTS (\(filteredCount))")
      Spacer(minLength: 6)
      Button {
        withAnimation(.easeOut(duration: 0.15)) {
          if searchVisible {
            hideSearch()
          } else {
            searchVisible = true
            searchFocused = true
          }
        }
      } label: {
        searchToggleLabel
      }
      .buttonStyle(.plain)
      .accessibilityLabel(searchVisible ? "Hide search" : "Search accounts")
      filterMenu
    }

    .frame(height: PanelMetrics.accountsHeader)
  }

  private var searchToggleLabel: some View {
    Image(systemName: "magnifyingglass")
      .font(.system(size: 11, weight: searchVisible ? .bold : .medium))
      .foregroundStyle(searchVisible ? AnyShapeStyle(.primary) : AnyShapeStyle(.secondary))
      .frame(width: 22, height: 22)
      .contentShape(.rect)
  }

  private var filterMenu: some View {
    Menu {
      Picker("Status", selection: $status) {
        Text("All").tag(AccountFilter.Status.all)
        Text("Active").tag(AccountFilter.Status.active)
        Text("Rate-limited").tag(AccountFilter.Status.rateLimited)
        Text("Paused").tag(AccountFilter.Status.paused)
        Text("Inactive").tag(AccountFilter.Status.inactive)
      }
      .pickerStyle(.inline)
      Divider()
      Picker("Sort", selection: Binding(
        get: { sort },
        set: {
          if isScoped {
            sortRaw = $0.rawValue
          } else {
            allSortRaw = $0.rawValue
          }
        }
      )) {
        Text("Weekly remaining (lowest)").tag(AccountFilter.Sort.remainingAsc)
        Text("Weekly remaining (highest)").tag(AccountFilter.Sort.remainingDesc)
        Text("Reset (soonest)").tag(AccountFilter.Sort.resetSoonest)
        Text("Reset (latest)").tag(AccountFilter.Sort.resetLatest)
        Text("Name A-Z").tag(AccountFilter.Sort.nameAsc)
        Text("Name Z-A").tag(AccountFilter.Sort.nameDesc)
      }
      .pickerStyle(.inline)
    } label: {
      Image(systemName: filter.hasActiveFilters
        ? "line.3.horizontal.decrease.circle.fill"
        : "line.3.horizontal.decrease.circle")
        .font(.system(size: 13, weight: filter.hasActiveFilters ? .bold : .regular))
        .foregroundStyle(filter.hasActiveFilters ? AnyShapeStyle(.primary) : AnyShapeStyle(.secondary))
        .frame(width: 22, height: 22)
        .contentShape(.rect)
    }
    .menuStyle(.button)
    .menuIndicator(.hidden)
    .buttonStyle(.plain)
    .fixedSize()
    .accessibilityLabel(filter.hasActiveFilters ? "Filter accounts (active)" : "Filter accounts")
  }



  private var searchField: some View {
    HStack(spacing: 6) {
      Image(systemName: "magnifyingglass")
        .font(.system(size: 10))
        .foregroundStyle(.tertiary)
      TextField("Search accounts…", text: $query)
        .textFieldStyle(.plain)
        .font(.system(size: 12))
        .focused($searchFocused)
        .onExitCommand { hideSearch() }
    }
    .padding(.horizontal, 8)
    .frame(height: 24)
    .background(.thinMaterial, in: .rect(cornerRadius: 6))
    .onChange(of: searchFocused) { _, focused in

      if !focused && query.isEmpty {
        withAnimation(.easeOut(duration: 0.15)) { searchVisible = false }
      }
    }
  }

  private func hideSearch() {
    query = ""
    searchFocused = false
    withAnimation(.easeOut(duration: 0.15)) { searchVisible = false }
  }



  @ViewBuilder
  private func list(_ filtered: [AccountListItem]) -> some View {







    TimelineView(.periodic(from: .now, by: 30)) { context in
      let rows = VStack(spacing: 0) {
        ForEach(filtered) { item in
          switch item {
          case .proxy(let account):
            AccountRow(account: account, now: fixedNow ?? context.date)
          case .seat(let seat):
            SeatAccountRow(seat: seat, now: fixedNow ?? context.date)
          }
        }
      }
      .padding(.vertical, 4)
      Group {
        if Double(filtered.count) * PanelMetrics.accountRow + PanelMetrics.listPadding <= listHeight {
          rows.frame(maxHeight: .infinity, alignment: .top)
        } else {
          ScrollView { rows }
        }
      }
      .frame(height: listHeight)
      .background(.thinMaterial, in: .rect(cornerRadius: 8))
    }
  }

  private var noMatches: some View {
    VStack(spacing: 6) {
      Text("No accounts match")
        .font(.system(size: 12))
        .foregroundStyle(.secondary)
      Button {
        withAnimation(.easeOut(duration: 0.15)) {
          status = .all
          query = ""
        }
      } label: {
        Text("Clear filters")
          .font(.system(size: 11))
          .underline()
          .foregroundStyle(.secondary)
      }
      .buttonStyle(.plain)
    }
    .frame(maxWidth: .infinity)

    .frame(height: PanelMetrics.noMatches)
    .background(.thinMaterial, in: .rect(cornerRadius: 8))
  }

  private var emptyState: some View {
    VStack(spacing: 8) {
      Image(systemName: "person.crop.circle.badge.questionmark")
        .font(.system(size: 24))
        .foregroundStyle(.secondary)
      Text(isScoped ? "No accounts for this provider" : "No accounts connected")
        .font(.system(size: 13))
      if !isScoped {
        Button {
          NSWorkspace.shared.open(appState.dashboardURL)
        } label: {
          Text("Open Dashboard to add accounts")
            .font(.system(size: 11))
            .underline()
            .foregroundStyle(.secondary)
        }
        .buttonStyle(.plain)
      }
    }
    .frame(maxWidth: .infinity)

    .frame(height: PanelMetrics.emptyState)
    .background(.thinMaterial, in: .rect(cornerRadius: 8))
  }
}
