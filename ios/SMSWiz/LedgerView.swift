//
//  LedgerView.swift
//  SMS Wiz — reference implementation
//
//  NOT COMPILED OR TESTED IN THIS REPOSITORY.
//
//  One screen, one job: show the month the server computed, and never invent a
//  number of its own. Every figure here is a word the API used
//  (`spend`, `income`, `refunds`, `excluded`), because the whole point of the
//  ledger is that "excluded" is visible rather than quietly dropped.
//

import SwiftUI
import SMSCore

struct LedgerView: View {
    @EnvironmentObject private var store: AppStore

    var body: some View {
        NavigationStack {
            List {
                if let month = store.month?.month {
                    Section("This month · \(month.label)") {
                        figure("Spent", month.spend, tint: .primary)
                        figure("Income", month.income, tint: .green)
                        figure("Refunds", month.refunds, tint: .blue)
                        figure("Excluded", month.excluded, tint: .secondary)
                        if month.reviewCount > 0 {
                            Label("\(month.reviewCount) need a look", systemImage: "exclamationmark.triangle")
                                .foregroundStyle(.orange)
                        }
                    }

                    Section("Where it went") {
                        let rows = month.byCategory.sorted { $0.value > $1.value }
                        if rows.isEmpty {
                            Text("Nothing counted yet this month.").foregroundStyle(.secondary)
                        }
                        ForEach(rows, id: \.key) { row in
                            HStack {
                                Text(row.key)
                                Spacer()
                                Text(row.value, format: .currency(code: "INR").precision(.fractionLength(0)))
                                    .monospacedDigit()
                            }
                        }
                    }

                    if let top = store.month?.topMerchants, !top.isEmpty {
                        Section("Top merchants") {
                            ForEach(top) { merchant in
                                HStack {
                                    Text(merchant.merchant)
                                    Spacer()
                                    Text(merchant.count, format: .number)
                                        .foregroundStyle(.secondary)
                                    Text(merchant.spend, format: .currency(code: "INR").precision(.fractionLength(0)))
                                        .monospacedDigit()
                                }
                            }
                        }
                    }
                } else if store.busy {
                    ProgressView("Loading the month…")
                } else {
                    ContentUnavailableView(
                        "No ledger yet",
                        systemImage: "tray",
                        description: Text("Import a message from the Import tab, or set the server address in Settings.")
                    )
                }

                if let error = store.lastError {
                    Section {
                        Label(error, systemImage: "wifi.exclamationmark")
                            .foregroundStyle(.orange)
                    }
                }
            }
            .navigationTitle("SMS Wiz")
            .refreshable { await store.refresh() }
            .toolbar {
                Button {
                    Task { await store.refresh() }
                } label: {
                    Image(systemName: "arrow.clockwise")
                }
            }
        }
    }

    private func figure(_ title: String, _ value: Double, tint: Color) -> some View {
        HStack {
            Text(title)
            Spacer()
            Text(value, format: .currency(code: "INR").precision(.fractionLength(0)))
                .monospacedDigit()
                .foregroundStyle(tint)
        }
    }
}
