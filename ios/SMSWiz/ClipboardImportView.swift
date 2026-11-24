//
//  ClipboardImportView.swift
//  SMS Wiz — reference implementation
//
//  NOT COMPILED OR TESTED IN THIS REPOSITORY.
//
//  Why paste is the primary import path: the filter extension cannot read
//  message bodies, and a Shortcuts "Message" automation may not expose the body
//  at all (docs/ios-constraints.md §3). Selecting bubbles in Messages and
//  copying them always works, on every iOS version, with no entitlement.
//
//  The screen is built around the three things a person needs to know:
//    1. what the app understood (N messages, how many with a sender),
//    2. what happened to each one (filed, queued, refused and why),
//    3. that nothing was silently dropped.
//

import SwiftUI
import SMSCore
#if canImport(UIKit)
import UIKit
#endif

struct ClipboardImportView: View {
    @EnvironmentObject private var store: AppStore

    @State private var text: String = ""
    @State private var preview: [PastedMessage] = []
    @State private var results: [Result] = []
    @State private var importing = false
    @State private var progress = 0

    struct Result: Identifiable {
        let id: UUID
        let sender: String
        let body: String
        let outcome: String
        let isError: Bool

        init(message: PastedMessage, outcome: String, isError: Bool) {
            self.id = message.id
            self.sender = message.sender
            self.body = message.body
            self.outcome = outcome
            self.isError = isError
        }
    }

    var body: some View {
        NavigationStack {
            List {
                Section {
                    Button {
                        pasteFromClipboard()
                    } label: {
                        Label("Paste from clipboard", systemImage: "doc.on.clipboard")
                    }

                    if clipboardCount > 0 {
                        Text("\(clipboardCount) messages on the clipboard right now")
                            .font(.footnote)
                            .foregroundStyle(.secondary)
                    }
                } header: {
                    Text("1. Paste")
                } footer: {
                    Text("In Messages, select the bubbles, tap Copy, then come back here. A sender line like “AD-HDFCBK-T:” is used when present; without one the message is imported as an unknown sender rather than guessed.")
                }

                if !text.isEmpty {
                    Section("2. Review") {
                        TextEditor(text: $text)
                            .frame(minHeight: 120)
                            .font(.system(.body, design: .monospaced))
                            .onChange(of: text) { _, newValue in
                                preview = PasteParser.parse(newValue)
                                results = []
                            }

                        HStack {
                            Text("\(preview.count) messages")
                            Spacer()
                            Text("\(preview.filter(\.hasSender).count) with a sender")
                                .foregroundStyle(.secondary)
                        }
                        .font(.footnote)
                    }

                    Section {
                        Button {
                            Task { await importAll() }
                        } label: {
                            if importing {
                                HStack(spacing: 8) {
                                    ProgressView()
                                    Text("Importing \(progress)/\(preview.count)")
                                }
                            } else {
                                Label("Import \(preview.count) messages", systemImage: "tray.and.arrow.down")
                            }
                        }
                        .disabled(importing || preview.isEmpty)
                    } header: {
                        Text("3. Import")
                    } footer: {
                        Text("Each message is classified by the server and stored once. Re-deliveries are folded, and anything the app could not reach is queued in the App Group and retried from Settings.")
                    }
                }

                if !results.isEmpty {
                    Section("What happened") {
                        ForEach(results) { result in
                            VStack(alignment: .leading, spacing: 4) {
                                HStack {
                                    Text(result.sender.isEmpty ? "unknown sender" : result.sender)
                                        .font(.footnote.monospaced())
                                    Spacer()
                                    Label(result.outcome, systemImage: result.isError ? "xmark.octagon" : "checkmark.circle")
                                        .font(.footnote)
                                        .foregroundStyle(result.isError ? .red : .green)
                                }
                                Text(result.body)
                                    .font(.footnote)
                                    .lineLimit(2)
                                    .foregroundStyle(.secondary)
                            }
                        }
                    }
                }

                if store.queued > 0 {
                    Section {
                        Button {
                            Task { _ = await store.flushQueue() }
                        } label: {
                            Label("Retry \(store.queued) queued messages", systemImage: "arrow.triangle.2.circlepath")
                        }
                    }
                }
            }
            .navigationTitle("Import")
        }
    }

    /// How many messages a paste would find, without importing anything.
    private var clipboardCount: Int {
        guard let text = UIPasteboard.general.string, !text.isEmpty else { return 0 }
        return PasteParser.parse(text).count
    }

    private func pasteFromClipboard() {
        if let clipboard = UIPasteboard.general.string, !clipboard.isEmpty {
            text = clipboard
            preview = PasteParser.parse(clipboard)
        }
    }

    private func importAll() async {
        importing = true
        progress = 0
        results = []
        for message in preview {
            progress += 1
            guard let outcome = await store.ingest(
                sender: message.sender,
                body: message.body,
                source: "clipboard"
            ) else {
                results.append(Result(message: message, outcome: "failed — see the alert below", isError: true))
                continue
            }
            switch outcome {
            case let .stored(result):
                let label: String
                if result.duplicateOf != nil {
                    label = "already recorded"
                } else if result.summary.isEmpty {
                    label = result.intent
                } else {
                    label = result.summary
                }
                results.append(Result(message: message, outcome: label, isError: false))
            case .queued:
                results.append(Result(message: message, outcome: "queued (server unreachable)", isError: false))
            }
        }
        importing = false
        await store.refresh()
    }
}
