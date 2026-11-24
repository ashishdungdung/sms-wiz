//
//  MerchantReviewView.swift
//  SMS Wiz — reference implementation
//
//  NOT COMPILED OR TESTED IN THIS REPOSITORY.
//
//  The review queue is the honest half of a merchant dictionary: the app can
//  only name what it has been taught, so it shows the user exactly what it could
//  not name, ranked by how often it appears and how much money it moved.
//
//  Backed by:
//    GET  /api/v1/merchants/unresolved  → raw, seen, total_amount, suggested
//    POST /api/v1/merchants             → { raw, clean, category }
//
//  The POST renames the events already stored (`events_updated`), which is why
//  the list shrinks after a correction instead of lying about the past.
//

import SwiftUI
import SMSCore

struct MerchantReviewView: View {
    @EnvironmentObject private var store: AppStore

    @State private var editing: UnresolvedMerchant?
    @State private var cleanName = ""
    @State private var category = "Uncategorized"

    private let categories = [
        "Food & Dining", "Groceries & Quick Commerce", "Transport & Fuel", "Shopping",
        "Bills & Utilities", "Mobile & Broadband", "Entertainment & Subscriptions",
        "Health", "Education", "Travel & Commute", "Investments", "Insurance",
        "Loans & EMI", "Taxes & Fines", "Bank Fees & Charges", "Cash & ATM", "Uncategorized",
    ]

    var body: some View {
        NavigationStack {
            List {
                if store.unresolved.isEmpty {
                    ContentUnavailableView(
                        "Nothing to name",
                        systemImage: "checkmark.seal",
                        description: Text("Every merchant in the ledger has a name.")
                    )
                }
                ForEach(store.unresolved) { merchant in
                    Button {
                        begin(merchant)
                    } label: {
                        VStack(alignment: .leading, spacing: 4) {
                            HStack {
                                Text(merchant.raw).font(.headline)
                                Spacer()
                                Text("×\(merchant.seen)").font(.footnote).foregroundStyle(.secondary)
                            }
                            HStack(spacing: 12) {
                                if let amount = merchant.totalAmount {
                                    Text(amount, format: .currency(code: "INR").precision(.fractionLength(0)))
                                        .font(.footnote.monospacedDigit())
                                }
                                if let suggestion = merchant.suggested {
                                    Text("looks like \(suggestion.clean)")
                                        .font(.footnote)
                                        .foregroundStyle(.secondary)
                                }
                            }
                        }
                    }
                    .buttonStyle(.plain)
                }
            }
            .navigationTitle("Name a merchant")
            .refreshable { await store.loadMerchants() }
            .sheet(item: $editing) { merchant in
                NavigationStack {
                    Form {
                        Section("As it appears") {
                            Text(merchant.raw).font(.system(.body, design: .monospaced))
                            if merchant.seen > 1 {
                                Text("Seen \(merchant.seen) times — one correction renames all of them.")
                                    .font(.footnote)
                                    .foregroundStyle(.secondary)
                            }
                        }
                        Section("Call it") {
                            TextField("Merchant name", text: $cleanName)
                                .textInputAutocapitalization(.words)
                            Picker("Category", selection: $category) {
                                ForEach(categories, id: \.self) { Text($0).tag($0) }
                            }
                        }
                        Section {
                            Button("Save") {
                                Task {
                                    _ = await store.rename(
                                        raw: merchant.raw,
                                        clean: cleanName,
                                        category: category
                                    )
                                    editing = nil
                                }
                            }
                            .disabled(cleanName.trimmingCharacters(in: .whitespaces).isEmpty)
                        }
                    }
                    .navigationTitle("Correct")
                    .toolbar {
                        ToolbarItem(placement: .cancellationAction) {
                            Button("Cancel") { editing = nil }
                        }
                    }
                }
                .presentationDetents([.medium])
            }
        }
    }

    private func begin(_ merchant: UnresolvedMerchant) {
        cleanName = merchant.suggested?.clean ?? prettify(merchant.raw)
        category = merchant.suggested?.category ?? "Uncategorized"
        editing = merchant
    }

    /// "SUNRISE STORES" → "Sunrise Stores". A starting point the user can
    /// accept or overwrite; never the answer on its own.
    private func prettify(_ raw: String) -> String {
        raw.lowercased()
            .split(separator: " ")
            .map { $0.prefix(1).uppercased() + $0.dropFirst() }
            .joined(separator: " ")
    }
}
