import Foundation

/// GET /api/app: everything the window shows, in one call (phoneuse/web.py, app_state). Every field is optional
/// so an older or newer core still decodes.
struct AppState: Decodable {
    var running: Bool?
    var name: String?
    var picture: Picture?
    var bridge: BridgeState?
    var session: Session?
    var awake: OnOff?
    var passcode: Passcode?
    var claude: Claude?
    var firewall: String?
    var confirmed: Bool?
    var paired: Int?
    var hero: Hero?
    var requests: Requests?
}

/// What waits for the owner: an agent asking for the phone, or asking to type the passcode.
struct Requests: Decodable {
    var session: SessionRequest?
    var unlock: UnlockRequest?
}

struct SessionRequest: Decodable, Equatable {
    var id: String
    var action: String?
    var mode: String?
    var deadline: Double?
}

struct UnlockRequest: Decodable, Equatable {
    var id: String
}

struct Picture: Decodable {
    var live: Bool?
    var stale: Bool?
    var paused: Bool?
}

struct BridgeState: Decodable {
    var saved: SavedBridge?
    var setup: Bool?
    var status: BridgeStatus?
}

struct SavedBridge: Decodable {
    var id: String
    var ip: String?
    var ssid: String?
    var use: Bool?
}

struct BridgeStatus: Decodable {
    var error: String?
    var connected: Bool?
    var phone: String?
}

struct Session: Decodable {
    var mode: String?
    var seconds: Int?
}

struct OnOff: Decodable { var on: Bool? }
/// GET /api/passcode. `enabled`: whether the PC offers Unlock at all (off for now, phoneuse/unlock.py ENABLED);
/// the passcode settings show only while it's true.
struct Passcode: Decodable { var saved: Bool?; var enabled: Bool? }

struct Claude: Decodable {
    var claude: Bool?
    var skill: Bool?
    var command: Bool?
    var connected: Bool { (skill ?? false) && (command ?? false) }
}

/// The next thing to do, or the iPhone (phoneuse/overview.py).
struct Hero: Decodable, Equatable {
    var step: String
    var title: String
    var detail: String?
    var action: String?
    var icon: String?
    var good: Bool?
    var quiet: Bool?
    var bold: String?
    var qr: String?       // "pair": show the pairing QR code under the title, in place of the icon
    var steps: [String]?  // a numbered list under the title; "{mirroring}" stands for the Screen Mirroring glyph

    /// overview.stopped(): the API can't say this itself.
    static let stopped = Hero(step: "stopped", title: "Phone Use is stopped",
                              detail: "Start it to see and use your iPhone here.", action: "start")
    static let starting = Hero(step: "starting", title: "Starting…")
}

/// Agent control, in the iOS app's words.
enum AgentMode: String, CaseIterable, Identifiable {
    case auto, countdown, ask
    var id: String { rawValue }
    var title: String {
        switch self {
        case .auto: "Always"
        case .countdown: "After a delay"
        case .ask: "Ask every time"
        }
    }
}

let agentDelays = [5, 10, 15, 30]
