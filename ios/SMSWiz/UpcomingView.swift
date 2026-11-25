import SwiftUI
import SMSCore

/// Recurring mandates/subscriptions, credit-card reconciliation, and risk alerts.
struct UpcomingView: View {
    @State private var subs: SubscriptionsSummary?
    @State private var cards: CardsSummary?
    @State private var alerts: [AlertItem] = []
    @State private var errorText: String?
    @State private var busy = false

    var body: some View {
        NavigationStack {
            List {
                if let errorText {
                    Section {
                        Text(errorText).foregroundStyle(.orange)
                    }
                }

                if let subs {
                    Section("Recurring & Mandates") {
                        HStack {
                            Text("Monthly commitment")
                            Spacer()
                            Text("₹\(Int(subs.monthlyCommitment))")
                                .font(.headline.monospacedDigit())
                        }
                        ForEach(subs.series) { item in
                            HStack {
                                VStack(alignment: .leading, spacing: 2) {
                                    Text(item.name).font(.headline)
                                    Text("\(item.cadence) · \(item.nextDue ?? "date unknown")")
                                        .font(.caption)
                                        .foregroundStyle(.secondary)
                                }
                                Spacer()
                                if let amount = item.amount {
                                    Text("₹\(Int(amount))")
                                        .font(.subheadline.monospacedDigit())
                                }
                            }
                        }
                    }
                }

                if let cards, !cards.cards.isEmpty {
                    Section("Credit Cards") {
                        ForEach(cards.cards) { card in
                            HStack {
                                VStack(alignment: .leading, spacing: 2) {
                                    Text("\(card.bank) · \(card.last4)")
                                        .font(.headline)
                                    Text(card.status.replacingOccurrences(of: "_", with: " "))
                                        .font(.caption)
                                        .foregroundStyle(card.status == "overdue" ? .red : .secondary)
                                }
                                Spacer()
                                VStack(alignment: .trailing, spacing: 2) {
                                    if let out = card.outstanding {
                                        Text("₹\(Int(out)) due")
                                            .font(.subheadline.monospacedDigit())
                                    }
                                    Text("₹\(Int(card.paid)) paid")
                                        .font(.caption.monospacedDigit())
                                        .foregroundStyle(.secondary)
                                }
                            }
                        }
                    }
                }

                Section("Alerts") {
                    if alerts.isEmpty {
                        Text("No alerts.").foregroundStyle(.secondary)
                    } else {
                        ForEach(alerts) { alert in
                            VStack(alignment: .leading, spacing: 4) {
                                Text((alert.severity ?? "info").uppercased())
                                    .font(.caption2.bold())
                                    .foregroundStyle(alert.severity == "critical" ? .red : .secondary)
                                Text(alert.message ?? "")
                                    .font(.subheadline)
                            }
                            .padding(.vertical, 2)
                        }
                    }
                }
            }
            .navigationTitle("Upcoming")
            .refreshable { await load() }
            .task { await load() }
            .overlay {
                if busy && subs == nil && cards == nil && alerts.isEmpty {
                    ProgressView()
                }
            }
        }
    }

    private func load() async {
        busy = true
        defer { busy = false }
        let client = APIClient()
        do {
            async let s = client.subscriptions()
            async let c = client.cards()
            async let a = client.alerts(limit: 30)
            subs = try await s
            cards = try await c
            alerts = try await a
            errorText = nil
        } catch {
            errorText = (error as? LocalizedError)?.errorDescription ?? String(describing: error)
        }
    }
}
