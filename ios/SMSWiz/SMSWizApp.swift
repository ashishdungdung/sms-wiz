//
//  SMSWizApp.swift
//  SMS Wiz — reference implementation
//
//  NOT COMPILED OR TESTED IN THIS REPOSITORY. See ios/README.md.
//
//  The app is deliberately four screens, because the phone is a *front end* to a
//  ledger the server owns:
//
//    Ledger   — this month, straight from /api/v1/overview
//    Import   — paste or share (the two paths that actually work)
//    Merchants— the review queue, backed by /api/v1/merchants
//    Settings — server address, token, export, queue
//
//  There is no local database on purpose: one source of truth means the CSV
//  export, the API and the app can never disagree about what you spent.
//

import SwiftUI
import SMSCore

@main
struct SMSWizApp: App {
    @StateObject private var store = AppStore()

    var body: some Scene {
        WindowGroup {
            TabView {
                LedgerView()
                    .tabItem { Label("Ledger", systemImage: "indianrupeesign.circle") }

                UpcomingView()
                    .tabItem { Label("Upcoming", systemImage: "calendar.badge.clock") }

                VaultView()
                    .tabItem { Label("Vault", systemImage: "lock.doc") }

                ClipboardImportView()
                    .tabItem { Label("Import", systemImage: "doc.on.clipboard") }

                MerchantReviewView()
                    .tabItem { Label("Merchants", systemImage: "building.2") }

                ComplaintView()
                    .tabItem { Label("Report", systemImage: "exclamationmark.shield") }

                SettingsView()
                    .tabItem { Label("Settings", systemImage: "gearshape") }
            }
            .environmentObject(store)
            .task { await store.refresh() }
        }
    }
}

/// One observable place for the month, the import queue and the merchant queue.
///
/// Small on purpose: a view asks for what it shows, and failures land in
/// `lastError` so every screen can display the same sentence about what broke.
@MainActor
final class AppStore: ObservableObject {
    @Published var month: Overview?
    @Published var recentEvents: [EventSummaryItem] = []
    @Published var unresolved: [UnresolvedMerchant] = []
    @Published var queued: Int = AppConfig.pendingMessages().count
    @Published var lastError: String?
    @Published var busy = false

    private var client: APIClient { APIClient() }

    func refresh() async {
        async let monthTask: Void = loadMonth()
        async let queueTask: Void = loadMerchants()
        _ = await (monthTask, queueTask)
        queued = AppConfig.pendingMessages().count
    }

    func loadMonth() async {
        busy = true
        defer { busy = false }
        do {
            month = try await client.overview()
            recentEvents = (try? await client.events(limit: 25)) ?? []
            lastError = nil
        } catch {
            lastError = (error as? LocalizedError)?.errorDescription ?? String(describing: error)
        }
    }

    func loadMerchants() async {
        do {
            unresolved = try await client.unresolvedMerchants()
        } catch {
            lastError = (error as? LocalizedError)?.errorDescription ?? String(describing: error)
        }
    }

    /// Import one pasted or shared message; queue when offline.
    func ingest(sender: String, body: String, source: String) async -> IngestOutcome? {
        do {
            let outcome = try await client.ingest(sender: sender, body: body, source: source)
            queued = AppConfig.pendingMessages().count
            return outcome
        } catch {
            lastError = (error as? LocalizedError)?.errorDescription ?? String(describing: error)
            return nil
        }
    }

    func flushQueue() async -> (sent: Int, remaining: Int) {
        let result = await client.flushQueue()
        queued = result.remaining
        return result
    }

    func rename(raw: String, clean: String, category: String) async -> Int? {
        do {
            let result = try await client.correctMerchant(raw: raw, clean: clean, category: category)
            await loadMerchants()
            await loadMonth()
            return result.eventsUpdated
        } catch {
            lastError = (error as? LocalizedError)?.errorDescription ?? String(describing: error)
            return nil
        }
    }
}
