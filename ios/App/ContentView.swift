import SwiftUI

/// The Linux window's three tabs, on the phone: Phone (what to do next, Keep Awake, this iPhone's pairing), Agents
/// (agent control, Unlock) and Bridge. PC-only settings (Start at Login, Open Ports, Claude Code, starting and
/// stopping the services) stay on the PC.
struct ContentView: View {
    @EnvironmentObject var model: AppModel
    @State private var tab = UserDefaults.standard.string(forKey: "tab") ?? "phone"  // launch with -tab agents

    var body: some View {
        Group {
            if model.pairing == nil {
                NavigationStack { UnpairedView() }
            } else {
                TabView(selection: $tab) {
                    NavigationStack { PhoneTab() }
                        .tabItem { Label("Phone", systemImage: "iphone") }.tag("phone")
                    NavigationStack { AgentsTab() }
                        .tabItem { Label("Agents", systemImage: "sparkles") }.tag("agents")
                    NavigationStack { BridgeTab() }
                        .tabItem { Label(model.state?.inputTitle ?? "Bridge", systemImage: "dot.radiowaves.left.and.right") }
                        .tag("bridge")
                        .badge(bridgeNeedsAttention ? Text("!") : nil)
                }
                .overlay(alignment: .bottom) { NoticeView().padding(.bottom, 64) }
            }
        }
        .sheet(item: Binding(get: { model.foregroundRequest }, set: { _ in })) { request in
            RequestSheet(request: request)
                .interactiveDismissDisabled()
                .presentationDetents([.medium])
        }
        .alert("Mirror again", isPresented: $model.askRemirror) {
            Button("OK", role: .cancel) {}
        } message: {
            Text("Open Control Center, tap Screen Mirroring, and choose \(model.state?.pc.receiverName ?? "Phone Use").")
        }
    }

    /// As the Linux window marks its Bridge tab: no bridge and no PC Bluetooth, or a bridge that's offline.
    private var bridgeNeedsAttention: Bool {
        guard let s = model.state else { return false }
        if let b = s.bridge { return !b.online }
        if s.usesPCBluetooth { return !s.bluetooth.connected }
        return true
    }
}

/// The Linux window's toasts: a confirmation for a few seconds, or the last error until the next change works.
struct NoticeView: View {
    @EnvironmentObject var model: AppModel

    var body: some View {
        Group {
            if let error = model.error {
                Label(error, systemImage: "exclamationmark.triangle.fill")
                    .foregroundStyle(.white)
                    .padding(.horizontal, 16).padding(.vertical, 10)
                    .background(.red.opacity(0.9), in: Capsule())
                    .accessibilityIdentifier("error")
                    .onTapGesture { model.error = nil }
            } else if let notice = model.notice {
                Text(notice)
                    .padding(.horizontal, 16).padding(.vertical, 10)
                    .background(.regularMaterial, in: Capsule())
                    .accessibilityIdentifier("notice")
            }
        }
        .font(.subheadline.weight(.medium))
        .multilineTextAlignment(.center)
        .padding(.horizontal, 16)
        .animation(.snappy, value: model.notice)
        .animation(.snappy, value: model.error)
    }
}

/// A row's title with a dim subtitle under it, as the Linux window's rows have.
struct RowTitle: View {
    var title: String
    var subtitle: String?

    init(_ title: String, _ subtitle: String? = nil) {
        self.title = title
        self.subtitle = subtitle
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 2) {
            Text(title)
            if let subtitle, !subtitle.isEmpty {
                Text(subtitle).font(.footnote).foregroundStyle(.secondary)
            }
        }
    }
}

/// A toggle binding that shows the PC's value and sends a change at once.
@MainActor func sendBinding(_ value: Bool, _ apply: @escaping (Bool) async -> Void) -> Binding<Bool> {
    Binding(get: { value }, set: { on in Task { await apply(on) } })
}

/// A row title with a spinner while its change is in flight.
struct BusyRow: View {
    @EnvironmentObject var model: AppModel
    var title: String
    var subtitle: String?
    var key: String

    var body: some View {
        HStack {
            RowTitle(title, subtitle)
            if model.busy.contains(key) { Spacer(); ProgressView() }
        }
    }
}

/// For the Agents and Bridge tabs while the PC doesn't answer.
struct OfflineSection: View {
    @EnvironmentObject var model: AppModel

    var body: some View {
        Section {
            Label("Can't reach \(model.pairing?.name ?? "the PC").", systemImage: "wifi.exclamationmark")
                .foregroundStyle(.orange)
        }
    }
}

struct UnpairedView: View {
    @EnvironmentObject var model: AppModel
    @State private var scanning = false

    var body: some View {
        ContentUnavailableView {
            Label("Not Paired", systemImage: "qrcode.viewfinder")
        } description: {
            Text("On your computer, open Phone Use and choose Pair iPhone. Then scan the code it shows.")
        } actions: {
            Button {
                model.error = nil
                scanning = true
            } label: {
                Label("Scan QR Code", systemImage: "qrcode.viewfinder")
            }
            .buttonStyle(.borderedProminent)
            .controlSize(.large)
            .accessibilityIdentifier("scanQR")
            Button("Paste Pairing Link") {
                if let text = UIPasteboard.general.string, let url = URL(string: text.trimmingCharacters(in: .whitespacesAndNewlines)) {
                    model.pair(from: url)
                } else {
                    model.error = "The clipboard has no pairing link."
                }
            }
            .buttonStyle(.plain)
            .foregroundStyle(.tint)
            .padding(.top, 4)
            Button("See a Demo") { model.startDemo() }
                .buttonStyle(.plain)
                .foregroundStyle(.tint)
                .accessibilityIdentifier("seeDemo")
            if let error = model.error { Text(error).foregroundStyle(.red).font(.footnote) }
        }
        .toolbar(.hidden, for: .navigationBar)  // no heading over Not Paired
        .fullScreenCover(isPresented: $scanning) {
            QRScannerSheet { url in
                scanning = false
                model.pair(from: url)
            }
        }
    }
}

// MARK: - Phone

struct PhoneTab: View {
    @EnvironmentObject var model: AppModel
    @State private var confirmUnpair = false

    private var s: PCState? { model.state }
    private var offline: Bool { !model.reachable || s == nil }

    var body: some View {
        Form {
            Section { PhoneHero() }
            if model.notificationsOff {
                Section {
                    Button { Task { await model.turnOnNotifications() } } label: {
                        RowTitle("Turn On Notifications", model.state?.unlock.on == true
                                 ? "So you can allow agents and Unlock while the iPhone mirrors"
                                 : "So you can allow agents while the iPhone mirrors")
                    }
                    .accessibilityIdentifier("turnOnNotifications")
                }
            }
            if let s {
                Section {
                    Toggle(isOn: sendBinding(s.awake) { on in await model.setAwake(on) }) {
                        BusyRow(title: "Keep Awake", subtitle: "The screen stays on while the iPhone mirrors", key: "awake")
                    }
                    .accessibilityIdentifier("awake")
                    Toggle(isOn: sendBinding(s.audio.on) { on in await model.setAudio(on) }) {
                        BusyRow(title: "Sound on the PC", subtitle: "Off mutes it. iOS can't keep sound on the iPhone "
                                + "while it mirrors.", key: "audio")
                    }
                    .accessibilityIdentifier("audio")
                    if s.audio.needsRemirror == true {
                        Label("Start Screen Mirroring to \(s.pc.receiverName) again.", systemImage: "exclamationmark.triangle")
                            .foregroundStyle(.orange)
                    }
                    Button {
                        Task { await model.toggleKeyboard() }
                    } label: {
                        BusyRow(title: "Show or Hide Keyboard", key: "keyboard")
                    }
                    .tint(.primary)
                    .accessibilityIdentifier("keyboard")
                }
                .disabled(offline)
            }
            if model.demo && !Demo.forScreenshots {
                Section {
                    LabeledContent("Paired with", value: "\(Demo.pcName) (demo)")
                    Button("Exit Demo") { model.stopDemo() }
                        .accessibilityIdentifier("exitDemo")
                } footer: {
                    Text("A simulated computer. To use your own iPhone, open Phone Use on your computer and choose "
                         + "Pair iPhone.")
                }
            } else {
                Section {
                    if let p = model.pairing {
                        LabeledContent("Paired with", value: p.name)
                        if !model.demo { LabeledContent("Address", value: "\(p.host):\(p.port)") }
                    }
                    Button("Unpair", role: .destructive) { confirmUnpair = true }
                }
            }
        }
        .toolbar(.hidden, for: .navigationBar)  // the tab bar names the tab
        .refreshable { await model.refresh() }
        .alert("Unpair from \(model.pairing?.name ?? "the PC")?", isPresented: $confirmUnpair) {
            Button("Cancel", role: .cancel) {}
            Button("Unpair", role: .destructive) { model.unpair() }
        } message: {
            Text("Agents can't use the iPhone until you pair the app again.")
        }
    }

}

/// The next thing to do, or the iPhone: the Linux window's hero (phoneuse/overview.py), from what /v1/state says.
struct PhoneHero: View {
    @EnvironmentObject var model: AppModel

    private struct Step {
        var icon: String
        var title: String
        var detail: String? = nil
        var steps: [String]? = nil
        var bold: String? = nil
        var good = false
        var retry = false
    }

    private var step: Step {
        guard model.reachable, let s = model.state else {
            return Step(icon: "wifi.exclamationmark", title: "Can't reach \(model.pairing?.name ?? "the PC")",
                        detail: "Check that Phone Use is running on the PC and that this iPhone is on the same network.",
                        retry: true)
        }
        if s.serving == false {  // the PC serves only the app on the iPhone that mirrors to it
            return Step(icon: "iphone.slash", title: "\(s.phone ?? "Another iPhone") is mirroring",
                        detail: "\(s.pc.name) works with the iPhone that mirrors to it. Start Screen Mirroring here "
                            + "to use this one.")
        }
        if !s.mirroring {
            let name = s.pc.receiverName
            return Step(icon: "iphone", title: "Start Screen Mirroring",
                        steps: ["Swipe down from the top-right corner.", "Tap {mirroring} Screen Mirroring.",
                                "Choose \(name)."], bold: name)
        }
        if let b = s.bridge, !b.online {
            return Step(icon: "wifi.exclamationmark", title: "The bridge is offline",
                        detail: "Check that it has power and is on the same Wi-Fi as the PC.", retry: true)
        }
        if s.bridge == nil, s.usesPCBluetooth, !s.bluetooth.connected {
            return Step(icon: "dot.radiowaves.left.and.right", title: "Connect the iPhone to the PC",
                        detail: "Open Settings › Bluetooth and tap Phone Use. If it won't connect, forget it there and "
                            + "tap it again.")
        }
        if let b = s.bridge, !(b.connected ?? s.bluetooth.connected) {
            return Step(icon: "dot.radiowaves.left.and.right", title: "Connect the bridge to the iPhone",
                        detail: "Open Settings › Bluetooth and tap Phone Use Bridge. If it won't connect, forget it there "
                            + "and tap it again.")
        }
        if s.agent.state == "paused" {
            return Step(icon: "iphone", title: "You're in control", detail: "You took over from the agent.", good: true)
        }
        return Step(icon: "iphone", title: s.phone ?? "iPhone", detail: "Mirroring to \(s.pc.name)", good: true)
    }

    var body: some View {
        let h = step
        VStack(spacing: 8) {
            Image(systemName: h.icon)
                .font(.system(size: 48, weight: .regular))
                .foregroundStyle(h.good ? AnyShapeStyle(.green) : AnyShapeStyle(.secondary))
                .frame(height: 58)
            Text(h.title).font(.title2.weight(.semibold)).multilineTextAlignment(.center)
                .accessibilityIdentifier(h.retry && !model.reachable ? "offline" : "heroTitle")
            if let steps = h.steps { StepsList(steps: steps, bold: h.bold) }
            if let d = h.detail, !d.isEmpty {
                Text(d).foregroundStyle(.secondary).multilineTextAlignment(.center)
            }
            if h.retry {
                Button("Try Again") { Task { await model.refresh() } }
                    .buttonStyle(.bordered).buttonBorderShape(.capsule).padding(.top, 6)
            }
        }
        .frame(maxWidth: .infinity)
        .padding(.vertical, 10)
    }
}

/// Numbered steps: dim numbers, left-aligned text, the receiver's name bold, and "{mirroring}" drawn as Control
/// Center's Screen Mirroring glyph.
struct StepsList: View {
    var steps: [String]
    var bold: String?

    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            ForEach(Array(steps.enumerated()), id: \.offset) { i, step in
                HStack(alignment: .firstTextBaseline, spacing: 6) {
                    Text("\(i + 1).").foregroundStyle(.secondary).monospacedDigit()
                    line(step).fixedSize(horizontal: false, vertical: true)
                }
            }
        }
        .multilineTextAlignment(.leading)
        .padding(.top, 4)
    }

    private func line(_ step: String) -> Text {
        let parts = step.components(separatedBy: "{mirroring}")
        var text = Text(styled(parts[0]))
        for part in parts.dropFirst() {
            text = text + Text(Image("ScreenMirroring").renderingMode(.template)) + Text(styled(part))
        }
        return text
    }

    private func styled(_ s: String) -> AttributedString {
        var a = AttributedString(s)
        if let bold, !bold.isEmpty, let r = a.range(of: bold) { a[r].inlinePresentationIntent = .stronglyEmphasized }
        return a
    }
}

// MARK: - Agents

struct AgentsTab: View {
    @EnvironmentObject var model: AppModel
    @State private var passcodeSheet = false
    @State private var confirmForget = false

    private var s: PCState? { model.state }
    private var offline: Bool { !model.reachable || s == nil }

    var body: some View {
        Form {
            if offline { OfflineSection() }
            if let s {
                Group {
                    agentSection(s)
                    if let session = s.session {
                        Section { StartModeRow(session: session) }
                    }
                    if s.unlock.on { unlockSection(s) }  // Unlock is off on the PC for now: no passcode settings
                }
                .disabled(offline)
            }
            AppleIntelligenceSection()
        }
        .toolbar(.hidden, for: .navigationBar)  // the tab bar names the tab
        .refreshable { await model.refresh() }
        .sheet(isPresented: $passcodeSheet) { PasscodeSheet(replace: s?.unlock.passcodeSaved == true) }
        .alert("Forget the passcode?", isPresented: $confirmForget) {
            Button("Cancel", role: .cancel) {}
            Button("Forget", role: .destructive) { Task { await model.forgetPasscode() } }
        } message: {
            Text("Unlock stops working until you save it again.")
        }
    }

    private func agentSection(_ s: PCState) -> some View {
        Section {
            Label {
                VStack(alignment: .leading, spacing: 2) {
                    Text(agentTitle(s.agent)).font(.headline)
                    if let detail = agentDetail(s), !detail.isEmpty {
                        Text(detail).font(.subheadline).foregroundStyle(.secondary).lineLimit(2)
                    }
                }
            } icon: {
                if s.agent.state == "driving" {
                    let color: Color = s.agent.phase == "thinking" ? .purple : .blue
                    ZStack {
                        SpinningRing(color: color)
                        RobotView(pose: .pose(phase: s.agent.phase, frame: 0), size: 13, color: color)
                    }
                    .frame(width: 24, height: 24)
                } else {
                    Image(systemName: s.agent.state == "paused" ? "pause.circle.fill" : "person.fill")
                        .font(.system(size: 17, weight: .semibold))
                        .symbolRenderingMode(.hierarchical)
                        .foregroundStyle(s.agent.state == "paused" ? Color.orange : Color.gray)
                }
            }
            .accessibilityIdentifier("agentState")
            .listRowBackground(s.agent.state == "driving" ? AnyView(GlowBackground()) : AnyView(Color(.secondarySystemGroupedBackground)))
            if let pending = s.session?.pending {
                Label(pending.deadline == nil ? "Agent requests control" : "Agent taking control soon",
                      systemImage: "hand.raised.fill")
                    .foregroundStyle(.orange)
                    .accessibilityIdentifier("sessionRequest")
                HStack {
                    Button("Allow") { Task { await model.answerSession(id: pending.id, approve: true) } }
                        .buttonStyle(.borderedProminent)
                        .accessibilityIdentifier("allowSession")
                    Button("Deny", role: .destructive) {
                        Task { await model.answerSession(id: pending.id, approve: false) }
                    }
                    .buttonStyle(.bordered)
                    .accessibilityIdentifier("denySession")
                    if model.busy.contains("session") { Spacer(); ProgressView() }
                }
            }
            if s.agent.state == "driving" {
                Button(role: .destructive) { Task { await model.takeOver() } } label: {
                    BusyRow(title: "Take Over", key: "agent")
                }
                .accessibilityIdentifier("takeover")
            } else if s.agent.state == "paused" {
                Button { Task { await model.resume() } } label: { BusyRow(title: "Resume", key: "agent") }
                    .accessibilityIdentifier("resume")
            }
        }
    }

    /// Who has control, worded as in the Live Activity.
    private func agentTitle(_ a: PCState.Agent) -> String {
        switch a.state {
        case "driving":
            switch a.phase {
            case "acting": return "Agent is tapping"
            case "reading": return "Agent is reading"
            case "thinking": return "Agent is thinking"
            default: return "Agent is working"
            }
        case "paused": return "You took over"
        default: return "You're in control"
        }
    }

    /// What's happening.
    private func agentDetail(_ s: PCState) -> String? {
        switch s.agent.state {
        case "driving":
            let steps = s.agent.steps ?? 0
            let count = steps <= 0 ? nil : steps == 1 ? "1 step" : "\(steps) steps"
            return [s.agent.action, count].compactMap { $0 }.filter { !$0.isEmpty }.joined(separator: " · ")
        case "paused": return "The agent is paused"
        default: return s.mirroring ? "Phone Use is mirroring your screen" : nil
        }
    }

    private func unlockSection(_ s: PCState) -> some View {
        Section {
            if let pending = s.unlock.pending {
                Label("Unlock requested", systemImage: "lock.open").foregroundStyle(.orange)
                HStack {
                    Button("Unlock") { Task { await model.answerUnlock(id: pending.id, approve: true) } }
                        .buttonStyle(.borderedProminent)
                        .accessibilityIdentifier("approveUnlock")
                    Button("Deny", role: .destructive) {
                        Task { await model.answerUnlock(id: pending.id, approve: false) }
                    }
                    .buttonStyle(.bordered)
                    .accessibilityIdentifier("denyUnlock")
                    if model.busy.contains("unlock") { Spacer(); ProgressView() }
                }
            }
            LabeledContent("Passcode") {
                HStack(spacing: 12) {
                    Text(s.unlock.passcodeSaved ? "Saved" : "Not saved")
                    if s.unlock.passcodeSaved {  // rare and destructive: a quiet button, not a row of its own
                        Button("Forget…", role: .destructive) { confirmForget = true }
                            .buttonStyle(.borderless)
                            .foregroundStyle(.red)
                            .accessibilityIdentifier("forgetPasscode")
                    }
                }
            }
            .accessibilityIdentifier("passcodeStatus")
            Button { passcodeSheet = true } label: {
                BusyRow(title: s.unlock.passcodeSaved ? "Replace Passcode" : "Save Passcode", key: "passcode")
            }
            .accessibilityIdentifier("savePasscode")
        } footer: {
            Text("Phone Use types the passcode only after you allow each Unlock.")
        }
    }
}

// MARK: - Bridge

struct BridgeTab: View {
    @EnvironmentObject var model: AppModel
    @State private var confirmForget = false

    private var s: PCState? { model.state }
    private var offline: Bool { !model.reachable || s == nil }

    var body: some View {
        Form {
            if offline { OfflineSection() }
            if let s {
                Group {
                    if let b = s.bridge {
                        bridgeSection(s, b)
                    } else if s.usesPCBluetooth {
                        Section { pcBluetoothRow(s) }
                    } else {
                        Section {
                            RowTitle("No bridge", "Set it up in Phone Use on the PC: Bridge › Set Up Bridge.")
                        }
                    }
                }
                .disabled(offline)
            }
        }
        .toolbar(.hidden, for: .navigationBar)  // the tab bar names the tab
        .refreshable { await model.refresh() }
        .alert("Forget the bridge?", isPresented: $confirmForget) {
            Button("Cancel", role: .cancel) {}
            Button("Forget", role: .destructive) { Task { await model.forgetBridge() } }
        } message: {
            Text("The bridge resets and disconnects from your iPhone. To use it again, hold its lower button for 3 "
                 + "seconds and set it up.")
        }
    }

    /// The PC's own Bluetooth as the iPhone's keyboard and mouse: no bridge in between.
    private func pcBluetoothRow(_ s: PCState) -> some View {
        let connected = s.bluetooth.connected && s.bluetooth.via != "bridge"
        return LabeledContent {
            Text(connected ? "Connected" : "Not connected").foregroundStyle(connected ? Color.green : Color.orange)
        } label: {
            RowTitle("PC's Bluetooth", connected ? s.pc.name : "Settings › Bluetooth › Phone Use")
        }
        .accessibilityIdentifier("pcBluetooth")
    }

    private func bridgeSection(_ s: PCState, _ b: PCState.Bridge) -> some View {
        Section {
            if let w = b.warning {
                Label {
                    VStack(alignment: .leading, spacing: 2) {
                        Text(w.title).font(.headline)
                        if let d = w.detail { Text(d).font(.subheadline).foregroundStyle(.secondary) }
                    }
                } icon: {
                    Image(systemName: "person.crop.circle.badge.exclamationmark.fill")
                        .symbolRenderingMode(.hierarchical)
                }
                .foregroundStyle(.orange)
                .accessibilityIdentifier("bridgeWarning")
            }
            let connected = b.connected ?? (s.bluetooth.connected && s.bluetooth.via == "bridge")
            let standby = b.online && !connected && b.standby == true  // waits for Screen Mirroring
            LabeledContent {
                Text(!b.online ? "Offline" : connected ? "Connected" : standby ? "Waiting for mirroring" : "Not connected")
                    .foregroundStyle(!b.online ? Color.red : connected ? Color.green
                                     : standby ? Color.secondary : Color.orange)
            } label: {
                RowTitle("Bridge \(String((b.id ?? "").suffix(4)))",
                         standby ? "Start Screen Mirroring to connect"
                             : b.online && !connected ? "Settings › Bluetooth › Phone Use Bridge" : b.ip)
            }
            .accessibilityIdentifier("bridgeStatus")
            if let phone = b.signal?.phone { signalRow("Bridge to iPhone", "cellularbars", phone) }
            if let wifi = b.signal?.wifi { signalRow("Bridge Wi-Fi", "wifi", wifi) }
            Toggle(isOn: sendBinding(b.use) { on in await model.setBridge(on) }) {
                BusyRow(title: "Enable Input", key: "bridge")
            }
            .accessibilityIdentifier("bridgeUse")
            NavigationLink {
                NetworksView(current: b.ssid)
            } label: {
                LabeledContent("Wi-Fi Network", value: b.ssid ?? "")
            }
            .accessibilityIdentifier("bridgeNetwork")
            Button("Forget Bridge", role: .destructive) { confirmForget = true }  // like Unpair on the Phone tab
                .accessibilityIdentifier("forgetBridge")
        }
    }

    /// A signal row: the symbol filled to the quality, and the word on the right.
    private func signalRow(_ title: String, _ symbol: String, _ quality: String) -> some View {
        let level: Double = quality == "good" ? 1 : quality == "fair" ? 0.66 : 0.33
        return LabeledContent {
            HStack(spacing: 6) {
                Image(systemName: symbol, variableValue: level)
                Text(quality.capitalized)
            }
            .foregroundStyle(.secondary)
        } label: {
            Text(title)
        }
    }
}

struct PasscodeSheet: View {
    var replace = false
    @EnvironmentObject var model: AppModel
    @Environment(\.dismiss) private var dismiss
    @State private var code = ""

    var body: some View {
        NavigationStack {
            Form {
                SecureField("New Passcode", text: $code)
                    .keyboardType(.numberPad)
                    .textContentType(.oneTimeCode)
                    .accessibilityIdentifier("passcodeField")
            }
            .navigationTitle(replace ? "Replace Passcode" : "Save Passcode")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) { Button("Cancel") { dismiss() } }
                ToolbarItem(placement: .confirmationAction) {
                    Button("Save") {
                        let c = code
                        dismiss()
                        Task { await model.savePasscode(c) }
                    }
                    .disabled(code.count < 4)
                    .accessibilityIdentifier("passcodeSave")
                }
            }
        }
        .presentationDetents([.medium])
    }
}

struct NetworksView: View {
    @EnvironmentObject var model: AppModel
    @Environment(\.dismiss) private var dismiss
    var current: String?
    @State private var networks: [BridgeNetwork]?
    @State private var loadError: String?
    @State private var chosen: BridgeNetwork?
    @State private var password = ""
    @State private var joining: String?

    var body: some View {
        List {
            if let loadError { Label(loadError, systemImage: "exclamationmark.triangle").foregroundStyle(.red) }
            Section {
            if let networks {
                ForEach(networks) { n in
                    Button {
                        password = ""
                        if n.secure { chosen = n } else { Task { await move(n, nil) } }
                    } label: {
                        HStack {
                            Text(n.ssid).foregroundStyle(Color.primary)
                            Spacer()
                            if n.ssid == current { Image(systemName: "checkmark") }
                            if n.secure { Image(systemName: "lock.fill").foregroundStyle(.secondary) }
                            Image(systemName: "wifi", variableValue: strength(n.rssi)).foregroundStyle(.secondary)
                        }
                    }
                    .accessibilityIdentifier("network-\(n.ssid)")
                }
            } else if loadError == nil {
                HStack { Spacer(); ProgressView("Looking for Wi-Fi…"); Spacer() }
            }
            } footer: {
                Text("Choose the same 2.4 GHz network as the PC.")
            }
        }
        .navigationTitle("Choose a Network")
        .toolbar(.visible, for: .navigationBar)  // a page within the tab: its title and Back
        .disabled(joining != nil)
        .overlay {
            if let joining {
                ProgressView("Joining \(joining)…").padding().background(.regularMaterial, in: .rect(cornerRadius: 12))
            }
        }
        .task { await load() }
        .refreshable { await load() }
        .alert(chosen?.ssid ?? "", isPresented: Binding(get: { chosen != nil }, set: { if !$0 { chosen = nil } })) {
            SecureField("Password", text: $password).accessibilityIdentifier("wifiPassword")
            Button("Cancel", role: .cancel) { chosen = nil }
            Button("Join") {
                if let n = chosen { Task { await move(n, password) } }
                chosen = nil
            }
        } message: {
            Text("The bridge's password for this network")
        }
    }

    private func strength(_ rssi: Int) -> Double { min(1, max(0.1, Double(rssi + 90) / 50)) }

    private func load() async {
        do {
            networks = try await model.networks()
            loadError = nil
        } catch {
            loadError = error.localizedDescription
        }
    }

    private func move(_ n: BridgeNetwork, _ password: String?) async {
        joining = n.ssid
        let ok = await model.moveBridge(ssid: n.ssid, password: password)
        joining = nil
        if ok { dismiss() } else { loadError = model.error }
    }
}

/// A soft blue-purple-pink glow that slowly turns around the Agent row's edges while an agent drives, like Apple
/// Intelligence's edge glow: a blurred gradient band with no hard stroke.
struct GlowBackground: View {
    var body: some View {
        TimelineView(.animation) { context in
            let angle = Angle.degrees(context.date.timeIntervalSinceReferenceDate.truncatingRemainder(dividingBy: 6) * 60)
            let gradient = AngularGradient(colors: [.blue, .purple, .pink, .purple, .blue], center: .center, angle: angle)
            ZStack {
                Color(.secondarySystemGroupedBackground)
                RoundedRectangle(cornerRadius: 12)
                    .strokeBorder(gradient, lineWidth: 14)
                    .blur(radius: 14)
                    .opacity(0.55)
            }
        }
    }
}

/// A ring that turns for as long as it's on screen, for the Agent row while an agent drives.
struct SpinningRing: View {
    var color: Color
    @State private var turning = false

    var body: some View {
        Circle()
            .trim(from: 0.1, to: 0.8)
            .stroke(color, style: StrokeStyle(lineWidth: 2.5, lineCap: .round))
            .frame(width: 24, height: 24)
            .rotationEffect(.degrees(turning ? 360 : 0))
            .animation(.linear(duration: 1).repeatForever(autoreverses: false), value: turning)
            .onAppear { turning = true }
    }
}

/// A session request while Phone Use is open. iOS doesn't expand the app's own Live Activity while the app is in
/// front, so the app asks here instead; the sheet closes when the request ends, however it was answered.
struct RequestSheet: View {
    @EnvironmentObject var model: AppModel
    var request: PCState.Pending

    private var deadline: Date? {
        request.deadline.map { Date(timeIntervalSince1970: $0) }.flatMap { $0 > Date() ? $0 : nil }
    }

    var body: some View {
        VStack(spacing: 20) {
            Image(systemName: "hand.raised.fill").font(.system(size: 44, weight: .semibold))
                .symbolRenderingMode(.hierarchical).foregroundStyle(.orange)
            VStack(spacing: 6) {
                if let deadline {
                    HStack(spacing: 6) {
                        Text("Agent taking control in")
                        Text(timerInterval: Date()...deadline, countsDown: true).monospacedDigit()
                    }
                    .font(.title2.bold())
                    if let action = request.action, !action.isEmpty {
                        Text(action).font(.headline).foregroundStyle(.secondary)
                            .multilineTextAlignment(.center)
                    }
                } else {
                    Text("Agent requests control").font(.title2.bold()).multilineTextAlignment(.center)
                    if let action = request.action, !action.isEmpty {
                        Text(action).font(.headline).foregroundStyle(.secondary).multilineTextAlignment(.center)
                    }
                }
                if let name = model.pairing?.name { Text(name).font(.subheadline).foregroundStyle(.secondary) }
            }
            HStack(spacing: 14) {
                Button(role: .destructive) { Task { await model.answerSession(id: request.id, approve: false) } } label: {
                    Text(deadline == nil ? "Deny" : "Cancel").font(.title3.bold()).frame(maxWidth: .infinity, minHeight: 50)
                }
                .buttonStyle(.bordered)
                .tint(.red)
                .accessibilityIdentifier("sheetDeny")
                Button { Task { await model.answerSession(id: request.id, approve: true) } } label: {
                    Text(deadline == nil ? "Allow" : "Allow now").font(.title3.bold())
                        .frame(maxWidth: .infinity, minHeight: 50)
                }
                .buttonStyle(.borderedProminent)
                .tint(.green)
                .accessibilityIdentifier("sheetAllow")
            }
            .disabled(model.busy.contains("session"))
        }
        .padding(24)
    }
}

/// "Agent control" (Always / After a delay / Ask every time) and, for a delay, how long: two ordinary rows.
struct StartModeRow: View {
    @EnvironmentObject var model: AppModel
    var session: PCState.Session

    var body: some View {
        Picker(selection: Binding(
            get: { session.currentMode },
            set: { mode in Task { await model.setSessionMode(mode) } }
        )) {
            Text("Always").tag("auto")
            Text("After a delay").tag("countdown")
            Text("Ask every time").tag("ask")
        } label: {
            Text("Agent control")
        }
        .pickerStyle(.menu)
        .accessibilityIdentifier("sessionMode")
        if session.currentMode == "countdown" {
            let current = session.seconds ?? 10
            Picker(selection: Binding(
                get: { current },
                set: { seconds in Task { await model.setSessionSeconds(seconds) } }
            )) {
                ForEach(Array(Set([5, 10, 15, 30, current])).sorted(), id: \.self) { Text("\($0) seconds").tag($0) }
            } label: {
                Text("Delay")
            }
            .pickerStyle(.menu)
            .accessibilityIdentifier("sessionSeconds")
        }
    }
}

/// One tap installs each shortcut `phoneuse siriai` runs (phoneuse/siriai.py): an iCloud link opens Shortcuts'
/// Add Shortcut sheet. The links were shared from shortcuts/ in the repository, the source of each shortcut;
/// a signed file opened from another website only downloads.
struct AppleIntelligenceSection: View {
    @Environment(\.openURL) private var openURL
    static let shortcuts = [
        ("PhoneUse On-Device", "The model on this iPhone", "https://www.icloud.com/shortcuts/f76f5ae481854d139c6d786c267d69e7"),
        ("PhoneUse Cloud", "Private Cloud Compute", "https://www.icloud.com/shortcuts/82f5af01617e4584bfe01ad3c08816c0"),
        ("PhoneUse Cloud Pro", "Apple's largest cloud model", "https://www.icloud.com/shortcuts/a2e0e65830b24c1ba382379b74154465"),
    ]

    var body: some View {
        Section {
            ForEach(Self.shortcuts, id: \.0) { name, subtitle, link in
                Button {
                    if let url = URL(string: link) { openURL(url) }
                } label: {
                    RowTitle("Add \u{201C}\(name)\u{201D}", subtitle)
                }
                .tint(.primary)
                .accessibilityIdentifier("shortcut-\(name)")
            }
        } header: {
            Text("Apple Intelligence")
        } footer: {
            Text("Agents use these shortcuts to ask Apple's models.")
        }
    }
}
