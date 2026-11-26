//
//  SettingsView.swift
//  SMS Wiz — reference implementation
//
//  NOT COMPILED OR TESTED IN THIS REPOSITORY.
//
//  Three jobs: point the app at your server, show what the server says about
//  itself, and give you your data back. The export section is not a debug tool —
//  it is the promise that the ledger is never hostage, so it sits next to the
//  settings rather than three taps deep in a menu.
//

import SwiftUI
import SMSCore
import UIKit

struct SettingsView: View {
    @EnvironmentObject private var store: AppStore

    @State private var baseURL = AppConfig.load().baseURL
    @State private var token = AppConfig.load().token
    @State private var health: String = "not checked"
    @State private var exportURL: URL?
    @State private var busy = false
    @State private var message: String?

    var body: some View {
        NavigationStack {
            Form {
                Section {
                    TextField("http://192.168.0.5:8000", text: $baseURL)
                        .keyboardType(.URL)
                        .textInputAutocapitalization(.never)
                        .autocorrectionDisabled()
                    SecureField("Token (optional, but set one)", text: $token)
                        .textInputAutocapitalization(.never)
                        .autocorrectionDisabled()
                    Button("Save and check") {
                        Task { await saveAndCheck() }
                    }
                    .disabled(busy || baseURL.isEmpty)
                    LabeledContent("Server", value: health)
                } header: {
                    Text("Server")
                } footer: {
                    Text("The token is the same value as SMSWIZ_TOKEN on the server. If the server has one set, every screen needs it — reads included. Stored in the App Group so the share extension and the Shortcuts action use the same value.")
                }

                Section {
                    if store.queued == 0 {
                        Text("Nothing waiting.").foregroundStyle(.secondary)
                    } else {
                        Button("Send \(store.queued) queued messages") {
                            Task {
                                let outcome = await store.flushQueue()
                                message = "\(outcome.sent) sent, \(outcome.remaining) still queued"
                            }
                        }
                    }
                } header: {
                    Text("Offline queue")
                } footer: {
                    Text("Messages hand-delivered while the server was asleep are kept here, not dropped.")
                }

                Section {
                    NavigationLink("Offline Filter Rules") {
                        FilterRulesSettingsView()
                    }
                    Button {
                        Task { await reclassifyAll() }
                    } label: {
                        Label("Re-run Classification Rules", systemImage: "arrow.triangle.2.circlepath")
                    }
                    .disabled(busy)
                } header: {
                    Text("Rules & Cleanup")
                }

                Section {
                    Button {
                        Task { await export("csv") }
                    } label: {
                        Label("Export as CSV", systemImage: "tablecells")
                    }
                    Button {
                        Task { await export("json") }
                    } label: {
                        Label("Export as one JSON snapshot", systemImage: "curlybraces")
                    }
                    if let exportURL {
                        ShareLink(item: exportURL) {
                            Label("Share \(exportURL.lastPathComponent)", systemImage: "square.and.arrow.up")
                        }
                    }
                } header: {
                    Text("Your data")
                } footer: {
                    Text("CSV opens in any spreadsheet; the JSON snapshot carries events, refunds, vouchers, balances and the merchant dictionary. Nothing is deleted by exporting.")
                }

                if let message {
                    Section {
                        Text(message).font(.footnote).foregroundStyle(.secondary)
                    }
                }

                if let error = store.lastError {
                    Section {
                        Text(error).foregroundStyle(.orange)
                    }
                }

                Section {
                    Text("Version \(Bundle.main.infoDictionary?["CFBundleShortVersionString"] as? String ?? "dev")")
                        .font(.footnote)
                        .foregroundStyle(.secondary)
                }
            }
            .navigationTitle("Settings")
        }
    }

    private func saveAndCheck() async {
        busy = true
        defer { busy = false }
        let config = AppConfig(baseURL: baseURL, token: token)
        config.save()
        do {
            let info = try await APIClient(config: config).health()
            health = "\(info.status) · \(info.merchantKeys) merchants · LLM \(info.llmEnabled ? "on" : "off")"
            message = nil
            await store.refresh()
        } catch {
            health = "unreachable"
            message = (error as? LocalizedError)?.errorDescription ?? String(describing: error)
        }
    }

    private func export(_ format: String) async {
        busy = true
        defer { busy = false }
        do {
            let (data, filename) = try await APIClient().export(format: format)
            let url = FileManager.default.temporaryDirectory.appendingPathComponent(filename)
            try data.write(to: url, options: .atomic)
            exportURL = url
            message = "\(filename) — \(data.count) bytes"
        } catch {
            message = (error as? LocalizedError)?.errorDescription ?? String(describing: error)
        }
    }

    private func reclassifyAll() async {
        busy = true
        defer { busy = false }
        do {
            let res = try await APIClient().reclassify()
            message = "Reclassified \(res.processed) events (\(res.updated) updated)."
            await store.refresh()
        } catch {
            message = (error as? LocalizedError)?.errorDescription ?? String(describing: error)
        }
    }
}

private struct FilterRulesSettingsView: View {
    @State private var blockedSenders: [String] = AppConfig.blockedSenders()
    @State private var junkKeywords: [String] = AppConfig.junkKeywords()
    @State private var promoKeywords: [String] = AppConfig.promoKeywords()
    @State private var newSender = ""
    @State private var newJunk = ""
    @State private var newPromo = ""

    var body: some View {
        Form {
            ruleSection(
                title: "Blocked Senders (→ Junk)",
                placeholder: "e.g. AD-RUMMYC-P",
                input: $newSender,
                items: $blockedSenders,
                uppercase: true,
                save: AppConfig.setBlockedSenders
            )
            ruleSection(
                title: "Junk Keywords (→ Junk)",
                placeholder: "e.g. rummy",
                input: $newJunk,
                items: $junkKeywords,
                uppercase: false,
                save: AppConfig.setJunkKeywords
            )
            ruleSection(
                title: "Promo Keywords (→ Promotions)",
                placeholder: "e.g. % off",
                input: $newPromo,
                items: $promoKeywords,
                uppercase: false,
                save: AppConfig.setPromoKeywords
            )
        }
        .navigationTitle("Offline Filter Rules")
    }

    @ViewBuilder
    private func ruleSection(
        title: String,
        placeholder: String,
        input: Binding<String>,
        items: Binding<[String]>,
        uppercase: Bool,
        save: @escaping ([String]) -> Void
    ) -> some View {
        Section(title) {
            HStack {
                TextField(placeholder, text: input)
                    .textInputAutocapitalization(uppercase ? .characters : .never)
                Button("Add") {
                    let trimmed = input.wrappedValue.trimmingCharacters(in: .whitespacesAndNewlines)
                    guard !trimmed.isEmpty else { return }
                    let val = uppercase ? trimmed.uppercased() : trimmed
                    if !items.wrappedValue.contains(val) {
                        items.wrappedValue.append(val)
                        save(items.wrappedValue)
                    }
                    input.wrappedValue = ""
                }
                .disabled(input.wrappedValue.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty)
            }
            ForEach(items.wrappedValue, id: \.self) { item in
                Text(item)
            }
            .onDelete { offsets in
                items.wrappedValue.remove(atOffsets: offsets)
                save(items.wrappedValue)
            }
        }
    }
}
