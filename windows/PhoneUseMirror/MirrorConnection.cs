using System.Net.Http;
using System.Net.WebSockets;
using System.Text;
using System.Text.Json;
using System.Text.Json.Nodes;

namespace PhoneUseMirror;

/// <summary>
/// The browser mirror's WebSocket (phoneuse/web.py, /ws), spoken natively.
/// In: binary messages are one H.264 access unit (Annex B) after a byte that is 1
/// for a keyframe; text messages are JSON ("state", "agent-cursor", "agent-action",
/// "unlock-request", "session-request", ...). Out: JSON input events
/// ({t: "move" | "down" | "up" | "scroll" | "key" | "mod" | "consumer" | "keyframe"
/// | "playing" | "blur"}). Reconnects every second while phoneuse isn't up.
/// </summary>
public sealed class MirrorConnection : IDisposable
{
    readonly Uri _ws;
    readonly HttpClient _http;
    readonly CancellationTokenSource _stop = new();
    ClientWebSocket? _sock;
    readonly SemaphoreSlim _sendLock = new(1, 1);

    public event Action<bool, byte[]>? Frame;          // keyframe?, access unit
    public event Action<JsonObject>? Message;          // any JSON message
    public event Action<bool>? ConnectedChanged;
    public bool Connected { get; private set; }

    public MirrorConnection(int port)
    {
        _ws = new Uri($"ws://127.0.0.1:{port}/ws");
        _http = new HttpClient { BaseAddress = new Uri($"http://127.0.0.1:{port}/"), Timeout = Timeout.InfiniteTimeSpan };
        // The page's own origin, as the browser mirror sends: the server refuses changes (the
        // bridge's routes among them) from any other origin, which only a web page could fake
        _http.DefaultRequestHeaders.TryAddWithoutValidation("Origin", $"http://127.0.0.1:{port}");
    }

    public void Start() => _ = Task.Run(RunAsync);

    async Task RunAsync()
    {
        while (!_stop.IsCancellationRequested)
        {
            using var sock = new ClientWebSocket();
            sock.Options.KeepAliveInterval = TimeSpan.FromSeconds(20);
            try
            {
                await sock.ConnectAsync(_ws, _stop.Token);
                _sock = sock;
                SetConnected(true);
                await ReceiveLoop(sock);
            }
            catch (Exception) when (!_stop.IsCancellationRequested) { }
            catch (OperationCanceledException) { return; }
            finally
            {
                _sock = null;
                SetConnected(false);
            }
            try { await Task.Delay(1000, _stop.Token); } catch (OperationCanceledException) { return; }
        }
    }

    void SetConnected(bool on)
    {
        if (Connected == on) return;
        Connected = on;
        ConnectedChanged?.Invoke(on);
    }

    async Task ReceiveLoop(ClientWebSocket sock)
    {
        var buf = new byte[1 << 16];
        using var msg = new MemoryStream();
        while (sock.State == WebSocketState.Open)
        {
            msg.SetLength(0);
            WebSocketReceiveResult r;
            do
            {
                r = await sock.ReceiveAsync(buf, _stop.Token);
                if (r.MessageType == WebSocketMessageType.Close) return;
                msg.Write(buf, 0, r.Count);
            } while (!r.EndOfMessage);

            if (r.MessageType == WebSocketMessageType.Binary)
            {
                if (msg.Length < 2) continue;
                var all = msg.GetBuffer();
                var data = new byte[msg.Length - 1];
                Buffer.BlockCopy(all, 1, data, 0, data.Length);
                Frame?.Invoke(all[0] == 1, data);
            }
            else
            {
                JsonObject? obj = null;
                try { obj = JsonNode.Parse(msg.GetBuffer().AsSpan(0, (int)msg.Length)) as JsonObject; } catch (JsonException) { }
                if (obj != null) Message?.Invoke(obj);
            }
        }
    }

    /// <summary>Send one input event; dropped while not connected (as the page does).</summary>
    public void Send(object msg)
    {
        var sock = _sock;
        if (sock == null || sock.State != WebSocketState.Open) return;
        var bytes = JsonSerializer.SerializeToUtf8Bytes(msg);
        _ = SendAsync(sock, bytes);
    }

    async Task SendAsync(ClientWebSocket sock, byte[] bytes)
    {
        await _sendLock.WaitAsync();
        try { await sock.SendAsync(bytes, WebSocketMessageType.Text, true, _stop.Token); }
        catch (Exception) { }
        finally { _sendLock.Release(); }
    }

    /// <summary>POST JSON to the server's API (/api/...); returns the reply, or null on failure.</summary>
    public Task<JsonObject?> PostAsync(string path, object body) => CallAsync(HttpMethod.Post, path, body);
    public Task<JsonObject?> GetAsync(string path) => CallAsync(HttpMethod.Get, path, null);
    public Task<JsonObject?> DeleteAsync(string path) => CallAsync(HttpMethod.Delete, path, null);

    /// <summary>The bridge's setup steps wait on Bluetooth: they get longer than other calls.</summary>
    public async Task<JsonObject?> CallAsync(HttpMethod method, string path, object? body, int timeoutSeconds = 90)
    {
        try
        {
            using var req = new HttpRequestMessage(method, path.TrimStart('/'));
            if (body != null)
                req.Content = new StringContent(JsonSerializer.Serialize(body), Encoding.UTF8, "application/json");
            using var cts = new CancellationTokenSource(TimeSpan.FromSeconds(timeoutSeconds));
            using var r = await _http.SendAsync(req, cts.Token);
            var text = await r.Content.ReadAsStringAsync();
            return JsonNode.Parse(text) as JsonObject ?? new JsonObject();
        }
        catch (Exception e) { return new JsonObject { ["error"] = "phoneuse didn't answer (" + e.Message + ")", ["_failed"] = true }; }
    }

    public Uri PageUri => _http.BaseAddress!;

    public void Dispose()
    {
        _stop.Cancel();
        _sock?.Abort();
        _http.Dispose();
    }
}
