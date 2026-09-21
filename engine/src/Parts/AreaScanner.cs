using System.Collections.Generic;
using FactoryForge.TagBus;
using Godot;

namespace FactoryForge.Parts;

/// <summary>
/// A safety laser scanner with a warning field, a protective field, and muting.
///
/// The light curtain already here measures the height of what breaks it, which
/// makes it a sorting instrument rather than a guard. A scanner is the other
/// thing: it watches an <em>area</em> and its whole output is "somebody is in
/// the wrong place". Two fields, because a scanner that only ever stopped the
/// line would be switched off within a week — the warning field slows things
/// down or sounds a horn, and the protective field is the one that stops.
///
/// <b>Muting is the part worth teaching.</b> A conveyor has to carry pallets
/// through the guarded opening, and a scanner that stopped the line for every
/// pallet would be useless, so the protective field is bridged while the pallet
/// passes. That is a genuine hole in the guard, and the thing that stops it
/// becoming a permanent hole is a <em>timeout</em>: muting that has been held
/// longer than a pallet takes is muting somebody has taped on, and the scanner
/// stops honouring it. A student who builds a mute without one has built a
/// defeat, and the timeout here is what lets them find that out.
/// </summary>
public partial class AreaScanner : Node3D, IPart
{
    /// <summary>Radius of the protective field, metres. Break this and the
    /// machine stops.</summary>
    [Export] public float StopRadius { get; set; } = 1.2f;

    /// <summary>Radius of the warning field, metres. Always the larger of the
    /// two; a warning field inside the protective one would warn you after it
    /// had already stopped.</summary>
    [Export] public float WarnRadius { get; set; } = 2.0f;

    /// <summary>Scanning arc, degrees, centred on the part's −Z. Real scanners
    /// cover 190°, not 360°, and where the blind sector points is a real
    /// commissioning decision.</summary>
    [Export] public float ScanArc { get; set; } = 190.0f;

    /// <summary>How long the protective field may be muted before the scanner
    /// stops believing it, seconds. Zero disables the timeout, which is the
    /// configuration somebody reaches for when muting keeps tripping and is
    /// exactly the one this part exists to make visible.</summary>
    [Export] public float MuteLimit { get; set; } = 6.0f;

    /// <summary>Is anything inside the protective field?</summary>
    public bool StopFieldBroken { get; private set; }

    /// <summary>Is anything inside the warning field?</summary>
    public bool WarnFieldBroken { get; private set; }

    /// <summary>Is the protective field currently bridged? False once the
    /// timeout has expired, however hard the mute input is held.</summary>
    public bool IsMuted { get; private set; }

    /// <summary>How long muting has been asked for continuously.</summary>
    public float MuteHeldFor { get; private set; }

    private StandardMaterial3D _stopLampMat = null!;
    private StandardMaterial3D _warnLampMat = null!;
    private StandardMaterial3D _muteLampMat = null!;
    private Label3D _readout = null!;
    private MeshInstance3D _stopField = null!;
    private MeshInstance3D _warnField = null!;

    private const float HeadY = 0.22f;

    /// <summary>The scan sweeps the floor, so anything above roughly knee
    /// height on a carton is still the same carton. Heights are compared
    /// against the scanner's own plane with this much tolerance either way.
    /// </summary>
    private const float PlaneTolerance = 0.9f;

    public override void _Ready()
    {
        var caseMat = new StandardMaterial3D
        {
            AlbedoColor = new Color(0.94f, 0.72f, 0.10f),
            Metallic = 0.25f,
            Roughness = 0.55f,
        };
        var trimMat = new StandardMaterial3D
        {
            AlbedoColor = new Color(0.14f, 0.15f, 0.17f),
            Metallic = 0.45f,
            Roughness = 0.45f,
        };

        AddChild(new MeshInstance3D
        {
            Name = "ScannerHead",
            Mesh = new BoxMesh { Size = new Vector3(0.14f, 0.16f, 0.14f) },
            MaterialOverride = caseMat,
            Position = new Vector3(0, HeadY, 0),
        });
        AddChild(new MeshInstance3D
        {
            Name = "ScannerWindow",
            Mesh = new CylinderMesh { TopRadius = 0.065f, BottomRadius = 0.065f, Height = 0.05f },
            MaterialOverride = trimMat,
            Position = new Vector3(0, HeadY + 0.045f, 0),
        });
        AddChild(new MeshInstance3D
        {
            Name = "Post",
            Mesh = new CylinderMesh
            {
                TopRadius = 0.026f, BottomRadius = 0.034f,
                Height = HeadY + PartLayout.FloorDrop,
            },
            MaterialOverride = trimMat,
            Position = new Vector3(0, (HeadY - PartLayout.FloorDrop) / 2.0f, 0),
        });

        // The fields, drawn. A guard whose reach you cannot see is a guard
        // somebody commissions by trial and error -- and the two radii being
        // visible is what makes "the warning field is inside the protective
        // one" an obvious mistake rather than a subtle one.
        _warnField = BuildField(WarnRadius, new Color(1.0f, 0.80f, 0.20f, 0.10f));
        _stopField = BuildField(StopRadius, new Color(1.0f, 0.25f, 0.20f, 0.14f));
        AddChild(_warnField);
        AddChild(_stopField);

        _stopLampMat = Lamp(new Color(0.28f, 0.06f, 0.06f));
        _warnLampMat = Lamp(new Color(0.30f, 0.24f, 0.05f));
        _muteLampMat = Lamp(new Color(0.06f, 0.18f, 0.28f));

        AddLed("StopLamp", _stopLampMat, -0.045f);
        AddLed("WarnLamp", _warnLampMat, 0.0f);
        AddLed("MuteLamp", _muteLampMat, 0.045f);

        _readout = new Label3D
        {
            Name = "ScannerReadout",
            Text = "CLEAR",
            Position = new Vector3(0, HeadY + 0.20f, 0),
            Billboard = BaseMaterial3D.BillboardModeEnum.Enabled,
            FontSize = 78,
            PixelSize = 0.0015f,
            Modulate = new Color(1.0f, 0.85f, 0.35f),
        };
        AddChild(_readout);

        Apply();
    }

    private MeshInstance3D BuildField(float radius, Color tint) => new()
    {
        Name = $"Field{radius:0.00}",
        Mesh = new CylinderMesh
        {
            TopRadius = Mathf.Max(radius, 0.05f),
            BottomRadius = Mathf.Max(radius, 0.05f),
            Height = 0.004f,
        },
        MaterialOverride = new StandardMaterial3D
        {
            AlbedoColor = tint,
            Transparency = BaseMaterial3D.TransparencyEnum.Alpha,
            ShadingMode = BaseMaterial3D.ShadingModeEnum.Unshaded,
            CullMode = BaseMaterial3D.CullModeEnum.Disabled,
        },
        Position = new Vector3(0, -PartLayout.FloorDrop + 0.01f, 0),
    };

    private void AddLed(string name, StandardMaterial3D mat, float x) =>
        AddChild(new MeshInstance3D
        {
            Name = name,
            Mesh = new SphereMesh { Radius = 0.014f, Height = 0.028f },
            MaterialOverride = mat,
            Position = new Vector3(x, HeadY - 0.05f, 0.075f),
        });

    private static StandardMaterial3D Lamp(Color dark) => new()
    {
        AlbedoColor = dark,
        Metallic = 0.10f,
        Roughness = 0.35f,
    };

    /// <summary>How often the scanner looks for bodies again. Cartons move, so
    /// this is every tick; the list of candidates is not rebuilt from the whole
    /// tree, only walked.</summary>
    public void Scan(bool muteRequested, float delta)
    {
        WarnFieldBroken = false;
        StopFieldBroken = false;

        Vector3 here = GlobalPosition;
        float halfArc = Mathf.DegToRad(Mathf.Clamp(ScanArc, 1.0f, 360.0f)) / 2.0f;
        Vector3 facing = -GlobalBasis.Z;

        foreach (var body in Intruders())
        {
            Vector3 to = body.GlobalPosition - here;
            if (Mathf.Abs(to.Y) > PlaneTolerance) continue;

            var flat = new Vector3(to.X, 0, to.Z);
            float distance = flat.Length();
            if (distance > WarnRadius) continue;

            // Outside the blind sector. A scanner mounted facing the wrong way
            // sees nothing at all, which is worth being able to reproduce.
            var facingFlat = new Vector3(facing.X, 0, facing.Z);
            if (!facingFlat.IsZeroApprox() && !flat.IsZeroApprox()
                && Mathf.Acos(Mathf.Clamp(facingFlat.Normalized().Dot(flat.Normalized()), -1.0f, 1.0f))
                   > halfArc)
                continue;

            WarnFieldBroken = true;
            if (distance <= StopRadius) StopFieldBroken = true;
        }

        // Muting, and its timeout. Held rather than latched, so releasing the
        // mute input resets the clock -- a pallet at a time is fine, a pallet's
        // worth of time held forever is not.
        if (muteRequested)
        {
            MuteHeldFor += delta;
            IsMuted = MuteLimit <= 0.0f || MuteHeldFor <= MuteLimit;
        }
        else
        {
            MuteHeldFor = 0.0f;
            IsMuted = false;
        }

        Apply();
    }

    /// <summary>
    /// Everything the scanner could see. Cartons only: a scanner that tripped on
    /// the machines it is guarding would never clear.
    ///
    /// The scanner's own siblings, not a walk of the whole tree. Cartons are
    /// added to the scene root by whoever spawns them, which is the same node
    /// the editor's parts are added to and the same place
    /// <c>SceneEditor.SweepBoxes</c> already looks — so one level is the whole
    /// answer. A recursive walk would be a per-node allocation on every scanner
    /// on every tick, which is the cost FF-15 and FF-16 were both about, and
    /// this is the one part here that has to look every tick rather than once a
    /// second: a guard that noticed an intrusion a quarter of a second late
    /// would be a guard in name only.
    /// </summary>
    private IEnumerable<Node3D> Intruders()
    {
        Node? root = GetParent();
        if (root is null) yield break;

        foreach (var node in root.GetChildren())
        {
            if (node is BoxPhysics box) yield return box;
        }
    }

    /// <summary>Is the protective output clear? Normally closed, like every
    /// other safety contact here: true means safe, so a broken circuit reads as
    /// an intrusion.</summary>
    public bool StopOutputClear => !StopFieldBroken || IsMuted;

    private void Apply()
    {
        SetLamp(_stopLampMat, !StopOutputClear, new Color(1.0f, 0.20f, 0.15f), new Color(0.28f, 0.06f, 0.06f));
        SetLamp(_warnLampMat, WarnFieldBroken, new Color(1.0f, 0.82f, 0.20f), new Color(0.30f, 0.24f, 0.05f));
        SetLamp(_muteLampMat, IsMuted, new Color(0.30f, 0.70f, 1.0f), new Color(0.06f, 0.18f, 0.28f));

        if (_readout is null) return;
        string text = !StopOutputClear ? "STOP"
            : IsMuted ? "MUTED"
            : WarnFieldBroken ? "WARN" : "CLEAR";
        if (_readout.Text != text) _readout.Text = text;
    }

    private static void SetLamp(StandardMaterial3D? mat, bool on, Color lit, Color dark)
    {
        if (mat is null) return;
        mat.AlbedoColor = on ? lit : dark;
        mat.EmissionEnabled = on;
        mat.Emission = on ? lit : Colors.Black;
        mat.EmissionEnergyMultiplier = on ? 2.2f : 0.0f;
    }

    /// <summary>Rebuild the drawn fields after a radius changes. Both are read
    /// only while the discs are built, so a slider without this would move a
    /// number the picture never followed -- the failure LE-01 shipped.</summary>
    public void Rebuild()
    {
        foreach (var child in new[] { _warnField, _stopField })
        {
            if (child is null) continue;
            RemoveChild(child);
            child.QueueFree();
        }
        _warnField = BuildField(WarnRadius, new Color(1.0f, 0.80f, 0.20f, 0.10f));
        _stopField = BuildField(StopRadius, new Color(1.0f, 0.25f, 0.20f, 0.14f));
        AddChild(_warnField);
        AddChild(_stopField);
    }

    // ---------- IPart (HP-34)

    public void DeclareTags(PartTagBuilder tags) => tags
        // The only thing the controller writes. Everything else is the
        // scanner's verdict, which a program reads and does not compute.
        .Bit("mute", $"Scanner {tags.Index} Mute Request", TagKind.Output)
        // Normally closed: true while the protective field is clear, so a
        // broken circuit reads as an intrusion.
        .Bit("stop", $"Scanner {tags.Index} Protective Field Clear (NC)", TagKind.Input, initial: true)
        .Bit("warn", $"Scanner {tags.Index} Warning Field Broken", TagKind.Input)
        .Bit("muted", $"Scanner {tags.Index} Muting Active", TagKind.Input);

    public void CaptureSettings(PartSettings settings)
    {
        settings.Put("stop_radius", StopRadius);
        settings.Put("warn_radius", WarnRadius);
        settings.Put("scan_arc", ScanArc);
        settings.Put("mute_limit", MuteLimit);
    }

    public void ApplySettings(PartSettings settings)
    {
        if (settings.Number("stop_radius") is { } stop) StopRadius = stop;
        if (settings.Number("warn_radius") is { } warn) WarnRadius = warn;
        if (settings.Number("scan_arc") is { } arc) ScanArc = arc;
        if (settings.Number("mute_limit") is { } limit) MuteLimit = limit;
    }

    public void StepPart(PartTick tick)
    {
        Scan(tick.Bit("mute"), tick.Dt);

        tick.Write("stop", StopOutputClear);
        tick.Write("warn", WarnFieldBroken);
        tick.Write("muted", IsMuted);
    }

    public void DescribeControls(IPartInspector ui)
    {
        ui.Slider("Protective (m)", StopRadius, 0.2f, 6.0f, 0.1f,
                  value => { StopRadius = value; Rebuild(); });
        ui.Slider("Warning (m)", WarnRadius, 0.2f, 8.0f, 0.1f,
                  value => { WarnRadius = value; Rebuild(); });
        ui.Slider("Scan Arc (deg)", ScanArc, 30.0f, 360.0f, 10.0f, value => ScanArc = value);
        ui.Slider("Mute Limit (s)", MuteLimit, 0.0f, 60.0f, 0.5f, value => MuteLimit = value);
    }

    public void ResetPart(PartReset reset)
    {
        MuteHeldFor = 0.0f;
        IsMuted = false;
        StopFieldBroken = false;
        WarnFieldBroken = false;
        Apply();
        reset.Write("stop", true);
        reset.Write("warn", false);
        reset.Write("muted", false);
    }

    public PartOperation? Operation => new("area scanner", "mute");

    public void Operate(PartOperate op) => op.ToggleBit("mute");
}
