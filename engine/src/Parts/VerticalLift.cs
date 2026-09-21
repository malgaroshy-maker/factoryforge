using FactoryForge.TagBus;
using Godot;

namespace FactoryForge.Parts;

/// <summary>
/// A vertical reciprocating conveyor — a lift between levels (HA-03).
///
/// <b>Height becomes a routing dimension.</b> Everything shipped in this
/// library is one plane: the belts, the diverter, the turntable and the gantry
/// all move product around a single floor, and a scene is therefore a map. A
/// lift makes the third axis part of the route — infeed at one level,
/// discharge at another — and with it comes the first control problem in the
/// library that is about <em>where a thing is</em> rather than about what a
/// machine is doing: two destinations, one path, and a call that has to be
/// answered before the next one can be.
///
/// <b>The carriage is a resource, and the machine enforces it.</b> Exactly one
/// carton fits, and that is the lesson: a second carton must wait, and waiting
/// is not something the controller may be trusted to remember. So the lift
/// carries its own entry gate — a blade across the infeed mouth that is down
/// only while the carriage is standing at the infeed level, empty and healthy.
/// The instant a carton is aboard the blade rises and physically holds the next
/// one on the belt outside. A program that ignores <c>ready</c> does not get
/// two cartons on the carriage; it gets a queue, which is what a real
/// interlocked machine does and is a far more useful thing to debug than a
/// carton clipping through another one.
///
/// <b>It really lifts.</b> The carriage is an <see cref="AnimatableBody3D"/>
/// with <c>SyncToPhysics</c>, so the carton is carried by contact — it rides up
/// because the deck comes up under it, and it is left behind if the deck drops
/// faster than gravity. And the deck is driven: <c>transfer</c> puts a surface
/// velocity on it, which is how the carton gets on and how it gets off, because
/// a lift that cannot discharge is a shelf.
///
/// Local space: the origin is the centre of the shaft on the work plane, and
/// level 0 puts the carriage deck flush with a standard conveyor's carrying
/// surface, lead-in wedges and all (gotcha 23). Product travels +X.
/// </summary>
public partial class VerticalLift : Node3D, IPart
{
    /// <summary>How many levels the lift serves, including level 0.</summary>
    [Export] public int Levels { get; set; } = 3;

    /// <summary>Rise between levels, metres.</summary>
    [Export] public float LevelSpacing { get; set; } = 0.90f;

    /// <summary>Hoist rate, metres per second. Comfortably under gravity, so a
    /// descending carriage keeps its load rather than dropping out from under
    /// it — which it will do if this is wound far enough up.</summary>
    [Export] public float HoistSpeed { get; set; } = 0.75f;

    /// <summary>Which level the infeed gate is on. The level product arrives
    /// at is a property of the building, not of the program.</summary>
    [Export] public int InfeedLevel { get; set; }

    /// <summary>Carriage deck surface speed when transferring, m/s.</summary>
    [Export] public float TransferSpeed { get; set; } = 0.5f;

    /// <summary>How close to a level's height counts as arrived, metres.</summary>
    [Export] public float LevelTolerance { get; set; } = 0.02f;

    /// <summary>
    /// The smallest level pitch this part will accept.
    ///
    /// Not tidiness: <see cref="NearestLevel"/> divides by the pitch, and a
    /// pitch of zero would put an infinity into an int conversion and a NaN
    /// into <c>TagTable.Set</c>, which throws (HP-23). Clamped where the value
    /// arrives rather than guarded at each use.
    /// </summary>
    private const float MinSpacing = 0.05f;

    private const float DeckLength = 0.46f;
    private const float DeckThickness = 0.10f;
    private const float DeckWidth = PartLayout.StandardBeltWidth;

    /// <summary>Deck top relative to the carriage origin, and the carriage
    /// origin at level 0 relative to the part origin — together they put the
    /// deck flush with a conveyor's carrying surface.</summary>
    private const float DeckTopLocal = DeckThickness / 2.0f;
    private const float CarriageBaseY = PartLayout.BeltSurface - DeckTopLocal;

    private const float BladeHeight = 0.26f;
    private const float BladeSpeed = 0.9f;

    private AnimatableBody3D _carriage = null!;
    private Area3D _deckZone = null!;
    private Node3D _blade = null!;
    private StandardMaterial3D? _faultLampMat;
    private StandardMaterial3D? _readyLampMat;

    private float _height;
    private float _bladeRise = 1.0f;
    private int _target;
    private bool _occupied;

    /// <summary>Carriage height above the work plane, metres. Level 0 is
    /// zero.</summary>
    public float Height => _height;

    public int EffectiveLevels => Mathf.Max(1, Levels);
    public float EffectiveSpacing => Mathf.Max(MinSpacing, LevelSpacing);

    /// <summary>The level the target call names, clamped to what the mast
    /// actually has. A call to a level that does not exist is a program bug,
    /// and the honest answer is the top of the mast rather than a carriage that
    /// keeps climbing.</summary>
    public int TargetLevel => Mathf.Clamp(_target, 0, EffectiveLevels - 1);

    public float HeightOf(int level) => Mathf.Clamp(level, 0, EffectiveLevels - 1) * EffectiveSpacing;

    /// <summary>Which level the carriage is nearest, whatever it was told. A
    /// seized carriage between two floors still reports the nearest one, and
    /// <see cref="IsAtLevel"/> is what says it is not actually there.</summary>
    public int NearestLevel =>
        Mathf.Clamp(Mathf.RoundToInt(_height / EffectiveSpacing), 0, EffectiveLevels - 1);

    public bool IsAtLevel => Mathf.Abs(_height - HeightOf(TargetLevel)) <= LevelTolerance;

    /// <summary>A carton is on the deck. Read from the collision world, not
    /// from a count of transfers, so a carton that was placed on the carriage
    /// by an arm is seen exactly as one that rode on from a belt.</summary>
    public bool IsOccupied => _occupied;

    public bool IsAtInfeed =>
        Mathf.Abs(_height - HeightOf(InfeedLevel)) <= LevelTolerance;

    /// <summary>Should the gate be letting product in? The machine's own
    /// decision, not the controller's.</summary>
    public bool GateShouldOpen => !IsFaulted && IsAtInfeed && !_occupied;

    public bool IsGateOpen => _bladeRise <= 0.02f;

    /// <summary>The carriage is standing at the infeed level, empty, with its
    /// gate open. The one bit a program upstream has to look at.</summary>
    public bool IsReady => GateShouldOpen && IsGateOpen;

    /// <summary>Seized: the carriage stops where it stands, between floors if
    /// that is where it was. A lift parked between two levels blocks both, and
    /// its gate stays shut, which is the only safe thing it can do.</summary>
    public bool IsFaulted { get; private set; }

    public override void _Ready()
    {
        Levels = EffectiveLevels;
        LevelSpacing = EffectiveSpacing;

        var frameMat = new StandardMaterial3D
        {
            AlbedoColor = new Color(0.23f, 0.25f, 0.28f),
            Metallic = 0.45f,
            Roughness = 0.50f,
        };
        var accentMat = new StandardMaterial3D
        {
            AlbedoColor = new Color(0.93f, 0.74f, 0.11f),
            Roughness = 0.45f,
        };

        BuildMast(frameMat, accentMat);
        BuildCarriage(frameMat, accentMat);
        BuildGate(accentMat);
        BuildLamps();

        ApplyCarriage();
        ApplyBlade();
    }

    /// <summary>Half-width of the mast columns' centres. Outside a standard
    /// belt lane, so the shaft straddles the line instead of standing in
    /// it.</summary>
    private const float MastOffsetZ = 0.36f;

    private void BuildMast(StandardMaterial3D frameMat, StandardMaterial3D accentMat)
    {
        float topRise = (EffectiveLevels - 1) * EffectiveSpacing + 0.55f;
        float columnHeight = PartLayout.FloorDrop + topRise;

        foreach (int sz in new[] { -1, 1 })
        {
            AddChild(new MeshInstance3D
            {
                Name = sz < 0 ? "MastNear" : "MastFar",
                Mesh = new BoxMesh { Size = new Vector3(0.10f, columnHeight, 0.10f) },
                MaterialOverride = frameMat,
                Position = new Vector3(0, -PartLayout.FloorDrop + columnHeight / 2.0f, sz * MastOffsetZ),
            });
            AddChild(new MeshInstance3D
            {
                Name = sz < 0 ? "FootNear" : "FootFar",
                Mesh = new BoxMesh { Size = new Vector3(0.34f, 0.035f, 0.24f) },
                MaterialOverride = frameMat,
                Position = new Vector3(0, -PartLayout.FloorDrop, sz * MastOffsetZ),
            });
        }

        AddChild(new MeshInstance3D
        {
            Name = "HeadBeam",
            Mesh = new BoxMesh { Size = new Vector3(0.14f, 0.10f, MastOffsetZ * 2.0f + 0.10f) },
            MaterialOverride = frameMat,
            Position = new Vector3(0, topRise - 0.05f, 0),
        });
        // A drive drum on the head beam. Without it the mast is two posts and a
        // lintel, and nothing says the carriage is hauled rather than floating.
        AddChild(new MeshInstance3D
        {
            Name = "HoistDrum",
            Mesh = new CylinderMesh { TopRadius = 0.06f, BottomRadius = 0.06f, Height = 0.16f },
            MaterialOverride = accentMat,
            RotationDegrees = new Vector3(90, 0, 0),
            Position = new Vector3(0, topRise - 0.16f, 0),
        });

        // A plate at each level, so the levels are visible in the scene rather
        // than only in the tag list -- and so a carriage stopped between two of
        // them is obviously between two of them.
        for (int level = 0; level < EffectiveLevels; level++)
        {
            float y = HeightOf(level);
            foreach (int sz in new[] { -1, 1 })
            {
                AddChild(new MeshInstance3D
                {
                    Name = $"LevelPlate{level}{(sz < 0 ? "N" : "F")}",
                    Mesh = new BoxMesh { Size = new Vector3(0.16f, 0.03f, 0.02f) },
                    MaterialOverride = accentMat,
                    Position = new Vector3(0, y + PartLayout.BeltSurface, sz * (MastOffsetZ - 0.06f)),
                });
            }
        }
    }

    private void BuildCarriage(StandardMaterial3D frameMat, StandardMaterial3D accentMat)
    {
        _carriage = new AnimatableBody3D
        {
            Name = "Carriage",
            // The carriage is a body the solver integrates rather than one
            // that teleports between frames.
            //
            // Worth being precise about what this does and does not buy, since
            // the turntable's identical line is doing a different job. A
            // *rotating* deck moves its load only through friction, so without
            // this it reports no surface motion and the carton sits still on a
            // visibly spinning plate. A *rising* deck displaces its load
            // directly, so a carton rides up either way — deliberately
            // breaking this line does not fail any assertion in
            // --self-test=handlingparts, and it was checked. What it buys here
            // is a carriage whose motion the solver can see: smooth contact
            // instead of a resolved overlap every frame, and a deck that can
            // carry a surface velocity along X at the same time.
            SyncToPhysics = true,
        };
        AddChild(_carriage);

        _carriage.AddChild(new MeshInstance3D
        {
            Name = "DeckPlate",
            Mesh = new BoxMesh { Size = new Vector3(DeckLength, DeckThickness, DeckWidth) },
            MaterialOverride = new StandardMaterial3D
            {
                AlbedoColor = new Color(0.31f, 0.33f, 0.36f),
                Metallic = 0.55f,
                Roughness = 0.45f,
            },
        });
        _carriage.AddChild(new CollisionShape3D
        {
            Shape = new BoxShape3D { Size = new Vector3(DeckLength, DeckThickness, DeckWidth) },
        });
        AddTransferRamps();

        _carriage.PhysicsMaterialOverride = new PhysicsMaterial
        {
            Friction = 0.70f,
            Bounce = 0.0f,
            Rough = true,
        };

        // Side rails, low enough not to foul a transfer along X and tall enough
        // that a carton nudged sideways during a hoist stays aboard.
        foreach (int sz in new[] { -1, 1 })
        {
            _carriage.AddChild(new MeshInstance3D
            {
                Name = sz < 0 ? "RailNear" : "RailFar",
                Mesh = new BoxMesh { Size = new Vector3(DeckLength, 0.07f, 0.025f) },
                MaterialOverride = accentMat,
                Position = new Vector3(0, DeckTopLocal + 0.035f, sz * (DeckWidth / 2.0f - 0.015f)),
            });
            _carriage.AddChild(new CollisionShape3D
            {
                Name = sz < 0 ? "RailShapeNear" : "RailShapeFar",
                Shape = new BoxShape3D { Size = new Vector3(DeckLength, 0.07f, 0.025f) },
                Position = new Vector3(0, DeckTopLocal + 0.035f, sz * (DeckWidth / 2.0f - 0.015f)),
            });
        }

        // The yoke the carriage hangs from, so it reads as running in the mast.
        foreach (int sz in new[] { -1, 1 })
        {
            _carriage.AddChild(new MeshInstance3D
            {
                Name = sz < 0 ? "ShoeNear" : "ShoeFar",
                Mesh = new BoxMesh { Size = new Vector3(0.12f, 0.16f, 0.06f) },
                MaterialOverride = frameMat,
                Position = new Vector3(0, DeckTopLocal + 0.05f, sz * MastOffsetZ),
            });
        }

        // What counts as "a carton is aboard". Deliberately inset from the deck
        // ends: a carton still straddling the mouth is not yet aboard, and
        // closing the gate underneath one is how a gate breaks its own load.
        _deckZone = new Area3D { Name = "DeckZone", Monitoring = true };
        _deckZone.AddChild(new CollisionShape3D
        {
            Shape = new BoxShape3D
            {
                Size = new Vector3(DeckLength * 0.62f, 0.34f, DeckWidth * 0.9f),
            },
            Position = new Vector3(0, DeckTopLocal + 0.17f, 0),
        });
        _carriage.AddChild(_deckZone);
    }

    private const float RampRun = 0.10f;
    private const float RampRise = 0.05f;
    private const float RampThickness = 0.04f;

    /// <summary><see cref="ConveyorBelt.AddTransferRamps"/>, on the carriage:
    /// the deck is the one surface in this library a carton has to step onto
    /// from a belt that is not bolted to it, so gotcha 23 applies twice
    /// over.</summary>
    private void AddTransferRamps()
    {
        float theta = Mathf.Atan2(RampRise, RampRun);
        float dx = RampRun / 2.0f * Mathf.Cos(theta) - RampThickness / 2.0f * Mathf.Sin(theta);
        float dy = RampRun / 2.0f * Mathf.Sin(theta) + RampThickness / 2.0f * Mathf.Cos(theta);

        foreach (int end in new[] { -1, 1 })
        {
            var ramp = new CollisionShape3D
            {
                Name = end < 0 ? "TailTransferRamp" : "HeadTransferRamp",
                Shape = new BoxShape3D { Size = new Vector3(RampRun, RampThickness, DeckWidth) },
                Position = new Vector3(end * (DeckLength / 2.0f + dx), DeckTopLocal - dy, 0),
            };
            ramp.RotateZ(-end * theta);
            _carriage.AddChild(ramp);
        }
    }

    /// <summary>
    /// The entry gate: a blade across the infeed mouth, on the fixed structure
    /// rather than on the carriage, because it has to stand there when the
    /// carriage has gone.
    /// </summary>
    private void BuildGate(StandardMaterial3D accentMat)
    {
        _blade = new Node3D { Name = "EntryGate" };
        AddChild(_blade);

        var body = new StaticBody3D { Name = "GateBlade" };
        _blade.AddChild(body);

        body.AddChild(new MeshInstance3D
        {
            Name = "BladePlate",
            Mesh = new BoxMesh { Size = new Vector3(0.028f, BladeHeight, DeckWidth) },
            MaterialOverride = accentMat,
        });
        body.AddChild(new CollisionShape3D
        {
            Shape = new BoxShape3D { Size = new Vector3(0.028f, BladeHeight, DeckWidth) },
        });
        body.PhysicsMaterialOverride = new PhysicsMaterial
        {
            Friction = 0.4f, Bounce = 0.0f, Rough = true,
        };
    }

    /// <summary>Blade centre when fully open: entirely below the infeed deck
    /// surface, so a carton passes over it without touching.</summary>
    private float BladeOpenY => HeightOf(InfeedLevel) + PartLayout.BeltSurface - BladeHeight / 2.0f - 0.03f;

    private float BladeClosedY => BladeOpenY + BladeHeight - 0.04f;

    private void ApplyBlade()
    {
        if (_blade is null) return;
        _blade.Position = new Vector3(
            -(DeckLength / 2.0f + 0.11f),
            Mathf.Lerp(BladeOpenY, BladeClosedY, _bladeRise),
            0);
        UpdateReadyLamp();
    }

    private void BuildLamps()
    {
        _faultLampMat = new StandardMaterial3D
        {
            AlbedoColor = new Color(0.30f, 0.06f, 0.06f),
            Metallic = 0.10f,
            Roughness = 0.35f,
        };
        _readyLampMat = new StandardMaterial3D
        {
            AlbedoColor = new Color(0.06f, 0.24f, 0.10f),
            Metallic = 0.10f,
            Roughness = 0.35f,
        };

        var mount = new Vector3(-(DeckLength / 2.0f + 0.11f), 0.10f, MastOffsetZ + 0.10f);
        const float stalk = 0.30f;

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
        // A second lamp for "the carriage will take one", beside the gate it
        // describes. A gate that is shut and a gate that is shut *because the
        // carriage is elsewhere* look identical, and the difference is the
        // whole handshake.
        AddChild(new MeshInstance3D
        {
            Name = "GateReadyLamp",
            Mesh = new SphereMesh { Radius = 0.032f, Height = 0.064f },
            MaterialOverride = _readyLampMat,
            Position = mount + new Vector3(0, stalk - 0.10f, -0.06f),
        });
    }

    private void UpdateReadyLamp()
    {
        if (_readyLampMat is null) return;
        bool on = IsReady;
        _readyLampMat.AlbedoColor = on ? new Color(0.25f, 1.0f, 0.35f) : new Color(0.06f, 0.24f, 0.10f);
        _readyLampMat.EmissionEnabled = on;
        _readyLampMat.Emission = on ? new Color(0.25f, 1.0f, 0.35f) : Colors.Black;
        _readyLampMat.EmissionEnergyMultiplier = on ? 1.8f : 0.0f;
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

    /// <summary>One machine tick: read the deck, hoist, move the gate, drive
    /// the transfer. Occupancy first, because everything else this tick depends
    /// on whether there is a carton aboard.</summary>
    public void Step(int target, bool transfer, float delta)
    {
        _target = target;
        _occupied = DeckHasCarton();

        if (!IsFaulted)
            _height = Mathf.MoveToward(_height, HeightOf(TargetLevel), HoistSpeed * delta);

        ApplyCarriage();

        float wantRise = GateShouldOpen ? 0.0f : 1.0f;
        _bladeRise = Mathf.MoveToward(_bladeRise, wantRise, BladeSpeed * delta);
        ApplyBlade();

        // A faulted hoist does not run its transfer either: the one thing worse
        // than a carriage stuck between floors is a carriage stuck between
        // floors feeding cartons into the gap.
        bool driving = transfer && !IsFaulted;
        _carriage.ConstantLinearVelocity = driving ? new Vector3(TransferSpeed, 0, 0) : Vector3.Zero;
    }

    private bool DeckHasCarton()
    {
        if (_deckZone is null) return false;
        foreach (var body in _deckZone.GetOverlappingBodies())
        {
            if (body is BoxPhysics carton && IsInstanceValid(carton)) return true;
        }
        return false;
    }

    private void ApplyCarriage()
    {
        if (_carriage is null) return;
        _carriage.Position = new Vector3(0, CarriageBaseY + _height, 0);
    }

    /// <summary>Park at level 0 with the gate shut, for a scene reset. The gate
    /// re-opens on the first tick if the carriage belongs there and is
    /// empty — decided by the machine, never restored from a saved
    /// state.</summary>
    public void ResetLift()
    {
        _height = 0.0f;
        _target = 0;
        _bladeRise = 1.0f;
        _occupied = false;
        if (_carriage is not null) _carriage.ConstantLinearVelocity = Vector3.Zero;
        ApplyCarriage();
        ApplyBlade();
    }

    // ---------- IPart

    public void DeclareTags(PartTagBuilder tags) => tags
        .Int("target", $"Lift {tags.Index} Call Level", TagKind.Output)
        .Bit("transfer", $"Lift {tags.Index} Deck Transfer", TagKind.Output)
        .Int("level", $"Lift {tags.Index} At Level", TagKind.Input)
        .Bit("atlevel", $"Lift {tags.Index} In Position", TagKind.Input, initial: true)
        .Float("height", $"Lift {tags.Index} Height (m)", TagKind.Input)
        .Bit("occupied", $"Lift {tags.Index} Carriage Occupied", TagKind.Input)
        .Bit("ready", $"Lift {tags.Index} Ready To Accept", TagKind.Input)
        .Bit("fault", $"Lift {tags.Index} Drive Fault", TagKind.Input);

    public void CaptureSettings(PartSettings settings)
    {
        settings.Put("levels", Levels);
        settings.Put("spacing", LevelSpacing);
        settings.Put("hoist_speed", HoistSpeed);
        settings.Put("infeed_level", InfeedLevel);
        settings.Put("transfer_speed", TransferSpeed);
        settings.Put("tolerance", LevelTolerance);
    }

    public void ApplySettings(PartSettings settings)
    {
        // Clamped where they arrive: a hand-edited scene file with a zero pitch
        // would otherwise divide by it in NearestLevel before _Ready ever ran.
        if (settings.Whole("levels") is { } levels) Levels = Mathf.Max(1, levels);
        if (settings.Number("spacing") is { } spacing) LevelSpacing = Mathf.Max(MinSpacing, spacing);
        if (settings.Number("hoist_speed") is { } hoist) HoistSpeed = hoist;
        if (settings.Whole("infeed_level") is { } infeed) InfeedLevel = Mathf.Max(0, infeed);
        if (settings.Number("transfer_speed") is { } transfer) TransferSpeed = transfer;
        if (settings.Number("tolerance") is { } tolerance) LevelTolerance = tolerance;
    }

    public void StepPart(PartTick tick)
    {
        if (tick.TryBit("fault", out bool faulted)) SetFaulted(faulted);

        Step(tick.Whole("target"), tick.Bit("transfer"), tick.Dt);

        tick.Write("level", NearestLevel);
        tick.Write("atlevel", IsAtLevel);
        tick.Write("height", (double)_height);
        tick.Write("occupied", IsOccupied);
        tick.Write("ready", IsReady);
    }

    /// <summary>The mast's level count and pitch are build-time geometry — the
    /// plates, the columns and the head beam are all sized from them — so they
    /// are deliberately absent rather than offered and silently ignored, the
    /// same call <see cref="TurnTable"/> makes about its deck radius.</summary>
    public void DescribeControls(IPartInspector ui)
    {
        ui.Slider("Hoist Speed (m/s)", HoistSpeed, 0.1f, 3.0f, 0.05f, value => HoistSpeed = value);
        ui.Slider("Transfer Speed (m/s)", TransferSpeed, 0.1f, 2.0f, 0.05f,
                  value => TransferSpeed = value);
        ui.Slider("Level Window (m)", LevelTolerance, 0.005f, 0.2f, 0.005f,
                  value => LevelTolerance = value);
    }

    public void ResetPart(PartReset reset)
    {
        ResetLift();
        reset.Write("level", 0);
        reset.Write("atlevel", true);
        reset.Write("height", 0.0);
        reset.Write("occupied", false);
        reset.Write("ready", false);
    }

    /// <summary>A click calls the carriage to the next level up, wrapping at
    /// the top — which is how somebody checks a mast reaches where they think
    /// it does without writing a program first.</summary>
    public PartOperation? Operation => new("lift", "target");

    public void Operate(PartOperate op) =>
        op.Force("target", (TargetLevel + 1) % EffectiveLevels);
}
