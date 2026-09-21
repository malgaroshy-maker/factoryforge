using FactoryForge.TagBus;
using Godot;

namespace FactoryForge.Parts;

/// <summary>
/// Process tank with an analog level, a modulating fill valve and a modulating
/// drain valve — the classic analog exercise a discrete sorting line cannot
/// teach. Everything else in the library is on/off; this is the part you write
/// a PID against.
///
/// The level is not a straight integrator. Outflow follows Torricelli, so it
/// falls with the square root of the head:
///
///     dL/dt = FillRate·(fill/100) − DrainRate·(drain/100)·√(L/100)
///
/// That matters pedagogically: the process gain changes with level, so a
/// controller tuned at the top of the tank overshoots at the bottom. A plain
/// integrator would make the exercise unrealistically easy.
/// </summary>
public partial class LevelTank : Node3D, IPart
{
    /// <summary>Percent per second added at a fully open fill valve.</summary>
    [Export] public float FillRate { get; set; } = 18.0f;

    /// <summary>Percent per second drained at a full valve and a full tank.</summary>
    [Export] public float DrainRate { get; set; } = 22.0f;

    /// <summary>Nominal capacity in litres. Display only — the model works in
    /// percent, which is what a level transmitter reports.</summary>
    [Export] public float CapacityLitres { get; set; } = 500.0f;

    private const float TankHeight = 0.70f;
    private const float TankRadius = 0.22f;
    private const float WallInset = 0.015f;

    private MeshInstance3D _liquid = null!;
    private CylinderMesh _liquidMesh = null!;
    private Label3D _readout = null!;

    /// <summary>Level as a percentage, 0–100. This is the measured variable.</summary>
    public float Level { get; private set; }

    /// <summary>True when the tank has run dry or brimmed over — the states an
    /// interlock is supposed to prevent.</summary>
    /// <summary>True while the valves are seized. Nothing here computes it —
    /// it is an Input, raised by whoever is playing maintenance.</summary>
    public bool IsFaulted { get; private set; }

    private float _heldFill;
    private float _heldDrain;
    private StandardMaterial3D? _faultLampMat;

    /// <summary>Freeze or release the valves. Freezing keeps whatever opening
    /// they had on the last step, rather than slamming them shut: a valve that
    /// failed closed would be a *safe* failure, and the instructive one is the
    /// valve that fails where it stands.</summary>
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

    public bool IsEmpty => Level <= 0.01f;
    public bool IsFull => Level >= 99.99f;

    public override void _Ready()
    {
        var steel = new StandardMaterial3D
        {
            AlbedoColor = new Color(0.62f, 0.64f, 0.68f),
            Metallic = 0.35f,
            Roughness = 0.45f,
        };

        // Glass shell, so the liquid column inside is readable at a glance.
        var glass = new StandardMaterial3D
        {
            AlbedoColor = new Color(0.72f, 0.80f, 0.86f, 0.22f),
            Transparency = BaseMaterial3D.TransparencyEnum.Alpha,
            Metallic = 0.10f,
            Roughness = 0.10f,
            CullMode = BaseMaterial3D.CullModeEnum.Disabled,
        };

        float centre = TankHeight / 2;

        AddChild(new MeshInstance3D
        {
            Name = "Shell",
            Mesh = new CylinderMesh
            {
                TopRadius = TankRadius,
                BottomRadius = TankRadius,
                Height = TankHeight,
            },
            MaterialOverride = glass,
            Position = new Vector3(0, centre, 0),
        });

        foreach (float y in new[] { 0.0f, TankHeight })
        {
            AddChild(new MeshInstance3D
            {
                Mesh = new CylinderMesh
                {
                    TopRadius = TankRadius + 0.015f,
                    BottomRadius = TankRadius + 0.015f,
                    Height = 0.03f,
                },
                MaterialOverride = steel,
                Position = new Vector3(0, y, 0),
            });
        }

        // Liquid column. Scaled from the bottom as the level changes.
        _liquidMesh = new CylinderMesh
        {
            TopRadius = TankRadius - WallInset,
            BottomRadius = TankRadius - WallInset,
            Height = 0.001f,
        };
        _liquid = new MeshInstance3D
        {
            Name = "Liquid",
            Mesh = _liquidMesh,
            MaterialOverride = new StandardMaterial3D
            {
                AlbedoColor = new Color(0.20f, 0.55f, 0.85f, 0.80f),
                Transparency = BaseMaterial3D.TransparencyEnum.Alpha,
                Roughness = 0.25f,
                Metallic = 0.05f,
            },
        };
        AddChild(_liquid);

        // Inlet over the top, outlet at the foot.
        AddChild(new MeshInstance3D
        {
            Mesh = new CylinderMesh { TopRadius = 0.035f, BottomRadius = 0.035f, Height = 0.22f },
            MaterialOverride = steel,
            Position = new Vector3(0, TankHeight + 0.11f, 0),
        });
        var outlet = new MeshInstance3D
        {
            Mesh = new CylinderMesh { TopRadius = 0.03f, BottomRadius = 0.03f, Height = 0.26f },
            MaterialOverride = steel,
            Position = new Vector3(0, 0.02f, TankRadius + 0.08f),
        };
        outlet.RotateX(Mathf.Pi / 2);
        AddChild(outlet);

        // Legs down to the floor, per the work-plane convention.
        foreach (var offset in new[] { new Vector2(1, 1), new Vector2(-1, 1),
                                       new Vector2(1, -1), new Vector2(-1, -1) })
        {
            AddChild(new MeshInstance3D
            {
                Mesh = new BoxMesh
                {
                    Size = new Vector3(0.035f, PartLayout.FloorDrop, 0.035f),
                },
                MaterialOverride = steel,
                Position = new Vector3(offset.X * TankRadius * 0.62f,
                                       -PartLayout.FloorDrop / 2,
                                       offset.Y * TankRadius * 0.62f),
            });
        }

        _readout = new Label3D
        {
            Name = "LevelReadout",
            Text = "0.0 %",
            Position = new Vector3(0, TankHeight + 0.30f, 0),
            Billboard = BaseMaterial3D.BillboardModeEnum.Enabled,
            FontSize = 96,
            PixelSize = 0.0016f,
            Modulate = new Color(0.55f, 0.85f, 1.0f),
        };
        AddChild(_readout);

        BuildFaultLamp();
        ApplyLevel();
    }

    /// <summary>The same beacon the drives carry. Placed on the shell beside
    /// the outlet, where an instrument panel would be.</summary>
    private void BuildFaultLamp()
    {
        _faultLampMat = new StandardMaterial3D
        {
            AlbedoColor = new Color(0.30f, 0.06f, 0.06f),
            Metallic = 0.10f,
            Roughness = 0.35f,
        };

        var mount = new Vector3(0, TankHeight * 0.72f, TankRadius);
        const float stalk = 0.06f;

        AddChild(new MeshInstance3D
        {
            Name = "ValveFaultStalk",
            Mesh = new CylinderMesh { TopRadius = 0.008f, BottomRadius = 0.010f, Height = stalk },
            MaterialOverride = new StandardMaterial3D
            {
                AlbedoColor = new Color(0.22f, 0.23f, 0.25f),
                Metallic = 0.40f,
                Roughness = 0.50f,
            },
            Position = mount + new Vector3(0, 0, stalk / 2),
            Rotation = new Vector3(Mathf.Pi / 2, 0, 0),
        });

        AddChild(new MeshInstance3D
        {
            Name = "ValveFaultLamp",
            Mesh = new SphereMesh { Radius = 0.040f, Height = 0.080f },
            MaterialOverride = _faultLampMat,
            Position = mount + new Vector3(0, 0, stalk + 0.026f),
        });
    }

    /// <summary>
    /// Integrate one step. <paramref name="fill"/> and <paramref name="drain"/>
    /// are valve openings in percent, which is what an analog output actually
    /// delivers — a PID writing 0–100 needs no scaling to drive this.
    /// </summary>
    public void Step(float fill, float drain, float delta)
    {
        // A seized valve holds the opening it had, whatever the controller
        // now writes (FI-01). This is the analog version of a jammed cylinder
        // and it is nastier: a digital actuator that stops is at least
        // obviously stopped, while a modulating valve stuck at 40% keeps the
        // process moving and looks like a controller that will not settle.
        // A PID chasing a valve that no longer answers is one of the first
        // real diagnoses an instrument technician learns.
        if (IsFaulted)
        {
            fill = _heldFill;
            drain = _heldDrain;
        }

        fill = Mathf.Clamp(fill, 0.0f, 100.0f);
        drain = Mathf.Clamp(drain, 0.0f, 100.0f);
        _heldFill = fill;
        _heldDrain = drain;

        // Whatever a pump has offered this tick, converted from litres per
        // second using this tank's own capacity -- the pump knows what it is
        // delivering and not what it is delivering into. Consumed and zeroed,
        // so a pump that stops offering stops filling on the next tick and not
        // whenever somebody remembers to clear a flag.
        //
        // The capacity is floored rather than trusted: a scene file can carry a
        // zero, and an infinite level would be rejected by the tag table with a
        // throw inside the tick, which is the hardest place to read one.
        float pumped = _offeredInflow / Mathf.Max(CapacityLitres, 0.01f) * 100.0f;
        _offeredInflow = 0.0f;

        float inflow = FillRate * (fill / 100.0f) + pumped;
        float outflow = DrainRate * (drain / 100.0f) * Mathf.Sqrt(Mathf.Max(Level, 0.0f) / 100.0f);

        Level = Mathf.Clamp(Level + (inflow - outflow) * delta, 0.0f, 100.0f);
        ApplyLevel();
    }

    /// <summary>Litres per second offered by pumps this tick, before the tank
    /// turns them into a percentage of its own capacity.</summary>
    private float _offeredInflow;

    /// <summary>
    /// Offer flow into this tank for this tick, in litres per second.
    ///
    /// Accumulated rather than applied, and consumed by <see cref="Step"/>, for
    /// the reason <see cref="HeatingStation.AddCooling"/> gives: parts are
    /// dispatched in placement order, so a pump placed before its tank would
    /// land on one side of the integration and a pump placed after it on the
    /// other, and the plant would behave differently depending on the order
    /// somebody clicked. Adding rather than assigning also means two pumps fill
    /// twice, which is what two pumps do.
    /// </summary>
    public void AddInflow(float litresPerSecond) =>
        _offeredInflow += Mathf.Max(litresPerSecond, 0.0f);

    public void ResetLevel()
    {
        Level = 0.0f;
        ApplyLevel();
    }

    /// <summary>Local-space centres of the inlet pipe (top) and the outlet pipe
    /// (side, near the foot), matching where <c>_Ready</c> places them.</summary>
    private static readonly Vector3 InletCentre = new(0, TankHeight + 0.11f, 0);
    private static readonly Vector3 OutletCentre = new(0, 0.02f, TankRadius + 0.08f);
    private const float ValveHitRadius = 0.09f;

    /// <summary>
    /// Which valve, if any, a click hits -- "fill" for the inlet pipe, "drain"
    /// for the outlet -- so the two can be operated independently instead of a
    /// click on the tank meaning only one of them (UX-37). Tested as spheres
    /// around each pipe rather than the tank's bounding box, the same
    /// reasoning as <see cref="ButtonPanel.HitTest"/>.
    /// </summary>
    public string? HitTest(Vector3 worldOrigin, Vector3 worldDirection)
    {
        var toLocal = GlobalTransform.AffineInverse();
        Vector3 origin = toLocal * worldOrigin;
        Vector3 dir = (toLocal.Basis * worldDirection).Normalized();

        float? fillT = RayHit.Sphere(origin, dir, InletCentre, ValveHitRadius);
        float? drainT = RayHit.Sphere(origin, dir, OutletCentre, ValveHitRadius);

        if (fillT is null) return drainT is null ? null : "drain";
        if (drainT is null) return "fill";
        return fillT <= drainT ? "fill" : "drain";
    }

    /// <summary>Level the geometry was last built for, so an unchanged tank
    /// costs nothing.</summary>
    private float _drawnLevel = float.NaN;

    private void ApplyLevel()
    {
        // Assigning CylinderMesh.Height regenerates the mesh and re-uploads it,
        // and setting Label3D.Text rebuilds its glyph mesh. Doing both every
        // physics tick for a value that has not moved is pure waste — and with
        // a renderer attached it was enough to stall the frame loop outright:
        // an idle tank scene ran headless and hung in the GUI.
        if (Mathf.IsEqualApprox(Level, _drawnLevel)) return;
        _drawnLevel = Level;

        float usable = TankHeight - 2 * WallInset;
        float height = Mathf.Max(usable * (Level / 100.0f), 0.001f);

        _liquidMesh.Height = height;
        _liquid.Position = new Vector3(0, WallInset + height / 2, 0);
        _liquid.Visible = Level > 0.05f;

        _readout.Text = $"{Level:0.0} %";
    }

    // ---------- IPart (HP-34)

    /// <summary>The library's first analog part: valve openings and a level
    /// transmitter, all in percent, all Float.</summary>
    public void DeclareTags(PartTagBuilder tags) => tags
        .Float("fill", $"Tank {tags.Index} Fill Valve (%)", TagKind.Output)
        .Float("drain", $"Tank {tags.Index} Drain Valve (%)", TagKind.Output)
        .Float("level", $"Tank {tags.Index} Level (%)", TagKind.Input)
        // A seized valve holds its opening (FI-01) -- the analog failure, and a
        // nastier one to diagnose than a stopped drive.
        .Bit("fault", $"Tank {tags.Index} Valve Fault", TagKind.Input);

    public void CaptureSettings(PartSettings settings)
    {
        settings.Put("fill_rate", FillRate);
        settings.Put("drain_rate", DrainRate);
        settings.Put("capacity", CapacityLitres);
    }

    public void ApplySettings(PartSettings settings)
    {
        if (settings.Number("fill_rate") is { } fill) FillRate = fill;
        if (settings.Number("drain_rate") is { } drain) DrainRate = drain;
        if (settings.Number("capacity") is { } capacity) CapacityLitres = capacity;
    }

    public void StepPart(PartTick tick)
    {
        if (!tick.Has("level") || !tick.Has("fill") || !tick.Has("drain")) return;

        if (tick.TryBit("fault", out bool faulted)) SetFaulted(faulted);

        // dt is scaled simulation time, so the tank obeys pause and the
        // time-scale control like everything else.
        Step(tick.Number("fill"), tick.Number("drain"), tick.Dt);
        tick.Write("level", (double)Level);
    }

    public void DescribeControls(IPartInspector ui)
    {
        ui.Slider("Fill Rate (%/s)", FillRate, 1.0f, 60.0f, 1.0f, value => FillRate = value);
        ui.Slider("Drain Rate (%/s)", DrainRate, 1.0f, 60.0f, 1.0f, value => DrainRate = value);
    }

    public void ResetPart(PartReset reset)
    {
        ResetLevel();
        reset.Write("level", 0.0);
    }

    /// <summary>Precise: the two valves are separately clickable.</summary>
    public PartOperation? Operation => new("tank", Precise: true);

    public string? HitTestRegion(Vector3 from, Vector3 direction) => HitTest(from, direction);

    public void Operate(PartOperate op) => op.ToggleAnalog(op.Region);
}
