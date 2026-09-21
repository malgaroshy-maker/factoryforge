using FactoryForge.TagBus;
using Godot;

namespace FactoryForge.Parts;

/// <summary>
/// A blade on a pivot that sweeps across the lane (CP-02).
///
/// The pneumatic pusher shoves a stopped box off the side of the belt. A pivot
/// arm does the other thing real sorters do: it deflects a box that is still
/// moving, without the line ever stopping. That makes it a different control
/// problem — the arm has to be out *before* the carton arrives and back home
/// before the next one does, so the two limit switches are the whole exercise
/// rather than a formality.
///
/// Local space follows the pusher's convention: the origin is the mounting
/// point at the near edge of the belt, the post stands there, and the blade
/// sweeps from parked (along the lane, in -X) out across the lane.
/// </summary>
public partial class PivotDiverter : Node3D, IPart
{
    /// <summary>Angle the blade swings to, in degrees. 45° is the classic
    /// deflector; steeper stops the carton instead of turning it.</summary>
    [Export] public float DivertAngle { get; set; } = 45.0f;

    /// <summary>Sweep rate, degrees per second.</summary>
    [Export] public float SwingSpeed { get; set; } = 220.0f;

    /// <summary>Blade length along the lane. Long enough to span a carton and
    /// keep contact through the turn.</summary>
    [Export] public float BladeLength { get; set; } = 0.44f;

    private const float BladeHeight = 0.16f;
    private const float BladeThickness = 0.035f;

    /// <summary>Blade centre height above the part origin, aligned with the
    /// middle of a short carton so contact is a push and not a trip.</summary>
    private float BladeY => PartLayout.BeltSurface + BladeHeight / 2.0f + 0.005f;

    private Node3D _pivot = null!;
    private AnimatableBody3D _blade = null!;
    private StandardMaterial3D? _faultLampMat;

    private float _angle;

    /// <summary>Blade angle in degrees, 0 at home. Exposed so a test can watch
    /// a seizure freeze it mid-sweep — the limit switches alone cannot tell
    /// "stuck at 20°" from "still travelling".</summary>
    public float Angle => _angle;

    public bool IsDiverted => _angle >= DivertAngle - 0.5f;
    public bool IsHome => _angle <= 0.5f;

    /// <summary>Seized: the arm stops wherever it is and stays there, whatever
    /// the valve is told — the same failure model as the pusher's jam, and
    /// nastier here because a blade frozen half across a *running* lane keeps
    /// hitting cartons at an angle nobody commanded.</summary>
    public bool IsFaulted { get; private set; }

    public override void _Ready()
    {
        var postMat = new StandardMaterial3D
        {
            AlbedoColor = new Color(0.20f, 0.22f, 0.25f),
            Metallic = 0.45f,
            Roughness = 0.50f,
        };
        var bladeMat = new StandardMaterial3D
        {
            AlbedoColor = new Color(0.95f, 0.75f, 0.10f),
            Metallic = 0.25f,
            Roughness = 0.40f,
        };

        // Pivot post, from the floor up to the blade.
        float postHeight = PartLayout.FloorDrop + BladeY + BladeHeight / 2.0f;
        AddChild(new MeshInstance3D
        {
            Name = "PivotPost",
            Mesh = new CylinderMesh { TopRadius = 0.035f, BottomRadius = 0.045f, Height = postHeight },
            MaterialOverride = postMat,
            Position = new Vector3(0, BladeY + BladeHeight / 2.0f - postHeight / 2.0f, 0),
        });
        AddChild(new MeshInstance3D
        {
            Name = "BaseFoot",
            Mesh = new CylinderMesh { TopRadius = 0.11f, BottomRadius = 0.11f, Height = 0.025f },
            MaterialOverride = postMat,
            Position = new Vector3(0, -PartLayout.FloorDrop, 0),
        });

        // Rotary actuator body at the top of the post, so the blade reads as
        // driven rather than as a stick that happens to move.
        AddChild(new MeshInstance3D
        {
            Name = "RotaryActuator",
            Mesh = new CylinderMesh { TopRadius = 0.055f, BottomRadius = 0.055f, Height = 0.07f },
            MaterialOverride = postMat,
            Position = new Vector3(0, BladeY + BladeHeight / 2.0f + 0.05f, 0),
        });

        // The pivot node carries the rotation; the blade body hangs off it at
        // half its own length so the arm sweeps about the post rather than
        // about its own centre.
        _pivot = new Node3D { Name = "BladePivot", Position = new Vector3(0, BladeY, 0) };
        AddChild(_pivot);

        _blade = new AnimatableBody3D
        {
            Name = "DiverterBlade",
            // On the physics clock, so the blade transfers momentum to the
            // carton it meets instead of teleporting through it between frames
            // — the same reason the pusher's face plate is an AnimatableBody3D.
            SyncToPhysics = true,
            Position = new Vector3(-BladeLength / 2.0f, 0, 0),
        };
        var bladeSize = new Vector3(BladeLength, BladeHeight, BladeThickness);
        _blade.AddChild(new MeshInstance3D { Mesh = new BoxMesh { Size = bladeSize }, MaterialOverride = bladeMat });
        _blade.AddChild(new CollisionShape3D { Shape = new BoxShape3D { Size = bladeSize } });
        // A low-friction face: the blade is there to turn a moving carton, not
        // to grab it. At the belt's own 0.7 the box stalls against the blade
        // and the line backs up instead of sorting.
        _blade.PhysicsMaterialOverride = new PhysicsMaterial { Friction = 0.12f, Bounce = 0.0f };
        _pivot.AddChild(_blade);

        BuildFaultLamp();
        ApplyAngle();
    }

    private void BuildFaultLamp()
    {
        _faultLampMat = new StandardMaterial3D
        {
            AlbedoColor = new Color(0.30f, 0.06f, 0.06f),
            Metallic = 0.10f,
            Roughness = 0.35f,
        };
        float lampY = BladeY + BladeHeight / 2.0f + 0.13f;
        AddChild(new MeshInstance3D
        {
            Name = "DriveFaultLamp",
            Mesh = new SphereMesh { Radius = 0.038f, Height = 0.076f },
            MaterialOverride = _faultLampMat,
            Position = new Vector3(0, lampY, 0),
        });
    }

    public void SetFaulted(bool faulted)
    {
        if (faulted == IsFaulted && _faultLampMat is not null) return;
        IsFaulted = faulted;

        if (_faultLampMat is null) return;
        _faultLampMat.AlbedoColor = faulted ? new Color(1.0f, 0.15f, 0.12f) : new Color(0.30f, 0.06f, 0.06f);
        _faultLampMat.EmissionEnabled = faulted;
        _faultLampMat.Emission = faulted ? new Color(1.0f, 0.15f, 0.12f) : Colors.Black;
        _faultLampMat.EmissionEnergyMultiplier = faulted ? 2.4f : 0.0f;
    }

    public void UpdateSwing(bool divert, float delta)
    {
        if (IsFaulted) return;

        float target = divert ? DivertAngle : 0.0f;
        _angle = Mathf.MoveToward(_angle, target, SwingSpeed * delta);
        ApplyAngle();
    }

    private void ApplyAngle()
    {
        if (_pivot is null) return;
        // The blade parks along -X and the lane lies in +Z (the pusher's
        // convention: the origin is the near edge, the machine is behind it).
        // A positive turn about +Y therefore carries the tip out across the
        // lane; a negative one would sweep it away from the belt entirely.
        _pivot.Rotation = new Vector3(0, Mathf.DegToRad(_angle), 0);
    }

    // ---------- IPart (HP-34)

    public void DeclareTags(PartTagBuilder tags) => tags
        .Bit("divert", $"Diverter {tags.Index} (Divert)", TagKind.Output)
        .Bit("diverted", $"Diverter {tags.Index} (Diverted)", TagKind.Input)
        // Parked, so the scene starts in the state the geometry shows.
        .Bit("home", $"Diverter {tags.Index} (Home)", TagKind.Input, initial: true)
        .Bit("fault", $"Diverter {tags.Index} Drive Fault", TagKind.Input);

    public void CaptureSettings(PartSettings settings)
    {
        settings.Put("divert_angle", DivertAngle);
        settings.Put("swing_speed", SwingSpeed);
        settings.Put("blade_length", BladeLength);
    }

    public void ApplySettings(PartSettings settings)
    {
        if (settings.Number("divert_angle") is { } angle) DivertAngle = angle;
        if (settings.Number("swing_speed") is { } swing) SwingSpeed = swing;
        if (settings.Number("blade_length") is { } blade) BladeLength = blade;
    }

    public void StepPart(PartTick tick)
    {
        if (!tick.TryBit("divert", out bool divert)) return;

        if (tick.TryBit("fault", out bool faulted)) SetFaulted(faulted);
        UpdateSwing(divert, tick.Dt);
        tick.Write("diverted", IsDiverted);
        tick.Write("home", IsHome);
    }

    public void DescribeControls(IPartInspector ui)
    {
        // Both are read live by UpdateSwing, so neither needs a rebuild; the
        // blade length is geometry and does, so it is deliberately not offered
        // here rather than offered and silently ignored.
        ui.Slider("Divert Angle (deg)", DivertAngle, 10.0f, 80.0f, 1.0f,
                  value => DivertAngle = value);
        ui.Slider("Swing Speed (deg/s)", SwingSpeed, 30.0f, 600.0f, 10.0f,
                  value => SwingSpeed = value);
    }

    public PartOperation? Operation => new("diverter", "divert");

    public void Operate(PartOperate op) => op.ToggleBit("divert");
}
