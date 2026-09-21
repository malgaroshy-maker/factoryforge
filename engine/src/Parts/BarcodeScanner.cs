using FactoryForge.TagBus;
using Godot;

namespace FactoryForge.Parts;

/// <summary>
/// Overhead code reader (CP-04).
///
/// The library could tell a controller that something was *there* and how tall
/// or how heavy it was. It could not tell it *what* the thing was. That is the
/// gap this fills, and it fills it in the shape real identification devices
/// use: a code plus a one-scan read pulse. A program that does not latch the
/// code on that pulse loses it, which is a mistake worth making here rather
/// than on a line.
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

    /// <summary>Last code read. Held until the next read, the way a real
    /// reader's output register is.</summary>
    public int LastCode { get; private set; }

    /// <summary>True for exactly the tick a new item was read.</summary>
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
    /// One scan. Returns having set <see cref="ReadPulse"/> true for this tick
    /// only if a *different* item entered the window — holding one carton under
    /// the head does not re-read it, which is exactly the behaviour that makes
    /// the pulse worth latching.
    /// </summary>
    public void Scan(float delta)
    {
        ReadPulse = false;

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
            LastCode = item.IsMetal ? CodeMetal : (item.IsTall ? CodeTallCarton : CodeShortCarton);
            ReadPulse = true;
        }

        UpdateVisuals(delta);
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
        // One scan wide, exactly like a panel button's pulse -- which is why a
        // program has to latch it rather than poll it.
        .Bit("read", $"Scanner {tags.Index} Read Pulse", TagKind.Input)
        .Bit("present", $"Scanner {tags.Index} Item Present", TagKind.Input);

    public void CaptureSettings(PartSettings settings)
    {
        settings.Put("height", HeightAboveBelt);
        settings.Put("window", WindowLength);
    }

    public void ApplySettings(PartSettings settings)
    {
        if (settings.Number("height") is { } height) HeightAboveBelt = height;
        if (settings.Number("window") is { } window) WindowLength = window;
    }

    public void StepPart(PartTick tick)
    {
        if (tick.TryBit("enable", out bool enabled)) Enabled = enabled;

        Scan(tick.Dt);

        tick.Write("code", LastCode);
        // Written every tick, so the pulse falls again on the very next one
        // without anybody having to remember to clear it — the panel's
        // queue-and-drain problem does not arise here because the read happens
        // on the same clock the tag is written on.
        tick.Write("read", ReadPulse);
        tick.Write("present", IsPresent);
    }

    public void DescribeControls(IPartInspector ui)
    {
        ui.Slider("Read Window (m)", WindowLength, 0.08f, 0.8f, 0.02f,
                  value => { WindowLength = value; Rebuild(); });
        ui.Slider("Head Height (m)", HeightAboveBelt, 0.15f, 0.9f, 0.02f,
                  value => { HeightAboveBelt = value; Rebuild(); });
    }

    public PartOperation? Operation => new("scanner", "enable");

    public void Operate(PartOperate op) => op.ToggleBit("enable");
}
