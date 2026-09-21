using FactoryForge.TagBus;
using Godot;

namespace FactoryForge.Parts;

/// <summary>
/// A double-acting cylinder on a double-solenoid 5/2 valve, with reed switches.
///
/// The pusher already in the library is the single-acting kind: one coil, a
/// spring return, and "not extended" is as good as "retracted". This is the
/// other half of pneumatics and the half that catches people out.
///
/// <b>Two coils, and a spool that stays put.</b> A double-solenoid 5/2 valve has
/// no spring. Energise <c>extend</c> and the spool moves and stays moved — let
/// go of the coil and the rod keeps going, because the valve is still ported
/// that way. Energise both and the spool does not move at all: the two
/// solenoids fight and whichever got there first wins. A student who writes
/// "set extend, then immediately set retract" gets a machine that does whatever
/// the previous scan told it, which is a real and very confusing bug.
///
/// <b>Two reeds, and a gap between them.</b> Mid-stroke, <em>neither</em> reed
/// is made. <c>!extended</c> does not mean retracted and never did; the
/// single-acting pusher lets you get away with believing it because its spring
/// always finishes the job. Sequencing off one reed and inferring the other is
/// the mistake, and it is only visible on a cylinder that can stop in the
/// middle — which this one does, on a fault, exactly where it was.
/// </summary>
public partial class PneumaticCylinder : Node3D, IPart
{
    [Export] public float StrokeLength { get; set; } = 0.40f;

    /// <summary>Rod speed, metres per second, with the flow controls as they
    /// are. Both directions are the same here; a real cylinder's two are
    /// adjusted separately, which is a refinement and not a lesson.</summary>
    [Export] public float RodSpeed { get; set; } = 0.9f;

    /// <summary>Face plate width across the lane.</summary>
    [Export] public float PlateWidth { get; set; } = 0.30f;

    /// <summary>How close to an end the rod has to be for the reed to make,
    /// metres. Reed switches have a real sensing band and it is adjustable,
    /// which is why one that has crept out of position is a classic
    /// fault.</summary>
    [Export] public float ReedBand { get; set; } = 0.012f;

    /// <summary>Position along the stroke, metres from fully retracted.</summary>
    public float Extension { get; private set; }

    /// <summary>The reed switches. Both false mid-stroke, which is the whole
    /// point.</summary>
    public bool IsExtended => Extension >= StrokeLength - ReedBand;
    public bool IsRetracted => Extension <= ReedBand;

    /// <summary>Which way the valve spool is ported: true = extend. It survives
    /// both coils dropping out, because a 5/2 double-solenoid valve has no
    /// spring to bring it back.</summary>
    public bool SpoolExtends { get; private set; }

    /// <summary>A seized cylinder stops where it stands, whatever the valve is
    /// told. Not "returns home": a stuck actuator is dangerous precisely
    /// because it does not go anywhere safe on its own, and a rod frozen
    /// between the two reeds is the failure a student has to notice from the
    /// switches rather than from the command.</summary>
    public bool IsFaulted { get; private set; }

    private const float AxisY = 0.12f;
    private const float BarrelLength = 0.26f;
    private const float BarrelRadius = 0.045f;
    private const float PlateThickness = 0.03f;
    private const float PlateHeight = 0.16f;

    private CylinderMesh _rodMesh = null!;
    private MeshInstance3D _rod = null!;
    private AnimatableBody3D _head = null!;
    private StandardMaterial3D _extendReedMat = null!;
    private StandardMaterial3D _retractReedMat = null!;
    private StandardMaterial3D? _faultLampMat;

    public override void _Ready()
    {
        var steelMat = new StandardMaterial3D
        {
            AlbedoColor = new Color(0.70f, 0.72f, 0.76f),
            Metallic = 0.75f,
            Roughness = 0.28f,
        };
        var trimMat = new StandardMaterial3D
        {
            AlbedoColor = new Color(0.20f, 0.22f, 0.25f),
            Metallic = 0.45f,
            Roughness = 0.45f,
        };

        // The barrel lies along +Z, which is the direction the rod strokes, so
        // rotating the part in the editor strokes it the new way.
        AddChild(new MeshInstance3D
        {
            Name = "Barrel",
            Mesh = new CylinderMesh
            {
                TopRadius = BarrelRadius, BottomRadius = BarrelRadius, Height = BarrelLength,
            },
            MaterialOverride = steelMat,
            Position = new Vector3(0, AxisY, -BarrelLength / 2.0f),
            Rotation = new Vector3(Mathf.Pi / 2.0f, 0, 0),
        });

        AddChild(new MeshInstance3D
        {
            Name = "Pedestal",
            Mesh = new BoxMesh { Size = new Vector3(0.10f, AxisY + PartLayout.FloorDrop, 0.10f) },
            MaterialOverride = trimMat,
            Position = new Vector3(0, (AxisY - PartLayout.FloorDrop) / 2.0f, -BarrelLength / 2.0f),
        });

        // The valve block, so the part reads as "cylinder plus valve" rather
        // than as a magic rod. Two coils, one at each end.
        AddChild(new MeshInstance3D
        {
            Name = "ValveBlock",
            Mesh = new BoxMesh { Size = new Vector3(0.16f, 0.06f, 0.06f) },
            MaterialOverride = trimMat,
            Position = new Vector3(0, AxisY + BarrelRadius + 0.04f, -BarrelLength / 2.0f),
        });

        _rodMesh = new CylinderMesh { TopRadius = 0.016f, BottomRadius = 0.016f, Height = 0.02f };
        _rod = new MeshInstance3D
        {
            Name = "Rod",
            Mesh = _rodMesh,
            MaterialOverride = steelMat,
            Rotation = new Vector3(Mathf.Pi / 2.0f, 0, 0),
        };
        AddChild(_rod);

        var plateSize = new Vector3(PlateWidth, PlateHeight, PlateThickness);
        _head = new AnimatableBody3D
        {
            Name = "CylinderHead",
            // On the physics clock, so the plate transfers momentum to what it
            // meets and genuinely blocks what it does not. Without this the
            // plate teleports between frames and cartons tunnel through it.
            SyncToPhysics = true,
        };
        _head.AddChild(new MeshInstance3D
        {
            Name = "FacePlate",
            Mesh = new BoxMesh { Size = plateSize },
            MaterialOverride = new StandardMaterial3D
            {
                AlbedoColor = new Color(0.86f, 0.62f, 0.10f),
                Metallic = 0.40f,
                Roughness = 0.45f,
            },
        });
        _head.AddChild(new CollisionShape3D { Shape = new BoxShape3D { Size = plateSize } });
        AddChild(_head);

        // The two reeds, clipped to the barrel where they really sit.
        _retractReedMat = Lamp(new Color(0.06f, 0.24f, 0.10f));
        _extendReedMat = Lamp(new Color(0.06f, 0.24f, 0.10f));
        AddReed("RetractReed", _retractReedMat, -BarrelLength + 0.03f);
        AddReed("ExtendReed", _extendReedMat, -0.03f);

        BuildFaultLamp(trimMat);
        Apply();
    }

    private void AddReed(string name, StandardMaterial3D mat, float z) =>
        AddChild(new MeshInstance3D
        {
            Name = name,
            Mesh = new SphereMesh { Radius = 0.013f, Height = 0.026f },
            MaterialOverride = mat,
            Position = new Vector3(BarrelRadius + 0.005f, AxisY, z),
        });

    private static StandardMaterial3D Lamp(Color dark) => new()
    {
        AlbedoColor = dark,
        Metallic = 0.10f,
        Roughness = 0.35f,
    };

    private void BuildFaultLamp(StandardMaterial3D trimMat)
    {
        _faultLampMat = Lamp(new Color(0.30f, 0.06f, 0.06f));
        var mount = new Vector3(0, AxisY + BarrelRadius + 0.08f, -BarrelLength / 2.0f);
        const float stalk = 0.06f;

        AddChild(new MeshInstance3D
        {
            Name = "DriveFaultStalk",
            Mesh = new CylinderMesh { TopRadius = 0.007f, BottomRadius = 0.009f, Height = stalk },
            MaterialOverride = trimMat,
            Position = mount + new Vector3(0, stalk / 2, 0),
        });
        AddChild(new MeshInstance3D
        {
            Name = "DriveFaultLamp",
            Mesh = new SphereMesh { Radius = 0.036f, Height = 0.072f },
            MaterialOverride = _faultLampMat,
            Position = mount + new Vector3(0, stalk + 0.024f, 0),
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

    /// <summary>
    /// One tick of the valve and the rod.
    /// </summary>
    public void Step(bool extendCoil, bool retractCoil, float delta)
    {
        // The spool. One coil moves it; two coils fight and it does not move;
        // no coil leaves it where it was, because there is no spring. This is
        // the whole difference from the single-acting pusher and it is three
        // lines.
        if (extendCoil && !retractCoil) SpoolExtends = true;
        else if (retractCoil && !extendCoil) SpoolExtends = false;

        if (IsFaulted) { Apply(); return; }

        float target = SpoolExtends ? StrokeLength : 0.0f;
        Extension = Mathf.MoveToward(Extension, target, Mathf.Max(RodSpeed, 0.0f) * delta);
        Apply();
    }

    private void Apply()
    {
        float rodLength = Mathf.Max(Extension + 0.02f, 0.02f);
        _rodMesh.Height = rodLength;
        _rod.Position = new Vector3(0, AxisY, rodLength / 2.0f);
        _head.Position = new Vector3(0, AxisY, rodLength + PlateThickness / 2.0f);

        SetLamp(_extendReedMat, IsExtended);
        SetLamp(_retractReedMat, IsRetracted);
    }

    private static void SetLamp(StandardMaterial3D? mat, bool on)
    {
        if (mat is null) return;
        var lit = new Color(0.25f, 1.0f, 0.35f);
        var dark = new Color(0.06f, 0.24f, 0.10f);
        mat.AlbedoColor = on ? lit : dark;
        mat.EmissionEnabled = on;
        mat.Emission = on ? lit : Colors.Black;
        mat.EmissionEnergyMultiplier = on ? 2.0f : 0.0f;
    }

    public void ResetCylinder()
    {
        SetFaulted(false);
        SpoolExtends = false;
        Extension = 0.0f;
        Apply();
    }

    // ---------- IPart (HP-34)

    public void DeclareTags(PartTagBuilder tags) => tags
        // Two coils, not one. A program that writes only `extend` and expects
        // the rod to come back has written for a spring-return cylinder.
        .Bit("extend", $"Cylinder {tags.Index} Extend Coil", TagKind.Output)
        .Bit("retract", $"Cylinder {tags.Index} Retract Coil", TagKind.Output)
        // Parked retracted, so the scene starts in the state the geometry
        // shows.
        .Bit("extended", $"Cylinder {tags.Index} Extended Reed", TagKind.Input)
        .Bit("retracted", $"Cylinder {tags.Index} Retracted Reed", TagKind.Input, initial: true)
        .Bit("fault", $"Cylinder {tags.Index} Seized", TagKind.Input);

    public void CaptureSettings(PartSettings settings)
    {
        settings.Put("stroke", StrokeLength);
        settings.Put("rod_speed", RodSpeed);
        settings.Put("plate_width", PlateWidth);
        settings.Put("reed_band", ReedBand);
    }

    public void ApplySettings(PartSettings settings)
    {
        if (settings.Number("stroke") is { } stroke) StrokeLength = stroke;
        if (settings.Number("rod_speed") is { } speed) RodSpeed = speed;
        if (settings.Number("plate_width") is { } width) PlateWidth = width;
        if (settings.Number("reed_band") is { } band) ReedBand = band;
    }

    public void StepPart(PartTick tick)
    {
        if (tick.TryBit("fault", out bool faulted)) SetFaulted(faulted);

        Step(tick.Bit("extend"), tick.Bit("retract"), tick.Dt);

        tick.Write("extended", IsExtended);
        tick.Write("retracted", IsRetracted);
    }

    /// <summary>Plate width is build-time geometry, so it is deliberately not
    /// offered here rather than offered and silently ignored.</summary>
    public void DescribeControls(IPartInspector ui)
    {
        ui.Slider("Stroke (m)", StrokeLength, 0.05f, 1.0f, 0.01f, value => StrokeLength = value);
        ui.Slider("Rod Speed (m/s)", RodSpeed, 0.05f, 3.0f, 0.05f, value => RodSpeed = value);
        // The reed band is the setting a "sequence stops half way" fault is
        // diagnosed with, so it is worth a slider even though it is small.
        ui.Slider("Reed Band (m)", ReedBand, 0.002f, 0.08f, 0.002f, value => ReedBand = value);
    }

    public void ResetPart(PartReset reset)
    {
        ResetCylinder();
        reset.Write("extended", false);
        reset.Write("retracted", true);
    }

    public PartOperation? Operation => new("cylinder", "extend");

    public void Operate(PartOperate op) => op.ToggleBit("extend");
}
