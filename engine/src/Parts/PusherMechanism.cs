using FactoryForge.TagBus;
using Godot;

namespace FactoryForge.Parts;

/// <summary>
/// Pneumatic pusher mechanism with physics body and extend/retract limit switch state.
///
/// Local space convention: the origin is the mounting point at the near edge of
/// the belt. The barrel sits behind it (-Z) and the rod strokes towards the belt
/// (+Z), so placing the node at the belt edge is all a scene needs to do. Never
/// place it at the belt centre — the barrel would straddle the lane.
/// </summary>
public partial class PusherMechanism : Node3D, IPart
{
    [Export] public float StrokeLength { get; set; } = 0.55f;
    [Export] public float ExtendSpeed { get; set; } = 1.6f;

    /// <summary>
    /// True when this part is only a *view* of a pusher the simulation already
    /// owns: it animates from the extend tag but never writes the limit-switch
    /// tags back, so there is exactly one writer per tag.
    /// </summary>
    [Export] public bool VisualOnly { get; set; }

    /// <summary>Rod left showing when fully retracted — a real cylinder never
    /// swallows its rod completely.</summary>
    private const float RestGap = 0.06f;
    private const float PlateThickness = 0.05f;
    private const float BarrelDepth = 0.30f;
    private const float BarrelHeight = 0.20f;

    /// <summary>Clearance between the belt surface and the bottom of the plate,
    /// so the plate sweeps the box without scraping the belt.</summary>
    private const float BeltClearance = 0.01f;

    /// <summary>Tallest carton the diverter is built to handle. The face plate is
    /// sized to it: a plate shorter than the carton contacts below the centre of
    /// mass and topples the box instead of sliding it, which is exactly what a
    /// 0.20 m plate did to a 0.30 m carton.</summary>
    [Export] public float MaxCartonHeight { get; set; } = 0.30f;

    private float PlateHeight => MaxCartonHeight - 2 * BeltClearance;

    /// <summary>Rod axis height relative to the part origin. Aligned with the
    /// centre of mass of a full-height carton, so the push is a pure translation
    /// with no tipping moment.</summary>
    private float AxisY => PartLayout.BeltSurface + MaxCartonHeight / 2.0f;

    private AnimatableBody3D _pusherHead = null!;
    private MeshInstance3D _rod = null!;
    private CylinderMesh _rodMesh = null!;
    private float _currentExtension;

    /// <summary>How far the rod is out, in metres. Exposed so a test can watch
    /// a jam freeze it mid-stroke — the limit switches alone cannot tell
    /// "stuck at 60%" from "still travelling".</summary>
    public float Extension => _currentExtension;

    public bool IsExtended => _currentExtension >= StrokeLength - 0.01f;
    public bool IsRetracted => _currentExtension <= 0.01f;

    public override void _Ready()
    {
        // The pedestal reaches the floor from the cylinder axis, so the part
        // supports itself wherever it is dropped on the work plane.
        var housing = IndustrialMeshBuilder.BuildPusherHousing(
            BarrelDepth, BarrelHeight, PartLayout.FloorDrop + AxisY);
        housing.Position = new Vector3(0, AxisY, 0);
        AddChild(housing);

        _rod = IndustrialMeshBuilder.BuildPusherRod(out _rodMesh);
        AddChild(_rod);

        var plateSize = new Vector3(0.34f, PlateHeight, PlateThickness);
        _pusherHead = new AnimatableBody3D
        {
            Name = "PusherHead",
            // Move on the physics clock, so the plate transfers momentum to the
            // cartons it sweeps and genuinely blocks the ones it does not. With
            // this off the plate teleports between frames and boxes tunnel or
            // get flung.
            SyncToPhysics = true,
        };
        _pusherHead.AddChild(IndustrialMeshBuilder.BuildPusherFacePlate(plateSize));
        _pusherHead.AddChild(new CollisionShape3D
        {
            Shape = new BoxShape3D { Size = plateSize }
        });
        AddChild(_pusherHead);

        BuildFaultLamp();

        ApplyExtension();
    }

    /// <summary>
    /// A jammed cylinder: it stops wherever it is and stays there, whatever
    /// the valve is told (FI-01). Not "returns home" — a stuck actuator is
    /// dangerous precisely because it does not go anywhere safe on its own,
    /// and a plate frozen out across the lane is the failure a student has to
    /// notice from the limit switches rather than from the command.
    /// </summary>
    public bool IsFaulted { get; private set; }

    private StandardMaterial3D? _faultLampMat;

    /// <summary>The same beacon a conveyor carries, for the same reason: a
    /// cylinder parked at rest and a cylinder seized at rest look identical,
    /// and the difference is the whole diagnosis. A jam mid-stroke is even
    /// easier to misread as "still travelling".</summary>
    private void BuildFaultLamp()
    {
        _faultLampMat = new StandardMaterial3D
        {
            AlbedoColor = new Color(0.30f, 0.06f, 0.06f),
            Metallic = 0.10f,
            Roughness = 0.35f,
        };

        // On top of the barrel housing, where nothing strokes past it.
        var mount = new Vector3(0, AxisY + BarrelHeight / 2.0f, 0);
        const float stalk = 0.07f;

        AddChild(new MeshInstance3D
        {
            Name = "DriveFaultStalk",
            Mesh = new CylinderMesh { TopRadius = 0.008f, BottomRadius = 0.010f, Height = stalk },
            MaterialOverride = new StandardMaterial3D
            {
                AlbedoColor = new Color(0.22f, 0.23f, 0.25f),
                Metallic = 0.40f,
                Roughness = 0.50f,
            },
            Position = mount + new Vector3(0, stalk / 2, 0),
        });

        AddChild(new MeshInstance3D
        {
            Name = "DriveFaultLamp",
            Mesh = new SphereMesh { Radius = 0.042f, Height = 0.084f },
            MaterialOverride = _faultLampMat,
            Position = mount + new Vector3(0, stalk + 0.028f, 0),
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

    /// <summary>Fraction of the stroke over which the cylinder accelerates off
    /// the seal and decelerates into the cushion (CP-11).</summary>
    private const float CushionFraction = 0.18f;

    /// <summary>Floor on the speed multiplier. Without one the eased profile
    /// asymptotes and the rod never quite arrives, so the limit switch never
    /// closes — an easing curve that breaks the machine is not an
    /// improvement.</summary>
    private const float MinSpeedFactor = 0.35f;

    public void UpdateExtension(bool extend, float delta)
    {
        if (IsFaulted) return;

        float target = extend ? StrokeLength : 0.0f;
        float origin = extend ? 0.0f : StrokeLength;

        // A pneumatic cylinder does not move at one speed. It accelerates off
        // the seal, runs, and decelerates into the end cushion; a flat
        // MoveToward reads as a prop being slid by hand, which is what this
        // looked like. Both ends of the profile are ramps, so the plate eases
        // out of rest and settles onto the limit rather than slamming into it.
        float cushion = Mathf.Max(StrokeLength * CushionFraction, 0.01f);
        float travelled = Mathf.Abs(_currentExtension - origin);
        float remaining = Mathf.Abs(target - _currentExtension);
        float factor = Mathf.Min(1.0f, remaining / cushion) * Mathf.Min(1.0f, travelled / cushion);
        factor = Mathf.Max(factor, MinSpeedFactor);

        _currentExtension = Mathf.MoveToward(_currentExtension, target, ExtendSpeed * factor * delta);
        ApplyExtension();
    }

    private void ApplyExtension()
    {
        float rodLength = RestGap + _currentExtension;
        _rodMesh.Height = rodLength;
        _rod.Position = new Vector3(0, AxisY, rodLength / 2.0f);
        _pusherHead.Position = new Vector3(0, AxisY, rodLength + PlateThickness / 2.0f);
    }

    // ---------- IPart (HP-34)

    public void DeclareTags(PartTagBuilder tags) => tags
        .Bit("extend", $"Pusher {tags.Index} (Extend)", TagKind.Output)
        .Bit("extended", $"Pusher {tags.Index} (Extended)", TagKind.Input)
        .Bit("retracted", $"Pusher {tags.Index} (Retracted)", TagKind.Input, initial: true)
        .Bit("fault", $"Pusher {tags.Index} Drive Fault", TagKind.Input);

    public void CaptureSettings(PartSettings settings)
    {
        settings.Put("stroke", StrokeLength);
        settings.Put("speed", ExtendSpeed);
        settings.Put("max_carton", MaxCartonHeight);
        settings.Put("visual_only", VisualOnly);
    }

    public void ApplySettings(PartSettings settings)
    {
        if (settings.Number("stroke") is { } stroke) StrokeLength = stroke;
        if (settings.Number("speed") is { } speed) ExtendSpeed = speed;
        if (settings.Number("max_carton") is { } carton) MaxCartonHeight = carton;
        if (settings.Flag("visual_only") is { } visualOnly) VisualOnly = visualOnly;
    }

    public void StepPart(PartTick tick)
    {
        if (!tick.TryBit("extend", out bool extend)) return;

        if (tick.TryBit("fault", out bool faulted)) SetFaulted(faulted);
        UpdateExtension(extend, tick.Dt);

        // A VisualOnly pusher mirrors a pusher the scene already simulates, so
        // the scene keeps the limit switches.
        if (VisualOnly) return;
        tick.Write("extended", IsExtended);
        tick.Write("retracted", IsRetracted);
    }

    public void DescribeControls(IPartInspector ui)
    {
        ui.Slider("Stroke Speed (m/s)", ExtendSpeed, 0.2f, 5.0f, 0.1f,
                  value => ExtendSpeed = value);
        ui.Slider("Stroke Length (m)", StrokeLength, 0.1f, 1.0f, 0.05f,
                  value => StrokeLength = value);
    }

    public PartOperation? Operation => new("pusher", "extend");

    public void Operate(PartOperate op) => op.ToggleBit("extend");
}
