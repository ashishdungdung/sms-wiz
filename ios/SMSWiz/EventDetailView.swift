import SwiftUI
import SMSCore

/// One-tap audit sheet: shows why SMS Wiz classified an event and whether it counted.
struct EventDetailView: View {
    let event: EventSummaryItem

    var body: some View {
        List {
            Section("Summary") {
                Text(event.summary.isEmpty ? event.intent : event.summary)
                    .font(.headline)
                row("Intent", event.intent)
                if let amount = event.amountValue {
                    row("Amount", "₹\(Int(amount))")
                }
                if let inst = event.institution {
                    row("Institution", inst)
                }
                if let merchant = event.merchantClean ?? event.merchantRaw {
                    row("Merchant", merchant)
                }
            }

            Section("Ledger & Dedup") {
                row("Treatment", event.ledgerTreatment)
                row("Counts toward spend", event.countsTowardSpend ? "Yes" : "No (excluded)")
                row("Dedup", event.dedupStatus.replacingOccurrences(of: "_", with: " "))
                row("Confidence", String(format: "%.0f%%", event.confidence * 100))
                if event.needsReview {
                    Label("Flagged for review", systemImage: "exclamationmark.triangle")
                        .foregroundStyle(.orange)
                }
            }

            Section("Apple Folder") {
                row("Action", event.appleAction)
                row("Sub-action", event.appleSubaction)
                row("Retention", event.retention)
            }

            if !event.evidence.isEmpty {
                Section("Evidence") {
                    ForEach(event.evidence, id: \.self) { line in
                        Text(line).font(.footnote)
                    }
                }
            }

            Section {
                NavigationLink {
                    ComplaintView(
                        initialSender: event.senderRaw,
                        initialBody: event.summary
                    )
                } label: {
                    Label("Report as Spam / Fraud (1909 / 1930)", systemImage: "exclamationmark.shield")
                        .foregroundStyle(.red)
                }
            }
        }
        .navigationTitle("Audit")
    }

    private func row(_ title: String, _ value: String) -> some View {
        HStack {
            Text(title).foregroundStyle(.secondary)
            Spacer()
            Text(value).multilineTextAlignment(.trailing)
        }
    }
}
