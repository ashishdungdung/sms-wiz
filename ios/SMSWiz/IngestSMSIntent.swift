//
//  IngestSMSIntent.swift
//  SMS Wiz — reference implementation
//
//  NOT COMPILED OR TESTED IN THIS REPOSITORY.
//
//  This is the "Shortcuts bridge": one App Intent that takes a message body and
//  sender and posts them to the server. Whether the Shortcuts "Message"
//  automation can hand you the body is device/iOS dependent — test it with
//  docs/ios-constraints.md §3 before building anything on top.
//
//  The same intent is useful with a manual shortcut, a share extension, or a
//  clipboard import, so it is worth having either way.
//

import AppIntents
import Foundation
import SMSCore
import UserNotifications

struct IngestSMSIntent: AppIntent {
    static var title: LocalizedStringResource = "Analyse SMS with SMS Wiz"
    static var description = IntentDescription(
        "Classifies an SMS (bank / UPI / delivery / OTP) and files it with SMS Wiz."
    )

    /// Keep the app in the background when this runs from an automation.
    static var openAppWhenRun: Bool = false

    @Parameter(title: "Message body")
    var messageBody: String

    @Parameter(title: "Sender")
    var sender: String

    func perform() async throws -> some IntentResult & ReturnsValue<String> {
        let config = AppConfig.load()

        guard let url = URL(string: config.baseURL + "/api/v1/ingest-sms") else {
            return .result(value: "SMS Wiz: server URL is not configured")
        }

        var request = URLRequest(url: url)
        request.httpMethod = "POST"
        request.timeoutInterval = 12
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        if !config.token.isEmpty {
            request.setValue(config.token, forHTTPHeaderField: "X-SMSWiz-Token")
        }

        let payload: [String: Any] = [
            "sender": sender,
            "body": messageBody,
            "source": "shortcut",
        ]
        request.httpBody = try JSONSerialization.data(withJSONObject: payload)

        do {
            let (data, response) = try await URLSession.shared.data(for: request)
            guard let http = response as? HTTPURLResponse, (200..<300).contains(http.statusCode) else {
                return .result(value: "SMS Wiz: server rejected the message")
            }

            let ingest = try JSONDecoder().decode(IngestResponse.self, from: data)

            // Only interrupt the user for things that can cost money or safety.
            for alert in ingest.alerts where alert.severity != "info" {
                await notify(title: alert.critical ? "SMS Wiz — action needed" : "SMS Wiz", body: alert.message)
            }

            return .result(value: ingest.event.summary.isEmpty ? ingest.event.intent : ingest.event.summary)
        } catch {
            // Never lose the message: the app can re-import from clipboard later.
            AppConfig.appendPending(messageBody: messageBody, sender: sender)
            return .result(value: "SMS Wiz: offline, queued for next sync")
        }
    }

    private func notify(title: String, body: String) async {
        let content = UNMutableNotificationContent()
        content.title = title
        content.body = body
        content.sound = .default
        let request = UNNotificationRequest(identifier: UUID().uuidString, content: content, trigger: nil)
        try? await UNUserNotificationCenter.current().add(request)
    }
}

// `AppConfig` used to live here. It moved to SMSCore because the share
// extension needs the same server address and token, and two copies of "where
// is the server" is how an extension ends up posting to a stale address after
// the user changes it in Settings. Import SMSCore and use `AppConfig.load()`.

struct ShortcutsProvider: AppShortcutsProvider {
    static var appShortcuts: [AppShortcut] {
        AppShortcut(
            intent: IngestSMSIntent(),
            phrases: ["File this SMS with \(.applicationName)"],
            shortTitle: "File SMS",
            systemImageName: "message.badge"
        )
    }
}
