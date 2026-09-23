using System.Collections.Generic;
using FactoryForge.TagBus;
using Godot;

namespace FactoryForge.Parts;

/// <summary>
/// Overhead code reader (CP-04).
///
/// The library could tell a controller that something was *there* and how tall
/// or how heavy it was. It could not tell it *what* the thing was. That is the
/// gap this fills, and it fills it in the shape real identification devices
/// use: a code plus a read pulse. A program that does not latch the code on
/// that pulse's rising edge loses it when the next item is read, which is a
/// mistake worth making here rather than on a line. The pulse is held for
/// <see cref="ReadHold"/> rather than one scan, so a polled link can see it
/// (IP-31).
///
/// Codes come from what the item actually is, so the reading is predictable and
/// a sorting program written against it is testable:
/// <list type="bullet">
/// <item><description>101 — short cardboard carton</description></item>
/// <item><description>102 — tall cardboard carton</description></item>
/// <item><description>201 — metal item</description></item>
/// </list>
/// </summary>
public partial class BarcodeScanner : Node3D, IPart
{
    /// <summary>Height of the read window above the carrying surface.</summary>
    [Export] public float HeightAboveBelt { get; set; } = 0.42f;

    /// <summary>Length of the read window along the lane. Anything inside it is
    /// in front of the head.</summary>
    [Export] public float WindowLength { get; set; } = 0.26f;

    public const int CodeShortCarton = 101;
    public const int CodeTallCarton = 102;
    public const int CodeMetal = 201;

    /// <summary>Armed by the controller. A disarmed scanner reports nothing and
    /// its light goes out — the same contract a real one has, and the reason
    /// `.enable` is an Output rather than a setting.</summary>
    public bool Enabled { get; set; } = true;

    /// <summary>Last code reported. Held until the next read is reported, the
    /// way a real reader's output register is -- and it changes on the same
    /// tick <see cref="ReadPulse"/> rises, so the edge and the code it belongs
    /// to always arrive together.</summary>
    public int LastCode { get; private set; }

    /// <summary>
    /// How long the read output stays on for each read, in seconds, and the
    /// least time it stays off before the next one (IP-31). The same 200 ms,
    /// for the same reason, as <see cref="ButtonPanel.PressHold"/>: it used to
    /// be one physics tick, and no controller reads this tag except through a
    /// transport that samples it -- a Modbus master polling every 50 ms sees a
    /// 17 ms pulse about one time in three. Counted in physics ticks, which
    /// Godot paces by the wall clock at every time scale, as the poller is.
    /// </summary>
    public float ReadHold { get; set; } = ButtonPanel.DefaultPressHold;

    public int ReadHoldTicks =>
        System.Math.Max(1, (int)System.Math.Ceiling(ReadHold * Engine.PhysicsTicksPerSecond - 1e-3));

    /// <summary>True while the read output is on: from the tick a read is
    /// reported, for <see cref="ReadHoldTicks"/> ticks.</summary>
    public bool ReadPulse { get; private set; }

    /// <summary>Something is in the read window right now.</summary>
    public bool IsPresent { get; private set; }

    private Area3D _window = null!;
    private MeshInstance3D _beam = null!;
    private StandardMaterial3D _beamMat = null!;
    private Label3D _readout = null!;
    private StandardMaterial3D _statusMat = null!;

    private ulong _lastReadId;
    private float _sweep;

    /// <summary>Reads made while the output was still on, or still off for
    /// its minimum, waiting to be reported in order. Two items read closer
    /// together than two holds -- a short gap on a fast belt -- would otherwise
    /// share one edge, and the program would latch the first code and never
    /// see the second. Bounded, because a belt that can outrun the reader
    /// indefinitely is a scene problem and not a queue to grow.</summary>
    private readonly Queue<int> _unreported = new();
    private const int MaxUnreported = 8;

    private enum OutputPhase { Idle, On, Off }
    private OutputPhase _phase;
    private int _phaseTicksLeft;

    private float WindowY => PartLayout.BeltSurface + HeightAboveBelt;

    public override void _Ready()
    {
        var frameMat = new StandardMaterial3D
        {
            AlbedoColor = new Color(0.22f, 0.24f, 0.27f),
            Metallic = 0.45f,
            Roughness = 0.45f,
        };
        var headMat = new StandardMaterial3D
        {
            AlbedoColor = new Color(0.10f, 0.11f, 0.13f),
            Metallic = 0.30f,
            Roughness = 0.35f,
        };

        // Gooseneck bracket: a post beside the lane and an arm reaching over
        // it, so the head looks down the way a real reader is mounted.
        float postHeight = PartLayout.FloorDrop + WindowY + 0.14f;
        AddChild(new MeshInstance3D
        {
            Name = "MountPost",
            Mesh = new BoxMesh { Size = new Vector3(0.05f, postHeight, 0.05f) },
            MaterialOverride = frameMat,
            Position = new Vector3(0, WindowY + 0.14f - postHeight / 2.0f, -0.30f),
        });
        AddChild(new MeshInstance3D
        {
            Name = "BaseFoot",
            Mesh = new BoxMesh { Size = new Vector3(0.16f, 0.025f, 0.16f) },
            MaterialOverride = frameMat,
            Position = new Vector3(0, -PartLayout.FloorDrop, -0.30f),
        });
        AddChild(new MeshInstance3D
        {
            Name = "MountArm",
            Mesh = new BoxMesh { Size = new Vector3(0.05f, 0.05f, 0.32f) },
            MaterialOverride = frameMat,
            Position = new Vector3(0, WindowY + 0.14f, -0.15f),
        });

        AddChild(new MeshInstance3D
        {
            Name = "ScannerHead",
            Mesh = new BoxMesh { Size = new Vector3(0.14f, 0.10f, 0.16f) },
            MaterialOverride = headMat,
            Position = new Vector3(0, WindowY + 0.13f, 0),
        });

        // Status LED on the head — lit amber when armed, green on a read. This
        // is how you diagnose a real reader: you look at the light before you
        // look at the tag.
        _statusMat = new StandardMaterial3D
        {
            AlbedoColor = new Color(0.25f, 0.20f, 0.05f),
            EmissionEnabled = true,
            Emission = new Color(1.0f, 0.75f, 0.15f),
            EmissionEnergyMultiplier = 0.0f,
        };
        AddChild(new MeshInstance3D
        {
            Name = "StatusLed",
            Mesh = new SphereMesh { Radius = 0.016f, Height = 0.032f },
            MaterialOverride = _statusMat,
            Position = new Vector3(0, WindowY + 0.13f, 0.085f),
        });

        // The scan fan. Unshaded and additive so it reads as light rather than
        // as a red plastic wedge hanging under the head.
        _beamMat = new StandardMaterial3D
        {
            AlbedoColor = new Color(1.0f, 0.12f, 0.10f, 0.30f),
            Transparency = BaseMaterial3D.TransparencyEnum.Alpha,
            BlendMode = BaseMaterial3D.BlendModeEnum.Add,
            ShadingMode = BaseMaterial3D.ShadingModeEnum.Unshaded,
            CullMode = BaseMaterial3D.CullModeEnum.Disabled,
        };
        _beam = new MeshInstance3D
        {
            Name = "ScanFan",
            Mesh = new BoxMesh { Size = new Vector3(0.006f, HeightAboveBelt, WindowLength * 0.9f) },
            MaterialOverride = _beamMat,
            Position = new Vector3(0, PartLayout.BeltSurface + HeightAboveBelt / 2.0f, 0),
        };
        AddChild(_beam);

        _readout = new Label3D
        {
            Name = "CodeReadout",
            Text = "---",
            FontSize = 40,
            PixelSize = 0.0022f,
            Modulate = new Color(1.0f, 0.55f, 0.25f),
            Position = new Vector3(0, WindowY + 0.24f, 0),
            Billboard = BaseMaterial3D.BillboardModeEnum.Enabled,
        };
        AddChild(_readout);

        _window = new Area3D { Name = "ReadWindow", Monitoring = true };
        _window.AddChild(new CollisionShape3D
        {
            Shape = new BoxShape3D { Size = new Vector3(WindowLength, 0.44f, 0.42f) },
            Position = new Vector3(0, PartLayout.BeltSurface + 0.22f, 0),
        });
        AddChild(_window);
    }

    /// <summary>
    /// Rebuild the geometry after a setting that the mesh was built from has
    /// changed. Without this, the head-height and window sliders would move and
    /// do nothing visible — the exact failure LE-01 found in the light curtain,
    /// where a control existed and configured nothing.
    /// </summary>
    public void Rebuild()
    {
        foreach (var child in GetChildren()) { RemoveChild(child); child.QueueFree(); }
        _Ready();
    }

    /// <summary>
    /// One scan. A read is made only when a *different* item enters the window
    /// — holding one carton under the head does not re-read it, which is
    /// exactly the behaviour that makes the pulse worth latching — and is then
    /// reported through the held output (see <see cref="StepOutput"/>).
    /// </summary>
    public void Scan(float delta)
    {
        BoxPhysics? item = null;
        if (Enabled && _window is not null)
        {
            foreach (var body in _window.GetOverlappingBodies())
            {
                if (body is not BoxPhysics box) continue;
                item = box;
                break;
            }
        }

        IsPresent = item is not null;

        if (item is null)
        {
            // Leaving the window arms the next read. Without this, two
            // identical cartons in a row would read once.
            _lastReadId = 0;
        }
        else if (item.GetInstanceId() != _lastReadId)
        {
            _lastReadId = item.GetInstanceId();
            if (_unreported.Count >= MaxUnreported) _unreported.Dequeue();
            _unreported.Enqueue(item.IsMetal ? CodeMetal : (item.IsTall ? CodeTallCarton : CodeShortCarton));
        }

        StepOutput();
        UpdateVisuals(delta);
    }

    /// <summary>
    /// Move the read output on by a tick: on for the hold, then off for at
    /// least the hold, then the next waiting read. One read, one rising edge,
    /// with the code register changed on that same tick. A disarmed reader
    /// reports nothing, so disarming drops the output and anything waiting --
    /// but still keeps the output off for its minimum, so re-arming cannot make
    /// an edge a poller would read as no edge at all.
    /// </summary>
    private void StepOutput()
    {
        int hold = ReadHoldTicks;

        if (!Enabled)
        {
            _unreported.Clear();
            if (_phase == OutputPhase.On) { _phase = OutputPhase.Off; _phaseTicksLeft = hold - 1; }
        }

        switch (_phase)
        {
            case OutputPhase.On:
                if (_phaseTicksLeft > 0) { _phaseTicksLeft--; break; }
                _phase = OutputPhase.Off;
                _phaseTicksLeft = hold - 1;
                break;

            case OutputPhase.Off:
                if (_phaseTicksLeft > 0) { _phaseTicksLeft--; break; }
                _phase = OutputPhase.Idle;
                goto case OutputPhase.Idle;

            case OutputPhase.Idle:
                if (_unreported.Count == 0) break;
                LastCode = _unreported.Dequeue();
                _phase = OutputPhase.On;
                _phaseTicksLeft = hold - 1;
                break;
        }

        ReadPulse = _phase == OutputPhase.On;
    }

    private void UpdateVisuals(float delta)
    {
        if (_beamMat is null) return;

        _beamMat.AlbedoColor = new Color(1.0f, 0.12f, 0.10f, Enabled ? 0.30f : 0.0f);
        if (_beam is not null)
        {
            _beam.Visible = Enabled;
            // The fan sweeps along the lane while armed, so an armed-but-idle
            // scanner is distinguishable from a dead one at a glance.
            _sweep += delta * 6.0f;
            _beam.Position = new Vector3(Mathf.Sin(_sweep) * WindowLength * 0.35f,
                                         PartLayout.BeltSurface + HeightAboveBelt / 2.0f, 0);
        }

        if (_statusMat is not null)
        {
            bool read = IsPresent && LastCode != 0;
            _statusMat.Emission = read ? new Color(0.25f, 1.0f, 0.35f) : new Color(1.0f, 0.75f, 0.15f);
            _statusMat.EmissionEnergyMultiplier = Enabled ? (read ? 3.0f : 1.2f) : 0.0f;
        }

        if (_readout is not null)
            _readout.Text = !Enabled ? "off" : (LastCode == 0 ? "---" : LastCode.ToString());
    }

    // ---------- IPart (HP-34)

    public void DeclareTags(PartTagBuilder tags) => tags
        // Armed on arrival: a scanner that has to be enabled before it shows
        // anything is a part that looks broken when it is placed.
        .Bit("enable", $"Scanner {tags.Index} Enable", TagKind.Output, initial: true)
        .Int("code", $"Scanner {tags.Index} Code", TagKind.Input)
        // One edge per read, held long enough for a polled link to see
        // (ReadHold) -- and the code changes with it, so a program latches the
        // code on the edge, the same way it would on a real reader.
        .Bit("read", $"Scanner {tags.Index} Read Pulse", TagKind.Input)
        .Bit("present", $"Scanner {tags.Index} Item Present", TagKind.Input);

    public void CaptureSettings(PartSettings settings)
    {
        settings.Put("height", HeightAboveBelt);
        settings.Put("window", WindowLength);
        settings.Put("read_hold", ReadHold);
    }

    public void ApplySettings(PartSettings settings)
    {
        if (settings.Number("height") is { } height) HeightAboveBelt = height;
        if (settings.Number("window") is { } window) WindowLength = window;
        if (settings.Number("read_hold") is { } hold) ReadHold = hold;
    }

    public void StepPart(PartTick tick)
    {
        if (tick.TryBit("enable", out bool enabled)) Enabled = enabled;

        Scan(tick.Dt);

        tick.Write("code", LastCode);
        // Written every tick, so the tag is always what the output is. The
        // read is made on the same clock the tag is written on, so there is
        // no click to queue here the way the panel has to -- only reads that
        // arrive faster than the output can report them.
        tick.Write("read", ReadPulse);
        tick.Write("present", IsPresent);
    }

    public void DescribeControls(IPartInspector ui)
    {
        ui.Slider("Read Window (m)", WindowLength, 0.08f, 0.8f, 0.02f,
                  value => { WindowLength = value; Rebuild(); });
        ui.Slider("Head Height (m)", HeightAboveBelt, 0.15f, 0.9f, 0.02f,
                  value => { HeightAboveBelt = value; Rebuild(); });
        // Read by StepOutput every tick, so no rebuild. Raise it for a link
        // that samples slower than 200 ms (IP-31).
        ui.Slider("Read Pulse (s)", ReadHold, 0.05f, 2.0f, 0.05f, value => ReadHold = value);
    }

    public void ResetPart(PartReset reset)
    {
        // A new run starts with nothing read and the output off -- not halfway
        // through reporting a carton from the last one.
        _unreported.Clear();
        _phase = OutputPhase.Idle;
        _phaseTicksLeft = 0;
        _lastReadId = 0;
        ReadPulse = false;
        reset.Write("read", false);
    }

    public PartOperation? Operation => new("scanner", "enable");

    public void Operate(PartOperate op) => op.ToggleBit("enable");

    /// <summary>IP-07. An armed head over a tall carton has to report it
    /// present and read it as what it is -- the code, not only a presence bit.
    /// </summary>
    public PartProbe? Probe => new(
        $"`present` made and `code` reading {CodeTallCarton} (a tall carton)",
        r => r.Bit("present") && (int)r.Number("code") == CodeTallCarton)
    {
        Drive = PartProbe.Drives(("enable", true)),
        Carton = new ProbeCarton(new Vector3(0, PartLayout.BeltSurface + 0.01f, 0)),
    };
}
