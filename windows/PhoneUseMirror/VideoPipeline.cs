using System.Collections.Concurrent;
using System.Diagnostics;
using System.Numerics;
using System.Runtime.CompilerServices;
using System.Runtime.InteropServices;
using Microsoft.UI.Dispatching;
using Microsoft.UI.Xaml.Controls;
using SharpGen.Runtime;
using Vortice.Direct3D;
using Vortice.Direct3D11;
using Vortice.DXGI;
using Vortice.MediaFoundation;

namespace PhoneUseMirror;

/// <summary>
/// The phone's H.264 stream, decoded and shown with as little delay as Windows allows:
///
///   access unit â”€â–º Media Foundation's H.264 decoder (low-latency mode, on the GPU
///   through D3D11) â”€â–º NV12 texture â”€â–º D3D11 video processor (NV12 â†’ BGRA and scaling
///   in one pass) â”€â–º composition swap chain in a SwapChainPanel â”€â–º Present
///
/// No player, no clock, no buffering: a frame is presented the moment the decoder
/// returns it. (MediaPlayer kept several frames in the decoder and its renderer,
/// and the phone sends frames only when its screen changes, so the picture lagged.)
///
/// Everything D3D and MF runs on one thread of its own. Every frame after a keyframe
/// needs all the frames before it, so decoding starts at a keyframe; when it falls
/// behind, it drops the backlog and asks for a fresh keyframe.
/// </summary>
public sealed unsafe class VideoPipeline : IDisposable
{
    static readonly Guid MFVideoFormat_H264 = new("34363248-0000-0010-8000-00AA00389B71");
    static readonly Guid MFVideoFormat_NV12 = new("3231564E-0000-0010-8000-00AA00389B71");
    static readonly Guid CODECAPI_AVLowLatencyMode = new("9c27891a-ed7a-40e1-88e8-b22727a024ee");
    static readonly Guid CLSID_MSH264DecoderMFT = new("62CE7E72-4C71-4d20-B15D-452831A87D9D");
    static readonly Guid MF_SA_D3D11_AWARE = new("206b4fc8-fcf9-4c51-afe3-9764369e33a0");
    static readonly Guid IID_ISwapChainPanelNative = new("63aad0b8-7c24-40ff-85a8-640d944cc325");
    const int MF_E_NOTACCEPTING = unchecked((int)0xC00D36B5);
    const int MF_E_TRANSFORM_NEED_MORE_INPUT = unchecked((int)0xC00D6D72);
    const int MF_E_TRANSFORM_STREAM_CHANGE = unchecked((int)0xC00D6D61);
    const int MaxQueued = 6;

    enum Kind { Frame, Reset, Stop, Resize, Quit }
    readonly record struct Item(Kind Kind, bool Key = false, byte[]? Data = null);

    readonly BlockingCollection<Item> _in = new(new ConcurrentQueue<Item>());
    readonly SwapChainPanel _panel;
    readonly DispatcherQueue _ui;
    readonly Thread _thread;

    /// <summary>Raised (on the video thread) when the decoder needs a keyframe.</summary>
    public event Action? KeyframeNeeded;
    /// <summary>Raised (on the video thread) once per stream, when the first picture is presented.</summary>
    public event Action? Playing;

    /// <summary>The picture's size changed (the phone turned, or the first picture): its width and
    /// height in pixels, without the decoder's padding. Raised on the decoding thread.</summary>
    public event Action<int, int>? FrameSize;

    // the panel's size in physical pixels, its DPI scale, and its corners' radius in pixels (set from the UI thread)
    volatile int _targetW, _targetH;
    volatile float _cornerPx;
    float _scaleX = 1, _scaleY = 1;
    long _askedKeyframeAt = -10_000;

    // --- owned by the video thread ---
    ID3D11Device? _device;
    ID3D11DeviceContext? _context;
    ID3D11VideoDevice? _videoDevice;
    ID3D11VideoContext? _videoContext;
    IMFDXGIDeviceManager? _dxgiManager;
    IDXGISwapChain1? _swap;
    int _swapW, _swapH;
    IMFTransform? _decoder;
    bool _decoderProvidesSamples;
    int _videoW, _videoH, _stride;
    bool _awaitingKey = true, _playingSent;
    ID3D11VideoProcessorEnumerator? _vpEnum;
    ID3D11VideoProcessor? _vp;
    int _vpInW, _vpInH, _vpOutW, _vpOutH;
    ID3D11Texture2D? _uploadTex;          // the software decoder's frames, uploaded
    IMFSample? _lastSample;               // to redraw after a resize
    readonly Stopwatch _clock = Stopwatch.StartNew();

    public VideoPipeline(SwapChainPanel panel, DispatcherQueue ui)
    {
        _panel = panel;
        _ui = ui;
        _thread = new Thread(Run) { IsBackground = true, Name = "video", Priority = ThreadPriority.AboveNormal };
        _thread.Start();
    }

    // ---------------------------------------------------------------- the public side

    /// <summary>One access unit from the server (any thread).</summary>
    public void Push(bool key, byte[] data)
    {
        if (!key && _in.Count > MaxQueued)
        {
            // Behind: everything queued is useless without its predecessors.
            _in.Add(new Item(Kind.Reset));
            return;
        }
        _in.Add(new Item(Kind.Frame, key, data));
    }

    /// <summary>Start over: the next keyframe begins a new stream.</summary>
    public void Reset() => _in.Add(new Item(Kind.Reset));

    /// <summary>Show nothing until the next stream.</summary>
    public void Stop() => _in.Add(new Item(Kind.Stop));

    /// <summary>The panel's size in DIPs, its composition scale, and the radius of the rounded corners the
    /// picture is cut to, in DIPs (UI thread).</summary>
    public void Resize(double width, double height, float scaleX, float scaleY, double corner)
    {
        _scaleX = scaleX <= 0 ? 1 : scaleX;
        _scaleY = scaleY <= 0 ? 1 : scaleY;
        _targetW = Math.Max(1, (int)Math.Round(width * _scaleX));
        _targetH = Math.Max(1, (int)Math.Round(height * _scaleY));
        _cornerPx = (float)Math.Max(0, corner * Math.Min(_scaleX, _scaleY));
        _in.Add(new Item(Kind.Resize));
    }

    public void Dispose()
    {
        _in.Add(new Item(Kind.Quit));
        _thread.Join(2000);
    }

    void AskKeyframe()
    {
        var now = Environment.TickCount64;
        if (now - _askedKeyframeAt < 500) return;
        _askedKeyframeAt = now;
        KeyframeNeeded?.Invoke();
    }

    // ---------------------------------------------------------------- the video thread

    void Run()
    {
        try
        {
            Marshal.ThrowExceptionForHR(MFStartup(0x20070 /*MF_VERSION*/, 0));
            CreateDevice();
        }
        catch (Exception e)
        {
            Debug.WriteLine("video: can't start: " + e);
            return;
        }

        foreach (var item in _in.GetConsumingEnumerable())
        {
            try
            {
                switch (item.Kind)
                {
                    case Kind.Quit:
                        ReleaseDecoder();
                        return;
                    case Kind.Reset:
                        DropBacklog();
                        ReleaseDecoder();
                        _awaitingKey = true;
                        AskKeyframe();
                        break;
                    case Kind.Stop:
                        DropBacklog();
                        ReleaseDecoder();
                        _awaitingKey = true;
                        ClearScreen();
                        break;
                    case Kind.Resize:
                        EnsureSwapChain();
                        if (_lastSample != null) Show(_lastSample, keep: false); else ClearScreen();
                        break;
                    case Kind.Frame:
                        if (_awaitingKey)
                        {
                            if (!item.Key) { AskKeyframe(); break; }
                            _awaitingKey = false;
                        }
                        Decode(item.Data!);
                        break;
                }
            }
            catch (Exception e)
            {
                // A decoder or device error: start over from a fresh keyframe.
                Debug.WriteLine("video: " + e.Message);
                ReleaseDecoder();
                _awaitingKey = true;
                AskKeyframe();
            }
        }
    }

    void DropBacklog()
    {
        while (_in.TryTake(out var next))
        {
            if (next.Kind == Kind.Frame) continue;
            if (next.Kind == Kind.Quit) { _in.Add(next); return; }
            if (next.Kind == Kind.Resize) { EnsureSwapChain(); continue; }
        }
    }

    void CreateDevice()
    {
        var levels = new[] { FeatureLevel.Level_11_1, FeatureLevel.Level_11_0, FeatureLevel.Level_10_1 };
        const uint BgraSupport = 0x20, VideoSupport = 0x800;
        Marshal.ThrowExceptionForHR(D3D11CreateDevice(IntPtr.Zero, 1 /*HARDWARE*/, IntPtr.Zero, BgraSupport | VideoSupport,
            levels, (uint)levels.Length, 7, out var dev, out _, out var ctx));
        _device = new ID3D11Device(dev);
        _context = new ID3D11DeviceContext(ctx);
        using (var mt = _device.QueryInterface<ID3D11Multithread>())
            mt.SetMultithreadProtected(true);  // the decoder uses the device from its own threads
        _videoDevice = _device.QueryInterface<ID3D11VideoDevice>();
        _videoContext = _context.QueryInterface<ID3D11VideoContext>();
        Marshal.ThrowExceptionForHR(MFCreateDXGIDeviceManager(out uint token, out var mgr));
        _dxgiManager = new IMFDXGIDeviceManager(mgr);
        // IMFDXGIDeviceManager::ResetDevice (vtable slot 7)
        var vtbl = *(void***)mgr;
        var reset = (delegate* unmanaged[Stdcall]<IntPtr, IntPtr, uint, int>)vtbl[7];
        Marshal.ThrowExceptionForHR(reset(mgr, _device.NativePointer, token));
    }

    [DllImport("mfplat.dll")] static extern int MFStartup(uint version, uint flags);
    [DllImport("mfplat.dll")] static extern int MFCreateDXGIDeviceManager(out uint token, out IntPtr manager);
    [DllImport("d3d11.dll")]
    static extern int D3D11CreateDevice(IntPtr adapter, int driverType, IntPtr software, uint flags,
        FeatureLevel[] levels, uint count, uint sdkVersion, out IntPtr device, out FeatureLevel level, out IntPtr context);

    // ---------------------------------------------------------------- decoding

    [DllImport("ole32.dll")]
    static extern int CoCreateInstance(in Guid clsid, IntPtr outer, uint context, in Guid iid, out IntPtr obj);

    void CreateDecoder()
    {
        var iid = typeof(IMFTransform).GUID;
        Marshal.ThrowExceptionForHR(CoCreateInstance(CLSID_MSH264DecoderMFT, IntPtr.Zero, 1 /*INPROC_SERVER*/, iid, out var ptr));
        var mft = new IMFTransform(ptr);
        var attrs = mft.Attributes;
        // Return each frame as soon as it's decoded instead of filling the reorder buffer
        // first: the phone's stream has no B-frames, so nothing is lost.
        attrs.Set(CODECAPI_AVLowLatencyMode, 1u);
        bool d3d = false;
        try { d3d = attrs.GetUInt32(MF_SA_D3D11_AWARE) != 0; } catch (SharpGenException) { }
        if (d3d)
        {
            try
            {
                mft.ProcessMessage(TMessageType.MessageSetD3DManager, (nuint)(nint)_dxgiManager!.NativePointer);
            }
            catch (SharpGenException) { d3d = false; }
        }

        using (var input = MediaFactory.MFCreateMediaType())
        {
            input.Set(MediaTypeAttributeKeys.MajorType, MediaTypeGuids.Video);
            input.Set(MediaTypeAttributeKeys.Subtype, MFVideoFormat_H264);
            input.Set(MediaTypeAttributeKeys.InterlaceMode, 2u);  // progressive
            mft.SetInputType(0, input, 0);
        }
        _decoder = mft;
        SelectOutputType();
        _decoder.ProcessMessage(TMessageType.MessageNotifyBeginStreaming, 0);
        _decoder.ProcessMessage(TMessageType.MessageNotifyStartOfStream, 0);
        Debug.WriteLine($"video: H.264 decoder ready (GPU: {d3d}, provides samples: {_decoderProvidesSamples})");
    }

    void SelectOutputType()
    {
        var mft = _decoder!;
        for (int i = 0; ; i++)
        {
            IMFMediaType type;
            try { type = mft.GetOutputAvailableType(0, i); }
            catch (SharpGenException) { throw new InvalidOperationException("the decoder offers no NV12 output"); }
            using (type)
            {
                if (type.GetGUID(MediaTypeAttributeKeys.Subtype) != MFVideoFormat_NV12) continue;
                mft.SetOutputType(0, type, 0);
                break;
            }
        }
        using (var current = mft.GetOutputCurrentType(0))
        {
            try
            {
                ulong size = current.GetUInt64(MediaTypeAttributeKeys.FrameSize);
                _videoW = (int)(size >> 32);
                _videoH = (int)(size & 0xFFFFFFFF);
            }
            catch (SharpGenException) { }
            try { _stride = (int)current.GetUInt32(MediaTypeAttributeKeys.DefaultStride); }
            catch (SharpGenException) { _stride = _videoW; }
            _crop = ReadAperture(current, _videoW, _videoH);
            FrameSize?.Invoke(_crop.Right - _crop.Left, _crop.Bottom - _crop.Top);
        }
        var info = mft.GetOutputStreamInfo(0);
        _decoderProvidesSamples = (info.Flags & (int)(OutputStreamInfoFlags.OutputStreamProvidesSamples
                                                     | OutputStreamInfoFlags.OutputStreamCanProvideSamples)) != 0;
        _outputSize = info.Size;
        _uploadTex?.Dispose();
        _uploadTex = null;
    }

    int _outputSize;

    // The part of the decoded frame that is the picture. H.264 codes whole 16-pixel blocks,
    // so the frame is padded on the right and bottom (FrameSize), and drawing the padding
    // showed as a black strip along those edges of the screen.
    Vortice.RawRect _crop;

    /// <summary>The minimum display aperture (an MFVideoArea: x and y as 16.16 fixed point,
    /// then width and height), or the whole frame when the decoder gives none.</summary>
    static Vortice.RawRect ReadAperture(IMFMediaType type, int w, int h)
    {
        try
        {
            var a = type.GetBlob(MediaTypeAttributeKeys.MinimumDisplayAperture);
            if (a.Length >= 16)
            {
                int x = BitConverter.ToInt16(a, 2), y = BitConverter.ToInt16(a, 6);
                int cw = BitConverter.ToInt32(a, 8), ch = BitConverter.ToInt32(a, 12);
                if (cw > 0 && ch > 0 && x >= 0 && y >= 0 && (w <= 0 || x + cw <= w) && (h <= 0 || y + ch <= h))
                    return new Vortice.RawRect(x, y, x + cw, y + ch);
            }
        }
        catch (SharpGenException) { }
        return new Vortice.RawRect(0, 0, w, h);
    }

    void ReleaseDecoder()
    {
        _lastSample?.Dispose();
        _lastSample = null;
        if (_decoder == null) return;
        try { _decoder.ProcessMessage(TMessageType.MessageCommandFlush, 0); } catch (SharpGenException) { }
        _decoder.Dispose();
        _decoder = null;
        _playingSent = false;
    }

    void Decode(byte[] data)
    {
        if (_decoder == null) CreateDecoder();
        using var sample = MediaFactory.MFCreateSample();
        using (var buffer = MediaFactory.MFCreateMemoryBuffer(data.Length))
        {
            buffer.Lock(out var p, out _, out _);
            Marshal.Copy(data, 0, p, data.Length);
            buffer.Unlock();
            buffer.CurrentLength = data.Length;
            sample.AddBuffer(buffer);
        }
        sample.SampleTime = _clock.Elapsed.Ticks;  // 100 ns units; the decoder needs them rising

        try { _decoder!.ProcessInput(0, sample, 0); }
        catch (SharpGenException e) when (e.ResultCode.Code == MF_E_NOTACCEPTING)
        {
            DrainOutput();
            _decoder!.ProcessInput(0, sample, 0);
        }
        DrainOutput();
    }

    void DrainOutput()
    {
        while (true)
        {
            var output = new OutputDataBuffer { StreamID = 0 };
            IMFSample? own = null;
            if (!_decoderProvidesSamples)
            {
                own = MediaFactory.MFCreateSample();
                using var b = MediaFactory.MFCreateMemoryBuffer(Math.Max(_outputSize, _videoW * _videoH * 3 / 2));
                own.AddBuffer(b);
                output.Sample = own;
            }
            var hr = _decoder!.ProcessOutput(ProcessOutputFlags.None, 1, ref output, out _);
            output.Events?.Dispose();
            if (hr.Code == MF_E_TRANSFORM_NEED_MORE_INPUT)
            {
                own?.Dispose();
                return;
            }
            if (hr.Code == MF_E_TRANSFORM_STREAM_CHANGE)
            {
                own?.Dispose();
                SelectOutputType();  // a new size (the phone rotated) or the first SPS
                continue;
            }
            hr.CheckError();
            var frame = output.Sample ?? own;
            if (frame != null) Show(frame, keep: true);
        }
    }

    // ---------------------------------------------------------------- presenting

    void Show(IMFSample sample, bool keep)
    {
        if (!EnsureSwapChain())
        {
            if (keep) { _lastSample?.Dispose(); _lastSample = sample; }
            return;
        }
        using var buffer = sample.GetBufferByIndex(0);
        ID3D11Texture2D? texture = null;
        uint slice = 0;
        IMFDXGIBuffer? dxgi = null;
        try { dxgi = buffer.QueryInterface<IMFDXGIBuffer>(); } catch (SharpGenException) { }
        if (dxgi != null)
        {
            using (dxgi)
            {
                var ptr = dxgi.GetResource(typeof(ID3D11Texture2D).GUID);
                texture = new ID3D11Texture2D(ptr);
                slice = (uint)dxgi.SubresourceIndex;
            }
        }
        else
        {
            texture = Upload(buffer);
        }
        try
        {
            if (texture != null) Blit(texture, slice);
        }
        finally
        {
            if (dxgi != null) texture?.Dispose();  // the upload texture is kept
        }
        if (keep)
        {
            if (!ReferenceEquals(_lastSample, sample)) _lastSample?.Dispose();
            _lastSample = sample;
        }
        if (!_playingSent)
        {
            _playingSent = true;
            Playing?.Invoke();
        }
    }

    /// <summary>The software decoder's NV12 frame, copied into a texture.</summary>
    ID3D11Texture2D? Upload(IMFMediaBuffer buffer)
    {
        if (_videoW <= 0 || _videoH <= 0) return null;
        _uploadTex ??= _device!.CreateTexture2D(new Texture2DDescription
        {
            Width = (uint)_videoW,
            Height = (uint)_videoH,
            MipLevels = 1,
            ArraySize = 1,
            Format = Format.NV12,
            SampleDescription = new SampleDescription(1, 0),
            Usage = ResourceUsage.Default,
            BindFlags = BindFlags.ShaderResource,
        });
        buffer.Lock(out var p, out _, out _);
        try
        {
            int stride = _stride > 0 ? _stride : _videoW;
            _context!.UpdateSubresource(_uploadTex, 0, null, p, (uint)stride, 0);
        }
        finally { buffer.Unlock(); }
        return _uploadTex;
    }

    void Blit(ID3D11Texture2D texture, uint slice)
    {
        var desc = texture.Description;
        int inW = _videoW > 0 ? _videoW : (int)desc.Width, inH = _videoH > 0 ? _videoH : (int)desc.Height;
        EnsureProcessor(inW, inH, _swapW, _swapH);

        var inDesc = new VideoProcessorInputViewDescription
        {
            FourCC = 0,
            ViewDimension = VideoProcessorInputViewDimension.Texture2D,
            Texture2D = new Texture2DVideoProcessorInputView { MipSlice = 0, ArraySlice = slice },
        };
        _videoDevice!.CreateVideoProcessorInputView(texture, _vpEnum!, inDesc, out var inView).CheckError();
        using var back = _swap!.GetBuffer<ID3D11Texture2D>(0);
        var outDesc = new VideoProcessorOutputViewDescription { ViewDimension = VideoProcessorOutputViewDimension.Texture2D };
        _videoDevice.CreateVideoProcessorOutputView(back, _vpEnum!, outDesc, out var outView).CheckError();
        using (inView)
        using (outView)
        {
            var src = _crop.Right > _crop.Left && _crop.Right <= inW && _crop.Bottom <= inH
                ? _crop : new Vortice.RawRect(0, 0, inW, inH);  // the picture, without the padding
            var dst = new Vortice.RawRect(0, 0, _swapW, _swapH);
            _videoContext!.VideoProcessorSetStreamSourceRect(_vp!, 0, true, src);
            _videoContext.VideoProcessorSetStreamDestRect(_vp!, 0, true, dst);
            _videoContext.VideoProcessorSetOutputTargetRect(_vp!, true, dst);
            var stream = new VideoProcessorStream { Enable = true, InputSurface = inView };
            _videoContext.VideoProcessorBlt(_vp!, outView, 0, 1, new[] { stream }).CheckError();
        }
        CutCorners(back);
        _swap.Present(0, PresentFlags.None);
    }

    /// <summary>
    /// Clear the picture's corners to transparent, outside a rounded rectangle of radius _cornerPx.
    /// The panel's composition clip (MainWindow.ClipScreen) rounds everything else on the screen but not a
    /// swap chain, whose square corners then showed past the phone's rounded ones. The edge is cut with
    /// circular corners, a little wider than the screen's continuous ones (ContinuousCorner): the phone's
    /// body, drawn over the picture, covers the difference and hides the edge's stair steps.
    /// </summary>
    void CutCorners(ID3D11Texture2D back)
    {
        float r = Math.Min(_cornerPx, Math.Min(_swapW, _swapH) / 2f);
        if (r < 1) return;
        var rects = new List<Vortice.RawRect>();
        int rows = (int)Math.Ceiling(r);
        for (int j = 0; j < rows; j++)
        {
            // how far in from the side the curve is on this row (pixel centres)
            float dy = r - (j + 0.5f);
            int cut = (int)Math.Floor(r - MathF.Sqrt(Math.Max(0, r * r - dy * dy)));
            if (cut <= 0) continue;
            int top = j, bottom = _swapH - 1 - j;
            rects.Add(new Vortice.RawRect(0, top, cut, top + 1));
            rects.Add(new Vortice.RawRect(_swapW - cut, top, _swapW, top + 1));
            rects.Add(new Vortice.RawRect(0, bottom, cut, bottom + 1));
            rects.Add(new Vortice.RawRect(_swapW - cut, bottom, _swapW, bottom + 1));
        }
        if (rects.Count == 0) return;
        using var rtv = _device!.CreateRenderTargetView(back);
        using var context1 = _context!.QueryInterface<ID3D11DeviceContext1>();
        context1.ClearView(rtv, new Vortice.Mathematics.Color4(0, 0, 0, 0), rects.ToArray());
    }

    void EnsureProcessor(int inW, int inH, int outW, int outH)
    {
        if (_vp != null && inW == _vpInW && inH == _vpInH && outW == _vpOutW && outH == _vpOutH) return;
        _vp?.Dispose();
        _vpEnum?.Dispose();
        var content = new VideoProcessorContentDescription
        {
            InputFrameFormat = VideoFrameFormat.Progressive,
            InputWidth = (uint)inW,
            InputHeight = (uint)inH,
            OutputWidth = (uint)outW,
            OutputHeight = (uint)outH,
            InputFrameRate = new Rational(60, 1),
            OutputFrameRate = new Rational(60, 1),
            Usage = VideoUsage.OptimalSpeed,
        };
        _videoDevice!.CreateVideoProcessorEnumerator(ref content, out _vpEnum).CheckError();
        _videoDevice.CreateVideoProcessor(_vpEnum!, 0, out _vp).CheckError();
        // The phone sends BT.709 video in limited range; the screen wants full-range RGB.
        uint input = (1u << 2) | (1u << 4);  // YCbCr_Matrix = BT.709, Nominal_Range = 16-235
        uint output = 0;                     // RGB_Range = 0-255
        _videoContext!.VideoProcessorSetStreamColorSpace(_vp!, 0, Unsafe.As<uint, VideoProcessorColorSpace>(ref input));
        _videoContext.VideoProcessorSetOutputColorSpace(_vp!, Unsafe.As<uint, VideoProcessorColorSpace>(ref output));
        _videoContext.VideoProcessorSetStreamFrameFormat(_vp!, 0, VideoFrameFormat.Progressive);
        _videoContext.VideoProcessorSetStreamAutoProcessingMode(_vp!, 0, false);  // no "enhancements": they cost time
        // every pixel it writes opaque: the swap chain has an alpha channel now (CutCorners)
        _videoContext.VideoProcessorSetOutputAlphaFillMode(_vp!, VideoProcessorAlphaFillMode.Opaque, 0);
        _vpInW = inW; _vpInH = inH; _vpOutW = outW; _vpOutH = outH;
    }

    /// <summary>A swap chain the panel's size; false while the panel has no size yet.</summary>
    bool EnsureSwapChain()
    {
        int w = _targetW, h = _targetH;
        if (w <= 0 || h <= 0) return false;
        if (_swap == null)
        {
            using var dxgiDevice = _device!.QueryInterface<IDXGIDevice>();
            using var adapter = dxgiDevice.GetAdapter();
            using var factory = adapter.GetParent<IDXGIFactory2>();
            var desc = new SwapChainDescription1
            {
                Width = (uint)w,
                Height = (uint)h,
                Format = Format.B8G8R8A8_UNorm,
                SampleDescription = new SampleDescription(1, 0),
                BufferUsage = Usage.RenderTargetOutput,
                BufferCount = 2,
                Scaling = Scaling.Stretch,
                SwapEffect = SwapEffect.FlipSequential,
                AlphaMode = AlphaMode.Premultiplied,  // the corners are cut out (CutCorners)
            };
            _swap = factory.CreateSwapChainForComposition(_device, desc, null);
            _swapW = w; _swapH = h;
            ApplyScale();
            AttachToPanel(_swap);
        }
        else if (w != _swapW || h != _swapH)
        {
            _swap.ResizeBuffers(2, (uint)w, (uint)h, Format.B8G8R8A8_UNorm, SwapChainFlags.None).CheckError();
            _swapW = w; _swapH = h;
            ApplyScale();
        }
        return true;
    }


    void ApplyScale()
    {
        // The buffer is in physical pixels; the panel lays out in DIPs.
        using var swap2 = _swap!.QueryInterface<IDXGISwapChain2>();
        var m = Matrix3x2.CreateScale(1f / _scaleX, 1f / _scaleY);
        swap2.MatrixTransform = m;
    }

    void ClearScreen()
    {
        if (!EnsureSwapChain()) return;
        using var back = _swap!.GetBuffer<ID3D11Texture2D>(0);
        using var rtv = _device!.CreateRenderTargetView(back);
        _context!.ClearRenderTargetView(rtv, new Vortice.Mathematics.Color4(0, 0, 0, 1));
        CutCorners(back);
        _swap.Present(0, PresentFlags.None);
    }

    /// <summary>ISwapChainPanelNative::SetSwapChain, on the UI thread (it must be called there).</summary>
    void AttachToPanel(IDXGISwapChain1 swap)
    {
        var done = new ManualResetEventSlim();
        var swapPtr = swap.NativePointer;
        _ui.TryEnqueue(() =>
        {
            try
            {
                var unk = ((WinRT.IWinRTObject)_panel).NativeObject.ThisPtr;
                var iid = IID_ISwapChainPanelNative;
                Marshal.ThrowExceptionForHR(Marshal.QueryInterface(unk, in iid, out var native));
                try
                {
                    var vtbl = *(void***)native;
                    var setSwapChain = (delegate* unmanaged[Stdcall]<IntPtr, IntPtr, int>)vtbl[3];
                    Marshal.ThrowExceptionForHR(setSwapChain(native, swapPtr));
                }
                finally { Marshal.Release(native); }
            }
            catch (Exception e) { Debug.WriteLine("video: can't attach the swap chain: " + e.Message); }
            finally { done.Set(); }
        });
        done.Wait(2000);
    }
}
