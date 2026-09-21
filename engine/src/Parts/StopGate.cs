using FactoryForge.TagBus;
using Godot;

namespace FactoryForge.Parts;

/// <summary>
/// A pneumatic blade stop that rises through the lane (LP-01).
///
/// Every other actuator in the library takes product *off* the line — the
/// pusher shoves it sideways, the diverter deflects it, the gantry lifts it
/// away. This is the one that keeps it on, and it is the single most common
/// device on a real conveyor: hold what arrives, release one, hold the next.
/// Without it there is no way to build an accumulation buffer here at all,
/// which means there is no way to teach singulation, and singulation is most
/// of what a conveyor program does.
///
/// The blade is an <see cref="AnimatableBody3D"/>, so the carton is stopped by
/// the solver rather than by anything that knows what a carton is: the belt
/// keeps driving underneath, the box scuffs, and the queue behind it builds up
/// because the physics says so. Drop the blade and the ones behind close up on
/// their own.
///
/// Local space: the origin is the lane centre, on the work plane, so the gate
/// is placed on the same grid cell as the belt it interrupts. The blade parks
/// below the deck line and rises through it.
/// </summary>
public partial class StopGate : Node3D, IPart
{
    /// <summary>How far the blade travels, in metres. The parked position is
    /// fixed at just below the deck, so this is what decides how far it stands
    /// proud of the belt when it is up.
    ///
    /// The default leaves the blade 0.14 m above the belt surface. That is not
    /// a round number, it is the shortest carton (0.10 m) plus margin — a
    /// stroke that stands exactly as proud as the carton is tall catches it on
    /// the very top edge and tips it over the blade instead of holding it.
    /// </summary>
    [Export] public float Stroke { get; set; } = 0.26f;

    /// <summary>Rod speed, m/s, before the cushion profile is applied.</summary>
    [Export] public float LiftSpeed { get; set; } = 0.9f;

    /// <summary>Blade width across the lane. A shade under the standard belt
    /// width: wide enough that a carton riding either rail still meets it
    /// square, narrow enough that the parked blade is hidden by the deck rather
    /// than showing its ends either side of a belt it is supposed to be under.
    /// </summary>
    [Export] public float BladeWidth { get; set; } = PartLayout.StandardBeltWidth - 0.04f;

    private const float BladeHeight = 0.16f;
    private const float BladeThickness = 0.035f;

    /// <summary>Blade centre height when parked: its top sits level with the
    /// underside of a conveyor deck, so nothing on the belt can touch it.</summary>
    private float ParkedY => -(PartLayout.BeltThickness / 2.0f) - BladeHeight / 2.0f;

    private AnimatableBody3D _blade = null!;
    private MeshInstance3D _rod = null!;
    private CylinderMesh _rodMesh = null!;
    private StandardMaterial3D? _faultLampMat;

    private float _lift;

    /// <summary>How far the blade has risen, in metres. Exposed because the two
    /// limit switches cannot tell "seized at 60 %" from "still travelling",
    /// which is exactly what a test of a jam needs to see.</summary>
    public float Lift => _lift;

    public bool IsUp => _lift >= Stroke - 0.01f;
    public bool IsDown => _lift <= 0.01f;

    /// <summary>Seized: the blade holds whatever height it had. A stop frozen
    /// halfway is the nastiest of the library's failures, because it still
    /// stops short cartons and lets tall ones ride over — an intermittent
    /// blockage that reads as a sensor problem.</summary>
    public bool IsFaulted { get; private set; }

    public override void _Ready()
    {
        var bodyMat = new StandardMaterial3D
        {
            AlbedoColor = new Color(0.20f, 0.22f, 0.25f),
            Metallic = 0.45f,
            Roughness = 0.50f,
        };
        var chromeMat = new StandardMaterial3D
        {
            AlbedoColor = new Color(0.72f, 0.74f, 0.78f),
            Metallic = 0.85f,
            Roughness = 0.20f,
        };
        var bladeMat = new StandardMaterial3D
        {
            AlbedoColor = new Color(0.95f, 0.75f, 0.10f),
            Metallic = 0.25f,
            Roughness = 0.40f,
        };

        // The cylinder body hangs under the deck where a real one is bolted to
        // the conveyor frame, clear of anything riding the belt.
        const float barrelHeight = 0.20f;
        float barrelY = ParkedY - BladeHeight / 2.0f - barrelHeight / 2.0f - 0.02f;
        AddChild(new MeshInstance3D
        {
            Name = "CylinderBody",
            Mesh = new BoxMesh { Size = new Vector3(0.12f, barrelHeight, 0.14f) },
            MaterialOverride = bodyMat,
            Position = new Vector3(0, barrelY, 0),
        });
        AddChild(new MeshInstance3D
        {
            Name = "MountBracket",
            Mesh = new BoxMesh { Size = new Vector3(BladeWidth + 0.06f, 0.03f, 0.10f) },
            MaterialOverride = bodyMat,
            Position = new Vector3(0, barrelY - barrelHeight / 2.0f, 0),
        });

        _rodMesh = new CylinderMesh { TopRadius = 0.016f, BottomRadius = 0.016f, Height = 0.10f };
        _rod = new MeshInstance3D { Name = "Rod", Mesh = _rodMesh, MaterialOverride = chromeMat };
        AddChild(_rod);

        _blade = new AnimatableBody3D
        {
            Name = "StopBlade",
            // On the physics clock: a blade that teleports between frames
            // passes straight through the carton it is supposed to catch.
            SyncToPhysics = true,
        };
        var bladeSize = new Vector3(BladeThickness, BladeHeight, BladeWidth);
        _blade.AddChild(new MeshInstance3D { Mesh = new BoxMesh { Size = bladeSize }, MaterialOverride = bladeMat });
        _blade.AddChild(new CollisionShape3D { Shape = new BoxShape3D { Size = bladeSize } });
        // Low friction on the face, for the same reason the diverter's blade
        // has it: the belt is still driving the carton into this, and at the
        // belt's own grip the box climbs the blade instead of resting against
        // it.
        _blade.PhysicsMaterialOverride = new PhysicsMaterial { Friction = 0.15f, Bounce = 0.0f };
        AddChild(_blade);

        BuildFaultLamp();
        ApplyLift();
    }

    private void BuildFaultLamp()
    {
        _faultLampMat = new StandardMaterial3D
        {
            AlbedoColor = new Color(0.30f, 0.06f, 0.06f),
            Metallic = 0.10f,
            Roughness = 0.35f,
        };
        // Beside the lane rather than over it: a beacon above a stop gate is a
        // beacon a tall carton collects.
        var mount = new Vector3(0, ParkedY, BladeWidth / 2.0f + 0.07f);
        const float stalk = 0.22f;

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
            Mesh = new SphereMesh { Radius = 0.038f, Height = 0.076f },
            MaterialOverride = _faultLampMat,
            Position = mount + new Vector3(0, stalk + 0.026f, 0),
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

    /// <summary>Fraction of the stroke spent accelerating off the seal and
    /// decelerating into the cushion, matching the pusher (CP-11).</summary>
    private const float CushionFraction = 0.18f;
    private const float MinSpeedFactor = 0.35f;

    public void UpdateLift(bool raise, float delta)
    {
        if (IsFaulted) return;

        float target = raise ? Stroke : 0.0f;
        float origin = raise ? 0.0f : Stroke;

        float cushion = Mathf.Max(Stroke * CushionFraction, 0.01f);
        float travelled = Mathf.Abs(_lift - origin);
        float remaining = Mathf.Abs(target - _lift);
        float factor = Mathf.Min(1.0f, remaining / cushion) * Mathf.Min(1.0f, travelled / cushion);
        factor = Mathf.Max(factor, MinSpeedFactor);

        _lift = Mathf.MoveToward(_lift, target, LiftSpeed * factor * delta);
        ApplyLift();
    }

    /// <summary>Drop the blade without ceremony, for a scene reset.</summary>
    public void ResetGate()
    {
        _lift = 0.0f;
        ApplyLift();
    }

    private void ApplyLift()
    {
        if (_blade is null) return;

        float bladeY = ParkedY + _lift;
        _blade.Position = new Vector3(0, bladeY, 0);

        // The rod grows out of the barrel rather than sliding as a fixed stick,
        // so the join stays covered at every height.
        float rodLength = 0.10f + _lift;
        _rodMesh.Height = rodLength;
        _rod.Position = new Vector3(0, bladeY - BladeHeight / 2.0f - rodLength / 2.0f, 0);
    }

    // ---------- IPart (HP-34)

    public void DeclareTags(PartTagBuilder tags) => tags
        .Bit("raise", $"Stop {tags.Index} (Raise)", TagKind.Output)
        .Bit("up", $"Stop {tags.Index} (Blade Up)", TagKind.Input)
        // Parked, so the scene starts in the state the geometry shows.
        .Bit("down", $"Stop {tags.Index} (Blade Down)", TagKind.Input, initial: true)
        .Bit("fault", $"Stop {tags.Index} Drive Fault", TagKind.Input);

    public void CaptureSettings(PartSettings settings)
    {
        settings.Put("stroke", Stroke);
        settings.Put("lift_speed", LiftSpeed);
        settings.Put("blade_width", BladeWidth);
    }

    public void ApplySettings(PartSettings settings)
    {
        if (settings.Number("stroke") is { } stroke) Stroke = stroke;
        if (settings.Number("lift_speed") is { } lift) LiftSpeed = lift;
        if (settings.Number("blade_width") is { } width) BladeWidth = width;
    }

    public void StepPart(PartTick tick)
    {
        if (!tick.TryBit("raise", out bool raise)) return;

        if (tick.TryBit("fault", out bool faulted)) SetFaulted(faulted);
        UpdateLift(raise, tick.Dt);
        tick.Write("up", IsUp);
        tick.Write("down", IsDown);
    }

    /// <summary>Blade width is build-time geometry, so a slider for it would
    /// move and change nothing — the exact failure LE-01 shipped. It stays in
    /// the scene file and out of this panel until the part grows a
    /// rebuild.</summary>
    public void DescribeControls(IPartInspector ui)
    {
        ui.Slider("Stroke (m)", Stroke, 0.08f, 0.45f, 0.01f, value => Stroke = value);
        ui.Slider("Lift Speed (m/s)", LiftSpeed, 0.2f, 3.0f, 0.1f, value => LiftSpeed = value);
    }

    public void ResetPart(PartReset reset)
    {
        ResetGate();
        reset.Write("up", false);
        reset.Write("down", true);
    }

    public PartOperation? Operation => new("blade stop", "raise");

    public void Operate(PartOperate op) => op.ToggleBit("raise");
}
