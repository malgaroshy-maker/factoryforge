using FactoryForge.TagBus;
using Godot;

namespace FactoryForge.Parts;

/// <summary>
/// A two-axis pick-and-place gantry with a vacuum cup (CP-03).
///
/// Everything else in the library is one actuator with one job. This is three
/// that have to be sequenced against each other, and that is the point: an
/// analog travel axis, a vertical stroke, and a gripper, where lowering while
/// the carriage is still travelling drags the load and releasing at the wrong
/// moment drops it. The interlocks a student writes here are the first ones in
/// this project that exist because the machine can genuinely hurt itself.
///
/// The cup picks up a real <see cref="BoxPhysics"/> — the body is frozen
/// kinematic and carried, then handed back to free physics on release with the
/// carriage's own velocity, so a box let go mid-travel flies. Grabbing where
/// there is nothing leaves <c>holding</c> false rather than pretending, so a
/// program that never checks it runs a whole cycle carrying air.
///
/// Local space: the origin is the centre of the gantry on the work plane. The
/// carriage travels along X, 0 % at -<see cref="RailLength"/>/2.
/// </summary>
public partial class PickPlaceArm : Node3D, IPart
{
    [Export] public float RailLength { get; set; } = 1.60f;

    /// <summary>Travel rate at full command, percent of the rail per second.</summary>
    [Export] public float TravelSpeed { get; set; } = 55.0f;

    /// <summary>Vertical stroke of the Z column, metres.</summary>
    [Export] public float StrokeLength { get; set; } = 0.62f;

    [Export] public float LowerSpeed { get; set; } = 0.85f;

    /// <summary>How close to target counts as in position, in percent. A real
    /// axis has a window; without one `.inposition` would chatter and a
    /// sequence written against it would never advance.</summary>
    [Export] public float PositionTolerance { get; set; } = 1.5f;

    /// <summary>Rail height above the part origin.</summary>
    private const float RailY = 0.88f;

    /// <summary>Carriage underside, where the Z column hangs from.</summary>
    private const float ColumnTopY = RailY - 0.10f;

    private const float CupRadius = 0.075f;

    /// <summary>How far off the centre line each leg stands. Wider than half a
    /// standard belt (<see cref="PartLayout.StandardBeltWidth"/> / 2 = 0.25) so
    /// the portal straddles a lane instead of standing in it.</summary>
    private const float LegOffsetZ = 0.32f;

    private Node3D _carriage = null!;
    private MeshInstance3D _column = null!;
    private BoxMesh _columnMesh = null!;
    private Node3D _cup = null!;
    private Area3D _pickZone = null!;
    private StandardMaterial3D _cupMat = null!;
    private StandardMaterial3D? _faultLampMat;

    private float _position;      // percent along the rail
    private float _extension;     // metres of Z stroke
    private BoxPhysics? _held;
    private Vector3 _lastCupWorld;
    private bool _hasLastCupWorld;
    private Vector3 _cupVelocity;

    /// <summary>Actual carriage position, 0–100 % of the rail. Named apart from
    /// Node3D.Position on purpose — hiding a base member that every caller in
    /// the editor reaches for would be a trap.</summary>
    public float AxisPosition => _position;

    /// <summary>Commanded position last seen, kept so the property panel can
    /// show the pair and the lag between them.</summary>
    public float Target { get; private set; }

    public bool InPosition => Mathf.Abs(_position - Target) <= PositionTolerance;
    public bool IsLowered => _extension >= StrokeLength - 0.01f;
    public bool IsRaised => _extension <= 0.01f;
    public bool IsHolding => _held is not null;

    /// <summary>Seized: the axis and the column stop where they are. A gantry
    /// frozen half-lowered over a running belt is the failure this part exists
    /// to let somebody cause on purpose.</summary>
    public bool IsFaulted { get; private set; }

    public override void _Ready()
    {
        var frameMat = new StandardMaterial3D
        {
            AlbedoColor = new Color(0.24f, 0.26f, 0.29f),
            Metallic = 0.45f,
            Roughness = 0.45f,
        };
        // Brushed, not chrome. At 0.55/0.30 the sun blew the rail out to a
        // solid white bar across the top of the machine -- the same failure
        // IndustrialMeshBuilder already had to tune out of the belt rails and
        // sensor posts, rediscovered here because this file picked its own
        // numbers instead of the ones next door.
        var railMat = new StandardMaterial3D
        {
            AlbedoColor = new Color(0.58f, 0.60f, 0.64f),
            Metallic = 0.35f,
            Roughness = 0.50f,
        };
        var accentMat = new StandardMaterial3D
        {
            AlbedoColor = new Color(0.95f, 0.75f, 0.10f),
            Roughness = 0.40f,
        };

        float columnHeight = PartLayout.FloorDrop + RailY;

        // A portal, not two posts in the lane. Each end of the rail stands on a
        // pair of legs straddling the conveyor, because a single leg on the
        // centre line would sit in the middle of whatever the gantry is picking
        // from — which is exactly what the first version did, and it made the
        // part unusable over any belt.
        foreach (int end in new[] { -1, 1 })
        {
            float x = end * RailLength / 2.0f;
            foreach (int side in new[] { -1, 1 })
            {
                float z = side * LegOffsetZ;
                AddChild(new MeshInstance3D
                {
                    Name = $"Leg{(end < 0 ? "L" : "R")}{(side < 0 ? "N" : "F")}",
                    Mesh = new BoxMesh { Size = new Vector3(0.08f, columnHeight, 0.08f) },
                    MaterialOverride = frameMat,
                    Position = new Vector3(x, RailY - columnHeight / 2.0f, z),
                });
                AddChild(new MeshInstance3D
                {
                    Name = $"Foot{(end < 0 ? "L" : "R")}{(side < 0 ? "N" : "F")}",
                    Mesh = new BoxMesh { Size = new Vector3(0.20f, 0.03f, 0.20f) },
                    MaterialOverride = frameMat,
                    Position = new Vector3(x, -PartLayout.FloorDrop, z),
                });
            }

            // Cross-beam tying each leg pair together under the rail.
            AddChild(new MeshInstance3D
            {
                Name = end < 0 ? "PortalLeft" : "PortalRight",
                Mesh = new BoxMesh { Size = new Vector3(0.08f, 0.08f, LegOffsetZ * 2 + 0.08f) },
                MaterialOverride = frameMat,
                Position = new Vector3(x, RailY - 0.02f, 0),
            });
        }

        // The rail the carriage runs on, plus a yellow cable track above it so
        // the axis reads as powered rather than as a bar with a block on it.
        AddChild(new MeshInstance3D
        {
            Name = "Rail",
            Mesh = new BoxMesh { Size = new Vector3(RailLength, 0.07f, 0.11f) },
            MaterialOverride = railMat,
            Position = new Vector3(0, RailY, 0),
        });
        AddChild(new MeshInstance3D
        {
            Name = "CableTrack",
            Mesh = new BoxMesh { Size = new Vector3(RailLength, 0.035f, 0.05f) },
            MaterialOverride = accentMat,
            Position = new Vector3(0, RailY + 0.06f, -0.08f),
        });

        _carriage = new Node3D { Name = "Carriage" };
        AddChild(_carriage);

        _carriage.AddChild(new MeshInstance3D
        {
            Name = "CarriageBody",
            Mesh = new BoxMesh { Size = new Vector3(0.20f, 0.13f, 0.17f) },
            MaterialOverride = frameMat,
            Position = new Vector3(0, RailY - 0.03f, 0),
        });

        // The Z column is resized rather than translated as it strokes, so it
        // grows out of the carriage instead of sliding through it — the same
        // trick the pusher's rod uses, and for the same reason.
        _columnMesh = new BoxMesh { Size = new Vector3(0.06f, 0.10f, 0.06f) };
        _column = new MeshInstance3D
        {
            Name = "ZColumn",
            Mesh = _columnMesh,
            MaterialOverride = railMat,
        };
        _carriage.AddChild(_column);

        _cup = new Node3D { Name = "VacuumCup" };
        _carriage.AddChild(_cup);

        _cupMat = new StandardMaterial3D
        {
            AlbedoColor = new Color(0.16f, 0.16f, 0.18f),
            Roughness = 0.75f,
        };
        _cup.AddChild(new MeshInstance3D
        {
            Name = "CupBell",
            Mesh = new CylinderMesh { TopRadius = 0.030f, BottomRadius = CupRadius, Height = 0.05f },
            MaterialOverride = _cupMat,
            Position = new Vector3(0, 0.025f, 0),
        });
        _cup.AddChild(new MeshInstance3D
        {
            Name = "CupCollar",
            Mesh = new CylinderMesh { TopRadius = 0.032f, BottomRadius = 0.032f, Height = 0.04f },
            MaterialOverride = accentMat,
            Position = new Vector3(0, 0.07f, 0),
        });

        // What the cup can reach. An Area3D rather than a distance search so a
        // pick obeys the same collision world everything else does, and so a
        // box wedged under something is not silently grabbed through it.
        _pickZone = new Area3D { Name = "PickZone", Monitoring = true };
        // Roughly a carton wide. The first version was 200 mm square, which
        // demanded the carton be indexed to within 50 mm of the cup's centre
        // and turned every small positioning error into a pick that came up
        // empty -- a fussiness no real vacuum cup has, and one that made the
        // part look unreliable when the *scene* was the thing misaligned.
        _pickZone.AddChild(new CollisionShape3D
        {
            Shape = new BoxShape3D { Size = new Vector3(0.28f, 0.14f, 0.28f) },
            Position = new Vector3(0, -0.03f, 0),
        });
        _cup.AddChild(_pickZone);

        BuildFaultLamp();
        ApplyMotion();
    }

    private void BuildFaultLamp()
    {
        _faultLampMat = new StandardMaterial3D
        {
            AlbedoColor = new Color(0.30f, 0.06f, 0.06f),
            Metallic = 0.10f,
            Roughness = 0.35f,
        };
        AddChild(new MeshInstance3D
        {
            Name = "DriveFaultLamp",
            Mesh = new SphereMesh { Radius = 0.042f, Height = 0.084f },
            MaterialOverride = _faultLampMat,
            Position = new Vector3(-RailLength / 2.0f, RailY + 0.12f, 0),
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
    /// One machine tick: travel, stroke, then gripper — in that order, because
    /// the gripper's decision depends on where the cup ended up this tick and
    /// not where it was last one.
    /// </summary>
    public void Step(float targetPercent, bool lower, bool grip, float delta)
    {
        Target = Mathf.Clamp(targetPercent, 0.0f, 100.0f);

        if (!IsFaulted)
        {
            _position = Mathf.MoveToward(_position, Target, TravelSpeed * delta);
            _extension = Mathf.MoveToward(_extension, lower ? StrokeLength : 0.0f, LowerSpeed * delta);
        }

        ApplyMotion();
        TrackCupVelocity(delta);
        UpdateGrip(grip);
        CarryHeldItem();
    }

    private void ApplyMotion()
    {
        if (_carriage is null) return;

        float x = -RailLength / 2.0f + RailLength * _position / 100.0f;
        _carriage.Position = new Vector3(x, 0, 0);

        // Column length is the rest stub plus the stroke, anchored at the
        // carriage so its top never moves.
        const float restStub = 0.10f;
        float length = restStub + _extension;
        _columnMesh.Size = new Vector3(0.06f, length, 0.06f);
        _column.Position = new Vector3(0, ColumnTopY - length / 2.0f, 0);
        _cup.Position = new Vector3(0, ColumnTopY - length, 0);
    }

    /// <summary>The cup's own world velocity, measured rather than derived, so
    /// a box released mid-travel is thrown with the speed the machine actually
    /// had. A released box that simply drops straight down is the tell that a
    /// simulator is faking the handoff.</summary>
    private void TrackCupVelocity(float delta)
    {
        Vector3 now = _cup.GlobalPosition;
        // Guarded by a flag rather than by "is the last sample still zero":
        // the origin is a place the cup can legitimately be, and a machine
        // parked there would never start measuring.
        if (delta > 0.0f && _hasLastCupWorld)
            _cupVelocity = (now - _lastCupWorld) / delta;
        _lastCupWorld = now;
        _hasLastCupWorld = true;
    }

    private void UpdateGrip(bool grip)
    {
        if (grip && _held is null) TryPick();
        else if (!grip && _held is not null) Release();

        // The cup darkens and the collar lights when vacuum is on, so the
        // gripper's state is visible from across the scene rather than only in
        // the tag list.
        if (_cupMat is not null)
        {
            _cupMat.AlbedoColor = grip ? new Color(0.30f, 0.10f, 0.10f) : new Color(0.16f, 0.16f, 0.18f);
            _cupMat.EmissionEnabled = IsHolding;
            _cupMat.Emission = new Color(1.0f, 0.35f, 0.15f);
            _cupMat.EmissionEnergyMultiplier = IsHolding ? 0.8f : 0.0f;
        }
    }

    private void TryPick()
    {
        if (_pickZone is null) return;

        BoxPhysics? best = null;
        float nearest = float.MaxValue;
        foreach (var body in _pickZone.GetOverlappingBodies())
        {
            if (body is not BoxPhysics box || box.Freeze) continue;
            float d = box.GlobalPosition.DistanceSquaredTo(_cup.GlobalPosition);
            if (d >= nearest) continue;
            nearest = d;
            best = box;
        }

        if (best is null) return;

        // Kinematic rather than Static: a carried box still shoves whatever it
        // is dragged into, which is how a real gantry knocks a stack over.
        best.FreezeMode = RigidBody3D.FreezeModeEnum.Kinematic;
        best.Freeze = true;
        _held = best;
    }

    private void Release()
    {
        if (_held is null) return;
        var box = _held;
        _held = null;

        box.Freeze = false;
        box.LinearVelocity = _cupVelocity;
        box.AngularVelocity = Vector3.Zero;
    }

    private void CarryHeldItem()
    {
        if (_held is null) return;
        // The box may have been despawned by a remover or the kill plane while
        // it was in the cup; a freed node is not something to keep carrying.
        if (!IsInstanceValid(_held)) { _held = null; return; }

        _held.GlobalPosition = _cup.GlobalPosition - new Vector3(0, _held.Height / 2.0f, 0);
    }

    /// <summary>Drop whatever is held without ceremony. Called on scene reset,
    /// so a run does not start with a frozen carton stuck to a cup that has
    /// since been moved somewhere else.</summary>
    public void ResetArm()
    {
        Release();
        _position = 0.0f;
        _extension = 0.0f;
        Target = 0.0f;
        ApplyMotion();
    }

    // ---------- IPart (HP-34)

    public void DeclareTags(PartTagBuilder tags) => tags
        .Float("target", $"Gantry {tags.Index} Target (%)", TagKind.Output)
        .Bit("lower", $"Gantry {tags.Index} Lower", TagKind.Output)
        .Bit("grip", $"Gantry {tags.Index} Vacuum", TagKind.Output)
        .Float("position", $"Gantry {tags.Index} Position (%)", TagKind.Input)
        .Bit("inposition", $"Gantry {tags.Index} In Position", TagKind.Input, initial: true)
        .Bit("lowered", $"Gantry {tags.Index} Lowered", TagKind.Input)
        .Bit("raised", $"Gantry {tags.Index} Raised", TagKind.Input, initial: true)
        .Bit("holding", $"Gantry {tags.Index} Holding", TagKind.Input)
        .Bit("fault", $"Gantry {tags.Index} Drive Fault", TagKind.Input);

    public void CaptureSettings(PartSettings settings)
    {
        settings.Put("rail_length", RailLength);
        settings.Put("travel_speed", TravelSpeed);
        settings.Put("stroke", StrokeLength);
        settings.Put("lower_speed", LowerSpeed);
        settings.Put("tolerance", PositionTolerance);
    }

    public void ApplySettings(PartSettings settings)
    {
        if (settings.Number("rail_length") is { } rail) RailLength = rail;
        if (settings.Number("travel_speed") is { } travel) TravelSpeed = travel;
        if (settings.Number("stroke") is { } stroke) StrokeLength = stroke;
        if (settings.Number("lower_speed") is { } lower) LowerSpeed = lower;
        if (settings.Number("tolerance") is { } tolerance) PositionTolerance = tolerance;
    }

    public void StepPart(PartTick tick)
    {
        if (tick.TryBit("fault", out bool faulted)) SetFaulted(faulted);

        Step(tick.Number("target"), tick.Bit("lower"), tick.Bit("grip"), tick.Dt);

        tick.Write("position", (double)AxisPosition);
        tick.Write("inposition", InPosition);
        tick.Write("lowered", IsLowered);
        tick.Write("raised", IsRaised);
        tick.Write("holding", IsHolding);
    }

    public void DescribeControls(IPartInspector ui)
    {
        ui.Slider("Travel Speed (%/s)", TravelSpeed, 5.0f, 200.0f, 5.0f,
                  value => TravelSpeed = value);
        ui.Slider("Lower Speed (m/s)", LowerSpeed, 0.1f, 3.0f, 0.05f, value => LowerSpeed = value);
        ui.Slider("In-Position Window (%)", PositionTolerance, 0.2f, 10.0f, 0.1f,
                  value => PositionTolerance = value);
    }

    public PartOperation? Operation => new("gantry", "lower");

    public void Operate(PartOperate op) => op.ToggleBit("lower");
}
