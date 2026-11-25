import SwiftUI
import SMSCore

/// Vouchers, refund claims, and Apple Messages folder cleanup queues.
struct VaultView: View {
    @State private var vouchers: [VoucherItem] = []
    @State private var refunds: [RefundClaim] = []
    @State private var queues: [CleanupQueueBucket] = []
    @State private var errorText: String?
    @State private var copiedCode: String?
    @State private var busy = false

    var body: some View {
        NavigationStack {
            List {
                if let errorText {
                    Section {
                        Text(errorText).foregroundStyle(.orange)
                    }
                }

                Section("Vouchers") {
                    if vouchers.isEmpty {
                        Text("No vouchers saved yet.")
                            .foregroundStyle(.secondary)
                    } else {
                        ForEach(vouchers) { v in
                            VStack(alignment: .leading, spacing: 4) {
                                HStack {
                                    Text(v.brand ?? "Voucher")
                                        .font(.headline)
                                    Spacer()
                                    if let value = v.value {
                                        Text("₹\(Int(value))")
                                            .font(.headline.monospacedDigit())
                                    }
                                }
                                if let code = v.code {
                                    HStack {
                                        Text("Code: \(code)")
                                            .font(.subheadline.monospaced())
                                        Spacer()
                                        Button(copiedCode == code ? "Copied" : "Copy") {
                                            UIPasteboard.general.string = code
                                            copiedCode = code
                                        }
                                        .buttonStyle(.bordered)
                                        .controlSize(.small)
                                    }
                                }
                                HStack {
                                    if let pin = v.pin {
                                        Text("PIN: \(pin)")
                                            .font(.caption.monospaced())
                                    }
                                    Spacer()
                                    if let exp = v.expiresOn {
                                        Text(v.expired == true ? "Expired \(exp)" : "Expires \(exp)")
                                            .font(.caption)
                                            .foregroundStyle(v.expired == true ? .red : .secondary)
                                    }
                                }
                            }
                            .padding(.vertical, 2)
                        }
                    }
                }

                Section("Refund Watchdog") {
                    if refunds.isEmpty {
                        Text("No open or matched refunds.")
                            .foregroundStyle(.secondary)
                    } else {
                        ForEach(refunds) { r in
                            HStack {
                                VStack(alignment: .leading, spacing: 2) {
                                    Text(r.merchant ?? "Refund")
                                        .font(.headline)
                                    Text(r.status.replacingOccurrences(of: "_", with: " "))
                                        .font(.caption)
                                        .foregroundStyle(r.overdue == true ? .red : .secondary)
                                }
                                Spacer()
                                Text("₹\(Int(r.amount))")
                                    .font(.subheadline.monospacedDigit())
                            }
                        }
                    }
                }

                Section {
                    if queues.isEmpty {
                        Text("No folder counts yet.")
                            .foregroundStyle(.secondary)
                    } else {
                        ForEach(queues) { q in
                            VStack(alignment: .leading, spacing: 4) {
                                HStack {
                                    Text(q.folder)
                                        .font(.subheadline.bold())
                                    Spacer()
                                    Text("\(q.count)")
                                        .font(.subheadline.monospacedDigit())
                                }
                                Text("Retention: \(q.retention)")
                                    .font(.caption)
                                    .foregroundStyle(.secondary)
                            }
                        }
                    }
                } header: {
                    Text("Apple Messages Cleanup")
                } footer: {
                    Text("iOS does not allow apps to delete SMS. Open the matching folder in Messages to review and clear.")
                }
            }
            .navigationTitle("Vault")
            .refreshable { await load() }
            .task { await load() }
            .overlay {
                if busy && vouchers.isEmpty && refunds.isEmpty && queues.isEmpty {
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
            async let v = client.vouchers()
            async let r = client.refunds()
            async let q = client.cleanupQueues()
            vouchers = try await v
            refunds = try await r
            queues = try await q.queues
            errorText = nil
        } catch {
            errorText = (error as? LocalizedError)?.errorDescription ?? String(describing: error)
        }
    }
}
