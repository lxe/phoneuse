using System.Diagnostics;
using System.Numerics;
using System.Runtime.InteropServices;
using SharpGen.Runtime;
using Vortice.D3DCompiler;
using Vortice.Direct3D;
using Vortice.Direct3D11;
using Vortice.DirectComposition;
using Vortice.DXGI;
using Vortice.Mathematics;

namespace PhoneUseMirror;

/// <summary>How the glow looks (Settings › Agent glow): the same knobs as windows/GlowSamples.</summary>
public readonly record struct GlowLook(float Intensity, float Speed, float Size, bool Bleed);

/// <summary>
/// The light around the phone while an agent drives it: a pixel shader (HLSL below, the GLSL of
/// windows/GlowSamples ported) drawn every frame on the GPU into a premultiplied-alpha swap chain.
///
/// It has a window of its own, not the mirror's: the halo reaches well past the phone, and the
/// mirror window is only the phone (its see-through parts still take clicks). This one is a
/// borderless popup owned by the mirror window (so always just above it, minimized with it),
/// click-through (layered + transparent), never activated, and shown through DirectComposition
/// (no redirection bitmap), so the desktop shows wherever the shader writes nothing.
///
/// D3D runs on a thread of its own; while nothing is lit it hides the window and sleeps.
/// </summary>
public sealed unsafe class AgentGlow : IDisposable
{
    readonly IntPtr _owner;
    IntPtr _hwnd;
    readonly Thread _thread;
    readonly AutoResetEvent _wake = new(false);
    readonly object _lock = new();
    volatile bool _quit;

    // --- set from the UI thread (under _lock) ---
    GlowLook _look = AppSettings.DefaultGlowLook;
    string _agent = "idle";
    bool _enabled = true, _preview, _hostVisible = true;
    // the phone, in screen pixels: its outer edge and the screen's
    double _ox, _oy, _ow, _oh, _or, _sw, _sh, _sr;
    bool _haveShape;
    readonly Vector4[] _taps = new Vector4[4];
    int _tapNext;

    /// <summary>False when the GPU or DirectComposition couldn't start: the mirror shows its plain ring instead.</summary>
    public bool Available { get; private set; } = true;
    public event Action? AvailabilityChanged;

    public AgentGlow(IntPtr owner)
    {
        _owner = owner;
        _hwnd = CreateGlowWindow(owner);
        if (_hwnd == IntPtr.Zero) Available = false;
        _thread = new Thread(Run) { IsBackground = true, Name = "agent glow" };
        if (Available) _thread.Start();
    }

    // ---------------------------------------------------------------- the public side (UI thread)

    public GlowLook Look
    {
        get { lock (_lock) return _look; }
        set { lock (_lock) _look = value; Place(); _wake.Set(); }
    }

    public bool Enabled
    {
        get { lock (_lock) return _enabled; }
        set { lock (_lock) _enabled = value; _wake.Set(); }
    }

    /// <summary>Lit while the Agent Control settings tab is open, agent or not, to see the look being chosen.</summary>
    public bool Preview
    {
        set { lock (_lock) _preview = value; _wake.Set(); }
    }

    /// <summary>"driving", "paused" (you took over: amber) or "idle" (off).</summary>
    public void SetAgent(string agent)
    {
        lock (_lock) _agent = agent;
        _wake.Set();
    }

    /// <summary>The mirror window is shown and not minimized.</summary>
    public void SetHostVisible(bool visible)
    {
        lock (_lock) _hostVisible = visible;
        _wake.Set();
    }

    /// <summary>The agent pressed at (x, y), fractions of the screen.</summary>
    public void Tap(double x, double y)
    {
        lock (_lock)
        {
            _taps[_tapNext] = new Vector4((float)x, (float)y, Now, 1);
            _tapNext = (_tapNext + 1) % _taps.Length;
        }
    }

    /// <summary>The phone's outer edge (the titanium band's) and its screen, in screen pixels, with their corner radii.</summary>
    public void SetShape(double ox, double oy, double ow, double oh, double or, double sw, double sh, double sr)
    {
        lock (_lock)
        {
            if (_haveShape && ox == _ox && oy == _oy && ow == _ow && oh == _oh && or == _or && sw == _sw && sh == _sh && sr == _sr) return;
            (_ox, _oy, _ow, _oh, _or, _sw, _sh, _sr) = (ox, oy, ow, oh, or, sw, sh, sr);
            _haveShape = true;
        }
        Place();
        _wake.Set();
    }

    public void Dispose()
    {
        _quit = true;
        _wake.Set();
        if (_thread.IsAlive) _thread.Join(1000);
        if (_hwnd != IntPtr.Zero) { DestroyWindow(_hwnd); _hwnd = IntPtr.Zero; }
    }

    // ---------------------------------------------------------------- geometry

    // The phone in shader units: 300 across its short side, whatever its size on screen.
    double Unit => Math.Max(1, Math.Min(_ow, _oh)) / 300.0;

    /// <summary>Room around the phone for the halo, in pixels: it fades out before the window's edge.</summary>
    double MarginPx => (60 + 50 * _look.Size) * Unit;

    int _winX, _winY, _winW, _winH;  // the glow window, screen pixels (read by the render thread)

    void Place()
    {
        int x, y, w, h;
        lock (_lock)
        {
            if (!_haveShape) return;
            double m = MarginPx;
            x = (int)Math.Floor(_ox - m); y = (int)Math.Floor(_oy - m);
            w = (int)Math.Ceiling(_ow + 2 * m); h = (int)Math.Ceiling(_oh + 2 * m);
            if (x == _winX && y == _winY && w == _winW && h == _winH) return;
            (_winX, _winY, _winW, _winH) = (x, y, w, h);
        }
        const uint NOZORDER = 0x4, NOACTIVATE = 0x10, ASYNC = 0x4000;
        if (_hwnd != IntPtr.Zero) SetWindowPos(_hwnd, IntPtr.Zero, x, y, w, h, NOZORDER | NOACTIVATE | ASYNC);
    }

    // ---------------------------------------------------------------- the render thread

    static readonly Stopwatch Clock = Stopwatch.StartNew();
    static float Now => (float)Clock.Elapsed.TotalSeconds;

    ID3D11Device? _device;
    ID3D11DeviceContext? _context;
    IDCompositionDevice? _dcomp;
    IDCompositionTarget? _target;
    IDCompositionVisual? _visual;
    IDXGISwapChain1? _swap;
    ID3D11VertexShader? _vs;
    ID3D11PixelShader? _ps;
    ID3D11Buffer? _cb;
    int _swapW, _swapH;

    void Run()
    {
        try { Start(); }
        catch (Exception e)
        {
            Debug.WriteLine("glow: can't start: " + e);
            Available = false;
            AvailabilityChanged?.Invoke();
            return;
        }

        float level = 0, paused = 0, igniteAt = -10, last = Now, nextFake = 0;
        bool shown = false;
        var rng = new Random();
        // Reuse this storage: stackalloc inside the frame loop grows the stack until Run returns.
        Span<Vector4> tp = stackalloc Vector4[4];
        while (!_quit)
        {
            GlowLook look; string agent; bool on, preview; Vector4[] taps;
            double ow, oh, or, sw, sh, sr, offX, offY; int ww, wh;
            lock (_lock)
            {
                look = _look; agent = _agent; preview = _preview;
                on = _enabled && _hostVisible && _haveShape && (agent != "idle" || preview);
                (ow, oh, or, sw, sh, sr) = (_ow, _oh, _or, _sw, _sh, _sr);
                offX = _ox - _winX; offY = _oy - _winY;  // the phone's corner in the window
                ww = _winW; wh = _winH;
                taps = (Vector4[])_taps.Clone();
            }
            float t = Now, dt = Math.Min(t - last, 0.1f);
            last = t;

            if (!on && level < 0.003f)
            {
                level = 0;
                if (shown) { ShowWindowAsync(_hwnd, 0 /*SW_HIDE*/); shown = false; }
                _wake.WaitOne();
                last = Now;
                continue;
            }
            if (on && level < 0.003f && !shown) igniteAt = t;  // lighting up from dark: the sweep
            level += ((on ? 1 : 0) - level) * (1 - MathF.Exp(-dt * (on ? 8 : 4)));
            paused += ((agent == "paused" ? 1 : 0) - paused) * (1 - MathF.Exp(-dt * 5));

            // Settings open with no agent: pretend taps, to show what they look like
            if (preview && agent == "idle" && t > nextFake)
            {
                lock (_lock)
                {
                    _taps[_tapNext] = new Vector4(0.15f + 0.7f * rng.NextSingle(), 0.15f + 0.7f * rng.NextSingle(), t, 1);
                    _tapNext = (_tapNext + 1) % _taps.Length;
                }
                nextFake = t + 1.2f + 1.4f * rng.NextSingle();
            }

            if (ww <= 0 || wh <= 0) { _wake.WaitOne(50); continue; }
            double unit = Math.Max(1, Math.Min(ow, oh)) / 300.0;
            double margin = (60 + 50 * look.Size) * unit;
            float ig = Math.Clamp((t - igniteAt) / 1.1f, 0, 1);
            var c = new Consts
            {
                C = new Vector2((float)(offX + ow / 2), (float)(offY + oh / 2)),
                Px = (float)unit,
                R = (float)(or / unit),
                Half = new Vector2((float)(ow / 2 / unit), (float)(oh / 2 / unit)),
                SHalf = new Vector2((float)(sw / 2 / unit), (float)(sh / 2 / unit)),
                SR = (float)(sr / unit),
                Time = t,
                Level = level,
                Ignite = 1 - MathF.Pow(1 - ig, 3),
                Paused = paused,
                Intensity = look.Intensity,
                Speed = look.Speed,
                Size = look.Size,
                Bleed = look.Bleed ? 1 : 0,
                Margin = (float)margin,
            };
            // taps: fractions of the screen → units from the phone's centre (y up)
            for (int i = 0; i < 4; i++)
            {
                var a = taps[i];
                tp[i] = a.W <= 0 ? default
                    : new Vector4((float)((a.X - 0.5) * sw / unit), (float)(-(a.Y - 0.5) * sh / unit), a.Z, a.W);
            }
            c.Tap0 = tp[0]; c.Tap1 = tp[1]; c.Tap2 = tp[2]; c.Tap3 = tp[3];

            try
            {
                // Buffer creation/resizing can fail too; keep it inside the recovery path.
                EnsureSwapChain(ww, wh);
                c.Res = new Vector2(_swapW, _swapH);
                Draw(ref c);
                if (!shown) { ShowWindowAsync(_hwnd, 4 /*SW_SHOWNOACTIVATE*/); shown = true; }
                _swap!.Present(1, PresentFlags.None);  // waits for the next vsync: 60 fps on a 60 Hz screen
            }
            catch (Exception e)
            {
                Debug.WriteLine("glow: " + e.Message);
                ReleaseSwapChain();
                _wake.WaitOne(500);
            }
        }
        ReleaseSwapChain();
    }

    void Start()
    {
        var levels = new[] { FeatureLevel.Level_11_1, FeatureLevel.Level_11_0, FeatureLevel.Level_10_1, FeatureLevel.Level_10_0 };
        const uint BgraSupport = 0x20;
        Marshal.ThrowExceptionForHR(D3D11CreateDevice(IntPtr.Zero, 1 /*HARDWARE*/, IntPtr.Zero, BgraSupport,
            levels, (uint)levels.Length, 7, out var dev, out _, out var ctx));
        _device = new ID3D11Device(dev);
        _context = new ID3D11DeviceContext(ctx);

        _vs = _device.CreateVertexShader(Compile("VS", "vs_4_0"));
        _ps = _device.CreatePixelShader(Compile("PS", "ps_4_0"));
        _cb = _device.CreateBuffer(new BufferDescription((uint)sizeof(Consts), BindFlags.ConstantBuffer, ResourceUsage.Default));

        using var dxgi = _device.QueryInterface<IDXGIDevice>();
        _dcomp = DComp.DCompositionCreateDevice<IDCompositionDevice>(dxgi);
        _dcomp.CreateTargetForHwnd(_hwnd, true, out _target).CheckError();
        _dcomp.CreateVisual(out _visual).CheckError();
        _target!.SetRoot(_visual);
    }

    // compiled once, at start, by the d3dcompiler_47.dll every Windows 10 and 11 has
    static byte[] Compile(string entry, string profile) =>
        Compiler.Compile(ContinuousCorner.Hlsl() + Hlsl, entry, "AgentGlow.hlsl", profile, ShaderFlags.OptimizationLevel3).ToArray();

    void EnsureSwapChain(int w, int h)
    {
        if (_swap == null)
        {
            using var dxgi = _device!.QueryInterface<IDXGIDevice>();
            using var adapter = dxgi.GetAdapter();
            using var factory = adapter.GetParent<IDXGIFactory2>();
            _swap = factory.CreateSwapChainForComposition(_device, new SwapChainDescription1
            {
                Width = (uint)w,
                Height = (uint)h,
                Format = Format.B8G8R8A8_UNorm,
                SampleDescription = new SampleDescription(1, 0),
                BufferUsage = Usage.RenderTargetOutput,
                BufferCount = 2,
                Scaling = Scaling.Stretch,
                SwapEffect = SwapEffect.FlipSequential,
                AlphaMode = AlphaMode.Premultiplied,  // see-through wherever the shader writes 0
            }, null);
            _visual!.SetContent(_swap);
            _dcomp!.Commit();
            _swapW = w; _swapH = h;
        }
        else if (w != _swapW || h != _swapH)
        {
            // The immediate context retains its own reference to the last bound view.
            // Disposing the managed view alone doesn't release the back buffer for resizing.
            _context!.ClearState();
            _swap.ResizeBuffers(2, (uint)w, (uint)h, Format.B8G8R8A8_UNorm, SwapChainFlags.None).CheckError();
            _swapW = w; _swapH = h;
        }
    }

    void ReleaseSwapChain()
    {
        if (_swap == null) return;
        _context?.ClearState();
        try { _visual?.SetContent(null); _dcomp?.Commit(); } catch (SharpGenException) { }
        _swap.Dispose();
        _swap = null;
    }

    void Draw(ref Consts c)
    {
        using var back = _swap!.GetBuffer<ID3D11Texture2D>(0);
        using var rtv = _device!.CreateRenderTargetView(back);
        var ctx = _context!;
        ctx.ClearRenderTargetView(rtv, new Color4(0, 0, 0, 0));
        ctx.UpdateSubresource(in c, _cb!);
        ctx.OMSetRenderTargets(rtv);
        ctx.RSSetViewport(new Viewport(0, 0, _swapW, _swapH, 0, 1));
        ctx.IASetPrimitiveTopology(PrimitiveTopology.TriangleList);
        ctx.VSSetShader(_vs);
        ctx.PSSetShader(_ps);
        ctx.PSSetConstantBuffer(0, _cb);
        ctx.Draw(3, 0);
    }

    [StructLayout(LayoutKind.Sequential)]
    struct Consts
    {
        public Vector2 C; public float Px, R;
        public Vector2 Half, SHalf;
        public float SR, Time, Reserved, Level;
        public float Ignite, Paused, Intensity, Speed;
        public float Size, Bleed; public Vector2 Res;
        public float Margin, Pad0, Pad1, Pad2;
        public Vector4 Tap0, Tap1, Tap2, Tap3;
    }

    // ---------------------------------------------------------------- the shader

    const string Hlsl = """
cbuffer Glow : register(b0)
{
    float2 C; float Px; float R;         // phone centre (window px, y down), px per unit, outer corner radius
    float2 Half; float2 SHalf;           // outer and screen half sizes (units: 300 across the short side)
    float SR; float Time; float Reserved; float Level;
    float Ignite; float Paused; float Intensity; float Speed;
    float Size; float Bleed; float2 Res;
    float Margin; float3 Pad;
    float4 Taps[4];                      // x, y (units from the centre, y up), start time, strength
};

#define TAU 6.28318530718

// HLSL pow returns NaN for a negative base, even for an exponent of two.
float square(float x) { return x * x; }

float4 VS(uint id : SV_VertexID) : SV_Position
{
    float2 uv = float2((id << 1) & 2, id & 2);
    return float4(uv * float2(2, -2) + float2(-1, 1), 0, 1);
}

// The phone's corners are iOS's continuous corners, as MainWindow.Squircle draws them (sdContinuous,
// prepended from ContinuousCorner.Hlsl).
float sdSquircle(float2 p, float2 b, float r) { return sdContinuous(p, b, r); }

// Smooth angular position, 0..1, counter-clockwise from the right-hand middle.
float perim(float2 p, float2 hb)
{
    // No nearest-edge or bounding-box projection: both leave diagonal derivative seams.
    float2 v = p / hb;
    if (dot(v, v) < 1e-10) return 0;
    return frac(atan2(v.y, v.x) / TAU + 1);
}

float screenGlowDistance(float2 p, float2 hb, float r)
{
    // The exact mask's max(q.x,q.y) has a crease on the interior corner bisectors.
    // Use a smooth maximum for the soft glow, with a corner profile close to the
    // squircle. Retain the exact distance for the rim and tap mask.
    float2 q = abs(p) - hb + r;
    float k = max(r * 0.3, 1);
    return min(max(q.x, q.y) + k * log(1 + exp(-abs(q.x - q.y) / k)) - r, 0);
}

// Siri-like blue, violet, pink, orange; periodic.
float3 siri(float x)
{
    x = frac(x) * 4;
    float3 c0 = float3(0.05, 0.55, 1.00), c1 = float3(0.68, 0.33, 1.00),
           c2 = float3(1.00, 0.24, 0.50), c3 = float3(1.00, 0.58, 0.14);
    float f = frac(x); f = f * f * (3 - 2 * f);
    if (x < 1) return lerp(c0, c1, f);
    if (x < 2) return lerp(c1, c2, f);
    if (x < 3) return lerp(c2, c3, f);
    return lerp(c3, c0, f);
}
float3 amber(float x) { return lerp(float3(1.0, 0.42, 0.04), float3(1.0, 0.78, 0.30), 0.5 + 0.5 * sin(x * TAU)); }

float pn(float u, float t, float s)
{
    return 0.5 * sin(TAU * 3 * u + t * 1.3 + s)
         + 0.3 * sin(TAU * 5 * u - t * 0.9 + s * 2.1)
         + 0.2 * sin(TAU * 9 * u + t * 2.3 + s * 3.7);
}

float4 PS(float4 pos : SV_Position) : SV_Target
{
    float2 p = (pos.xy - C) / Px;
    p.y = -p.y;
    float t = Time * Speed;
    float d  = sdSquircle(p, Half, R);    // > 0 outside the phone
    float ds = sdSquircle(p, SHalf, SR);  // < 0 on the screen
    // Tap ripples cross the whole screen; a depth cutoff here clips them into a box.
    float u = perim(p, Half);

    // Aurora
    float3 col = siri(u + t * 0.05 + 0.08 * pn(u, t, 0));
    float amp = 0.62 + 0.38 * pn(u, t * 0.8, 5) + 0.08 * sin(t * 1.7);

    // you took over: warm, slower, dimmer
    col = lerp(col, amber(u + t * 0.05), Paused);
    amp *= lerp(1, 0.5 + 0.12 * sin(t * 2), Paused);

    // taps: a ring runs out across the screen; where it meets the frame, the edge flares
    float ring = 0, flare = 0, dt = 0; float3 tapCol = 0;
    [unroll] for (int i = 0; i < 4; i++)
    {
        float4 tp = Taps[i];
        float age = Time - tp.z;
        if (age < 0 || age > 1.8 || tp.w <= 0) continue;
        float life = 1 - age / 1.8; life *= life;
        float r = age * 360, dist = length(p - tp.xy);
        float rg = exp(-square((dist - r) / 7)) * life * tp.w;
        ring += rg;
        flare += exp(-square((dist - r) / 55)) * life * tp.w;
        float dd = exp(-dist / 16) * exp(-age * 6) * tp.w;
        dt += dd;
        tapCol += siri(tp.z * 0.37 + dist / 500) * (rg + dd);
    }
    amp += flare * 0.9;

    // lighting up: the light runs up both sides from the bottom centre
    float du = abs(frac(u - 0.75 + 0.5) - 0.5);
    float front = Ignite * 0.56;
    float reveal = 1 - smoothstep(front - 0.07, front, du);
    float head = Ignite < 1 ? exp(-square((du - front) / 0.018)) * (1 - Ignite * Ignite) * 1.6 : 0;

    float E = (amp * reveal + head) * Level * Intensity;

    float I = 0;
    float rim = exp(-abs(d + 0.9) / 0.9);
    if (d > 0)
    {
        float w1 = 3 * Size * (0.5 + amp);
        float w2 = 20 * Size * (0.35 + amp);
        I = exp(-d / w1) * 0.85 + 0.6 / (1 + (d / w2) * (d / w2));
        I *= smoothstep(0, 1.2, d) * 0.6 + 0.4;
        // fade out before the window's edge
        float edge = min(min(pos.x, pos.y), min(Res.x - pos.x, Res.y - pos.y));
        I *= smoothstep(0, Margin * 0.7, edge);
    }
    I += rim * 1.1;
    float glowDs = screenGlowDistance(p, SHalf, SR * CORNER_EXTENT);  // its rounding reaches as far as the corner's
    if (ds < 0) I += (exp(glowDs / (9 * Size * (0.4 + amp))) * 0.6 + exp(ds / 1.6) * 0.35) * Bleed;
    I *= E;

    float3 c = lerp(col, 1, clamp(0.55 * rim + 0.2 * exp(-max(d, 0) / 1.5), 0, 0.9));

    float onScreen = 1 - smoothstep(-1, 0, ds);
    float tapI = (ring * 0.55 + dt * 0.5) * onScreen * Level * (1 - Paused) * Intensity;
    if (ring + dt > 0) tapCol /= (ring + dt);
    c = (c * I + lerp(tapCol, 1, 0.35) * tapI) / max(I + tapI, 1e-4);
    I += tapI;

    float a = saturate(I);
    return float4(min(c * min(I, 1.4), 1), a);  // premultiplied
}
""";

    // ---------------------------------------------------------------- the window

    static WndProc? _proc;  // kept alive: Windows calls it
    static bool _registered;

    static IntPtr CreateGlowWindow(IntPtr owner)
    {
        var hinst = GetModuleHandle(null);
        if (!_registered)
        {
            _proc = (h, m, w, l) => m == 0x84 /*WM_NCHITTEST*/ ? -1 /*HTTRANSPARENT*/ : DefWindowProc(h, m, w, l);
            var wc = new WNDCLASSEX
            {
                cbSize = (uint)Marshal.SizeOf<WNDCLASSEX>(),
                lpfnWndProc = Marshal.GetFunctionPointerForDelegate(_proc),
                hInstance = hinst,
                lpszClassName = "PhoneUseAgentGlow",
            };
            if (RegisterClassEx(ref wc) == 0) return IntPtr.Zero;
            _registered = true;
        }
        const uint WS_POPUP = 0x80000000;
        const uint WS_EX_TRANSPARENT = 0x20, WS_EX_TOOLWINDOW = 0x80, WS_EX_LAYERED = 0x80000,
                   WS_EX_NOREDIRECTIONBITMAP = 0x200000, WS_EX_NOACTIVATE = 0x08000000;
        var hwnd = CreateWindowEx(WS_EX_TRANSPARENT | WS_EX_TOOLWINDOW | WS_EX_LAYERED | WS_EX_NOREDIRECTIONBITMAP | WS_EX_NOACTIVATE,
            "PhoneUseAgentGlow", "", WS_POPUP, 0, 0, 1, 1, owner, IntPtr.Zero, hinst, IntPtr.Zero);
        if (hwnd != IntPtr.Zero) SetLayeredWindowAttributes(hwnd, 0, 255, 2 /*LWA_ALPHA*/);
        return hwnd;
    }

    delegate IntPtr WndProc(IntPtr hwnd, uint msg, IntPtr wParam, IntPtr lParam);

    [StructLayout(LayoutKind.Sequential, CharSet = CharSet.Unicode)]
    struct WNDCLASSEX
    {
        public uint cbSize, style;
        public IntPtr lpfnWndProc;
        public int cbClsExtra, cbWndExtra;
        public IntPtr hInstance, hIcon, hCursor, hbrBackground;
        public string? lpszMenuName, lpszClassName;
        public IntPtr hIconSm;
    }

    [DllImport("user32.dll", CharSet = CharSet.Unicode)] static extern ushort RegisterClassEx(ref WNDCLASSEX wc);
    [DllImport("user32.dll", CharSet = CharSet.Unicode)]
    static extern IntPtr CreateWindowEx(uint exStyle, string cls, string name, uint style, int x, int y, int w, int h,
        IntPtr parent, IntPtr menu, IntPtr inst, IntPtr param);
    [DllImport("user32.dll")] static extern IntPtr DefWindowProc(IntPtr hwnd, uint msg, IntPtr w, IntPtr l);
    [DllImport("user32.dll")] static extern bool DestroyWindow(IntPtr hwnd);
    [DllImport("user32.dll")] static extern bool ShowWindowAsync(IntPtr hwnd, int cmd);
    [DllImport("user32.dll")] static extern bool SetWindowPos(IntPtr hwnd, IntPtr after, int x, int y, int cx, int cy, uint flags);
    [DllImport("user32.dll")] static extern bool SetLayeredWindowAttributes(IntPtr hwnd, uint key, byte alpha, uint flags);
    [DllImport("kernel32.dll", CharSet = CharSet.Unicode)] static extern IntPtr GetModuleHandle(string? name);
    [DllImport("d3d11.dll")]
    static extern int D3D11CreateDevice(IntPtr adapter, int driverType, IntPtr software, uint flags,
        FeatureLevel[] levels, uint count, uint sdkVersion, out IntPtr device, out FeatureLevel level, out IntPtr context);
}
