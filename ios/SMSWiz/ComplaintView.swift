import SwiftUI

/// TRAI 1909 (UCC spam) and 1930 (cyber fraud) complaint draft builder.
/// Prepares a draft for the user to review and send manually — never auto-sends.
struct ComplaintView: View {
    enum Ground: String, CaseIterable, Identifiable {
        case ucc1909 = "TRAI 1909 (Unsolicited Commercial)"
        case fraud1930 = "Cyber 1930 (Fraud / Phishing)"
        var id: String { rawValue }
    }

    @State private var ground: Ground = .ucc1909
    @State private var sender: String
    @State private var receivedDate = Date()
    @State private var messageBody: String
    @State private var copied = false

    init(initialSender: String = "", initialBody: String = "") {
        _sender = State(initialValue: initialSender)
        _messageBody = State(initialValue: initialBody)
    }

    private var draftText: String {
        let formatter = DateFormatter()
        formatter.dateFormat = "dd/MM/yyyy HH:mm"
        let when = formatter.string(from: receivedDate)
        switch ground {
        case .ucc1909:
            return """
            TRAI DND / UCC Complaint (1909)
            Sender: \(sender.isEmpty ? "<sender>" : sender)
            Received: \(when)
            Details: Unsolicited commercial communication received despite DND preference.
            Message: \(messageBody.isEmpty ? "<paste SMS text>" : messageBody)
            """
        case .fraud1930:
            return """
            National Cyber Crime Helpline (1930) / Bank Fraud Report
            Sender: \(sender.isEmpty ? "<sender>" : sender)
            Received: \(when)
            Details: Suspected phishing or fraudulent SMS.
            Message: \(messageBody.isEmpty ? "<paste SMS text>" : messageBody)
            """
        }
    }

    var body: some View {
        NavigationStack {
            Form {
                Section("Classification") {
                    Picker("Channel", selection: $ground) {
                        ForEach(Ground.allCases) { g in
                            Text(g.rawValue).tag(g)
                        }
                    }
                    TextField("Sender (e.g. AD-HDFCBK-S or +9198...)", text: $sender)
                        .textInputAutocapitalization(.characters)
                    DatePicker("Received", selection: $receivedDate)
                }

                Section("Message text") {
                    TextEditor(text: $messageBody)
                        .frame(minHeight: 90)
                }

                Section("Draft (review before sending)") {
                    Text(draftText)
                        .font(.footnote.monospaced())
                        .textSelection(.enabled)

                    HStack {
                        Button(copied ? "Copied Draft" : "Copy Draft") {
                            UIPasteboard.general.string = draftText
                            copied = true
                        }
                        Spacer()
                        ShareLink(item: draftText) {
                            Label("Share", systemImage: "square.and.arrow.up")
                        }
                    }
                }

                Section {
                    Text("SMS Wiz prepares this draft for you. Nothing is sent automatically, and no outcome or disconnection is guaranteed.")
                        .font(.footnote)
                        .foregroundStyle(.secondary)
                }
            }
            .navigationTitle("Report")
        }
    }
}
