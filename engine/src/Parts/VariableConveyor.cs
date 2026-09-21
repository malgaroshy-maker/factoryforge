using FactoryForge.TagBus;
using Godot;

namespace FactoryForge.Parts;

/// <summary>
/// A belt behind a variable-frequency drive (CP-01).
///
/// Every other transport part in the library is a bit — it turns or it does
/// not. A real line is full of VFDs, and the first analog loop most students
/// meet is "ramp the belt to 40 % and read back what it is actually doing".
/// That read-back is the whole point of this part: the drive ramps at
/// <see cref="AccelRate"/> rather than jumping, so the reference and the actual
/// speed genuinely disagree while it is moving between them, and a program that
/// treats the reference as instantly true is wrong in a way it can be shown to
/// be wrong.
///
/// Extends <see cref="ConveyorBelt"/> so the surface-velocity transport, the
/// friction handling, the rotation-follows-the-part rule and the drive-fault
/// beacon are all inherited rather than reimplemented.
/// </summary>
public partial class VariableConveyor : ConveyorBelt
{
    /// <summary>Belt speed at 100 % reference, m/s. The reference tag is a
    /// percentage because that is what a drive takes; this is the scale
    /// factor that turns it into metres per second.</summary>
    [Export] public float MaxSpeed { get; set; } = 0.9f;

    /// <summary>Ramp rate, percent per second, used for both accelerating and
    /// decelerating. 40 %/s gives a 2.5 s ramp across the full range — long
    /// enough to see, short enough not to be tedious.</summary>
    [Export] public float AccelRate { get; set; } = 40.0f;

    /// <summary>Below this the drive is treated as stopped, so a reference of
    /// 0.3 % does not leave the belt creeping and `.actual` hovering just above
    /// zero forever.</summary>
    private const float DeadBand = 0.5f;

    /// <summary>Actual speed, 0–100 %. This is the measured variable, and it
    /// is what the drive reports back — not the reference.</summary>
    public float ActualPercent { get; private set; }

    /// <summary>Reference last commanded, 0–100 %, kept so the drive enclosure
    /// readout and the property panel can show both numbers.</summary>
    public float ReferencePercent { get; private set; }

    private Label3D _readout = null!;
    private MeshInstance3D _fan = null!;
    private float _fanSpin;

    public override void _Ready()
    {
        base._Ready();
        BuildDriveCabinet();
    }

    /// <summary>
    /// The gearmotor and the drive enclosure. A VFD belt that looked exactly
    /// like every other belt would be a part you could only tell apart by
    /// selecting it, and the readout is the honest one — it shows the actual,
    /// not the reference, so the lag is visible in the scene and not only in
    /// the tag list.
    /// </summary>
    private void BuildDriveCabinet()
    {
        var caseMat = new StandardMaterial3D
        {
            AlbedoColor = new Color(0.20f, 0.22f, 0.25f),
            Metallic = 0.45f,
            Roughness = 0.45f,
        };
        var finMat = new StandardMaterial3D
        {
            AlbedoColor = new Color(0.42f, 0.44f, 0.48f),
            Metallic = 0.60f,
            Roughness = 0.35f,
        };

        // Gearmotor, hung off the head end below the deck where a real one is.
        float headX = Size.X / 2 - 0.10f;
        float motorY = -0.16f;

        AddChild(new MeshInstance3D
        {
            Name = "Gearbox",
            Mesh = new BoxMesh { Size = new Vector3(0.16f, 0.16f, 0.14f) },
            MaterialOverride = caseMat,
            Position = new Vector3(headX, motorY, Size.Z / 2 + 0.10f),
        });

        var motor = new MeshInstance3D
        {
            Name = "MotorBody",
            Mesh = new CylinderMesh { TopRadius = 0.062f, BottomRadius = 0.062f, Height = 0.20f },
            MaterialOverride = finMat,
            Position = new Vector3(headX - 0.17f, motorY, Size.Z / 2 + 0.10f),
        };
        motor.RotateZ(Mathf.Pi / 2);
        AddChild(motor);

        // Cooling fan on the motor's non-drive end. It turns with the drive, so
        // a running motor is visible from behind as well as by the belt moving.
        _fan = new MeshInstance3D
        {
            Name = "MotorFan",
            Mesh = new BoxMesh { Size = new Vector3(0.09f, 0.012f, 0.09f) },
            MaterialOverride = new StandardMaterial3D
            {
                AlbedoColor = new Color(0.10f, 0.10f, 0.12f),
                Roughness = 0.6f,
            },
            Position = new Vector3(headX - 0.28f, motorY, Size.Z / 2 + 0.10f),
        };
        _fan.RotateZ(Mathf.Pi / 2);
        AddChild(_fan);

        // Drive enclosure on a stand beside the head end, at reading height.
        const float cabinetY = 0.34f;
        var cabinet = new Node3D { Name = "DriveCabinet", Position = new Vector3(headX, cabinetY, -Size.Z / 2 - 0.14f) };
        AddChild(cabinet);

        cabinet.AddChild(new MeshInstance3D
        {
            Mesh = new BoxMesh { Size = new Vector3(0.24f, 0.30f, 0.12f) },
            MaterialOverride = caseMat,
        });
        cabinet.AddChild(new MeshInstance3D
        {
            Name = "CabinetStand",
            Mesh = new BoxMesh { Size = new Vector3(0.05f, PartLayout.FloorDrop + cabinetY - 0.15f, 0.05f) },
            MaterialOverride = caseMat,
            Position = new Vector3(0, -(PartLayout.FloorDrop + cabinetY - 0.15f) / 2 - 0.15f, 0),
        });
        cabinet.AddChild(new MeshInstance3D
        {
            Name = "CabinetScreen",
            Mesh = new BoxMesh { Size = new Vector3(0.17f, 0.09f, 0.01f) },
            MaterialOverride = new StandardMaterial3D { AlbedoColor = new Color(0.04f, 0.05f, 0.04f) },
            Position = new Vector3(0, 0.07f, -0.062f),
        });

        _readout = new Label3D
        {
            Name = "DriveReadout",
            Text = "0.0%",
            FontSize = 40,
            PixelSize = 0.0018f,
            Modulate = new Color(0.35f, 1.0f, 0.45f),
            Position = new Vector3(0, 0.07f, -0.070f),
            RotationDegrees = new Vector3(0, 180, 0),
        };
        cabinet.AddChild(_readout);
    }

    /// <summary>
    /// One drive tick. The ramp is the part that matters: `actual` chases
    /// `reference` at a bounded rate, so the two disagree for as long as a real
    /// drive's would, and a faulted drive coasts to zero instead of stopping
    /// dead — a spinning mass does not stop the instant its supply drops.
    /// </summary>
    public void StepDrive(bool run, float referencePercent, float delta)
    {
        ReferencePercent = Mathf.Clamp(referencePercent, 0.0f, 100.0f);

        // The fault wins over the command, the same rule and the same ordering
        // as ConveyorBelt.SetRunning — but here it aims the ramp at zero rather
        // than cutting the speed, so a failed drive coasts down visibly.
        float target = (IsFaulted || !run) ? 0.0f : ReferencePercent;
        ActualPercent = Mathf.MoveToward(ActualPercent, target, AccelRate * delta);

        bool moving = ActualPercent > DeadBand;
        Speed = moving ? MaxSpeed * ActualPercent / 100.0f : 0.0f;
        SetRunning(moving);

        if (_readout is not null) _readout.Text = $"{ActualPercent:0.0}%";
    }

    public override void _Process(double delta)
    {
        base._Process(delta);

        // Fan speed follows the actual, not the reference — it is bolted to the
        // shaft, so it is one more honest readout of what the drive is doing.
        if (_fan is null || ActualPercent <= DeadBand) return;
        _fanSpin += ActualPercent * 0.35f * (float)delta;
        _fan.Basis = new Basis(Vector3.Back, Mathf.Pi / 2) * new Basis(Vector3.Up, _fanSpin);
    }

    // ---------- IPart (HP-34)

    /// <summary>The drive recomputes Speed from the reference on every tick, so
    /// it is a sample and not a setting. <c>--self-test=scene</c> asserts its
    /// absence from a saved VFD belt.</summary>
    protected override bool SpeedIsSetting => false;

    /// <summary>`speed` is what the controller asks for and `actual` is what
    /// the drive has managed so far; they are two tags because they are two
    /// different numbers for as long as the ramp is running.</summary>
    public override void DeclareTags(PartTagBuilder tags) => tags
        .Bit("run", $"VFD Conveyor {tags.Index} Run", TagKind.Output)
        .Float("speed", $"VFD Conveyor {tags.Index} Speed Ref (%)", TagKind.Output)
        .Float("actual", $"VFD Conveyor {tags.Index} Actual Speed (%)", TagKind.Input)
        .Bit("fault", $"VFD Conveyor {tags.Index} Drive Fault", TagKind.Input);

    public override void CaptureSettings(PartSettings settings)
    {
        base.CaptureSettings(settings);
        settings.Put("max_speed", MaxSpeed);
        settings.Put("accel_rate", AccelRate);
    }

    public override void ApplySettings(PartSettings settings)
    {
        base.ApplySettings(settings);
        if (settings.Number("max_speed") is { } maxSpeed) MaxSpeed = maxSpeed;
        if (settings.Number("accel_rate") is { } accel) AccelRate = accel;
    }

    public override void StepPart(PartTick tick)
    {
        // Fault first, for the same reason the plain belt does it first: a
        // faulted drive has to refuse the command rather than obey it and be
        // stopped again next tick.
        if (tick.TryBit("fault", out bool faulted)) SetFaulted(faulted);

        StepDrive(tick.Bit("run"), tick.Number("speed"), tick.Dt);
        tick.Write("actual", (double)ActualPercent);
        tick.Host.NoteTransportSpeed(tick.InstanceId, Speed);
    }

    public override void DescribeControls(IPartInspector ui)
    {
        ui.Slider("Max Speed (m/s @100%)", MaxSpeed, 0.1f, 3.0f, 0.05f,
                  value => MaxSpeed = value);
        ui.Slider("Ramp Rate (%/s)", AccelRate, 2.0f, 400.0f, 2.0f, value => AccelRate = value);
        // The base adds the friction row; its speed row is suppressed by
        // SpeedIsSetting above, for the reason given there.
        base.DescribeControls(ui);
    }

    public override PartOperation? Operation => new("VFD conveyor", "run");

    public override void Operate(PartOperate op) => op.ToggleBit("run");
}
