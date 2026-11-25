//
//  ShareViewController.swift
//  SMS Wiz — share extension
//
//  NOT COMPILED OR TESTED IN THIS REPOSITORY. This file belongs to a **separate
//  target** (File ▸ New ▸ Target ▸ Share Extension); see ios/README.md for the
//  wiring, the Info.plist below and the App Group that makes it share settings
//  with the app.
//
//  Why a share extension matters: from Messages you can long-press a bubble and
//  Share it. That path needs no entitlement, works on every iOS version, and is
//  the only one that carries the text of a message the filter extension was not
//  allowed to read.
//
//  The extension is deliberately tiny and fails *closed to queuing*: whatever
//  the user shared is written to the App Group queue before anything is sent, so
//  a crash, a timeout or a locked phone cannot lose it. It never asks for the
//  network twice — if the POST fails, the queued copy is the retry.
//

import SMSCore
import SwiftUI
import UIKit
import UniformTypeIdentifiers

/// The view model behind the small sheet the extension shows.
@MainActor
final class ShareViewModel: ObservableObject {
    enum Phase: Equatable {
        case waiting
        case sending
        case done(String)
        case queued
        case failed(String)
    }

    @Published var phase: Phase = .waiting
    @Published var preview: String = ""

    private let client = APIClient()

    /// Extract text from a share sheet payload. iOS hands over NSItemProvider
    /// objects; the two types that matter here are plain text and a URL that
    /// points at a message (some apps share "sms:" links).
    ///
    /// `async` rather than a callback so the extension's `viewDidLoad` can await
    /// it in one `Task` and the main actor is never left guessing which thread a
    /// completion ran on.
    func load(from items: [NSExtensionItem]) async -> String? {
        let providers = items.compactMap(\.attachments).flatMap { $0 }
        guard !providers.isEmpty else { return nil }

        var collected: [String] = []
        for provider in providers {
            if provider.hasItemConformingToTypeIdentifier(UTType.plainText.identifier) {
                let item = try? await provider.loadItem(forTypeIdentifier: UTType.plainText.identifier)
                if let text = item as? String {
                    collected.append(text)
                } else if let data = item as? Data, let text = String(data: data, encoding: .utf8) {
                    collected.append(text)
                }
            } else if provider.hasItemConformingToTypeIdentifier(UTType.url.identifier) {
                let item = try? await provider.loadItem(forTypeIdentifier: UTType.url.identifier)
                if let url = item as? URL {
                    collected.append(url.absoluteString)
                }
            }
        }

        let text = collected.joined(separator: "\n").trimmingCharacters(in: .whitespacesAndNewlines)
        return text.isEmpty ? nil : text
    }

    /// Queue first, then send. `queue-first` is the whole design: the message
    /// the user shared exists on disk before any network call is attempted.
    func submit(_ text: String) async {
        let messages = PasteParser.parse(text)
        guard !messages.isEmpty else {
            phase = .failed("Nothing to file — that share had no message text.")
            return
        }
        for message in messages {
            AppConfig.appendPending(
                .init(sender: message.sender, body: message.body, source: "share")
            )
        }

        phase = .sending
        let outcome = await client.flushQueue()
        if outcome.remaining == 0 {
            phase = .done(messages.count == 1 ? "Filed 1 message" : "Filed \(messages.count) messages")
        } else {
            phase = .queued
        }
    }
}

final class ShareViewController: UIViewController {
    private let model = ShareViewModel()
    private var hosting: UIHostingController<ShareSheetView>?

    override func viewDidLoad() {
        super.viewDidLoad()
        let sheet = ShareSheetView(model: model) { [weak self] in
            self?.finish()
        }
        let hosting = UIHostingController(rootView: sheet)
        self.hosting = hosting
        addChild(hosting)
        hosting.view.frame = view.bounds
        hosting.view.autoresizingMask = [.flexibleWidth, .flexibleHeight]
        view.addSubview(hosting.view)
        hosting.didMove(toParent: self)

        view.backgroundColor = .systemBackground

        guard let items = extensionContext?.inputItems as? [NSExtensionItem] else {
            finish()
            return
        }
        Task { @MainActor in
            guard let text = await model.load(from: items) else {
                model.phase = .failed("That app shared something without message text.")
                return
            }
            model.preview = text
            await model.submit(text)
        }
    }

    private func finish() {
        extensionContext?.completeRequest(returningItems: [], completionHandler: nil)
    }
}

/// The sheet: one sentence, a preview, and a Done button. A share extension has
/// seconds, not minutes; anything more elaborate gets killed by the system.
struct ShareSheetView: View {
    @ObservedObject var model: ShareViewModel
    let done: () -> Void

    var body: some View {
        VStack(alignment: .leading, spacing: 16) {
            HStack(spacing: 8) {
                Image(systemName: icon)
                    .foregroundStyle(tint)
                Text(title).font(.headline)
                Spacer()
            }

            if !model.preview.isEmpty {
                ScrollView {
                    Text(model.preview)
                        .font(.footnote)
                        .foregroundStyle(.secondary)
                        .frame(maxWidth: .infinity, alignment: .leading)
                }
                .frame(maxHeight: 160)
            }

            if case let .failed(reason) = model.phase {
                Text(reason).font(.footnote).foregroundStyle(.orange)
            }
            if model.phase == .queued {
                Text("The server could not be reached, so the message is queued. Open the app → Settings to send it; nothing is lost.")
                    .font(.footnote)
                    .foregroundStyle(.secondary)
            }

            HStack {
                Spacer()
                Button("Done", action: done)
                    .buttonStyle(.borderedProminent)
                    .disabled(model.phase == .sending || model.phase == .waiting)
            }
        }
        .padding(20)
    }

    private var title: String {
        switch model.phase {
        case .waiting: return "Reading…"
        case .sending: return "Filing with SMS Wiz…"
        case let .done(text): return text
        case .queued: return "Queued for later"
        case .failed(_): return "Could not file this"
        }
    }

    private var icon: String {
        switch model.phase {
        case .done(_): return "checkmark.circle.fill"
        case .queued: return "clock.arrow.circlepath"
        case .failed(_): return "exclamationmark.triangle.fill"
        default: return "message"
        }
    }

    private var tint: Color {
        switch model.phase {
        case .done(_): return .green
        case .queued: return .blue
        case .failed(_): return .orange
        default: return .secondary
        }
    }
}
