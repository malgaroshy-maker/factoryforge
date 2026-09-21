using FactoryForge.TagBus;
using Godot;

namespace FactoryForge.Parts;

/// <summary>
/// A pallet and the pattern that fills it (HA-02).
///
/// <b>What nothing else in the library teaches.</b> Every machine here so far
/// does one motion when it is told to. Not one of them can express "the same
/// motion, N times, to N different places, in a pattern that then repeats one
/// layer higher" — which is the shape of an enormous amount of real control
/// code, and the first place a student meets an index register, a modulo, a
/// nested counter and a "this resource is now full, stop sending" handshake all
/// in the same program.
///
/// <b>It is a pattern generator, not a robot.</b> The obvious way to build a
/// palletiser is as one machine that both computes the pattern and performs the
/// motion — and it would then be a worse <see cref="ArticulatedArm"/> welded to
/// a counter, useful with nothing else. This is the other half only: it holds
/// the pallet, it knows where the next carton goes, and it counts. The motion
/// is whatever the scene already has — the arm, the gantry, a pusher — so the
/// student writes the part that is actually theirs to write, which is the
/// sequence that reads a position, moves to it, releases, and indexes.
///
/// <b>The three things it makes true.</b> The pattern <em>repeats</em>: slot
/// zero of layer one is slot zero of layer zero, one <see cref="LayerHeight"/>
/// higher. Layers <em>interlock</em>: with <see cref="AlternateLayers"/> the
/// grid turns a quarter-turn on every odd layer, which is why a real stack does
/// not shear apart, and which means "the pattern" is not simply a list of
/// coordinates. And the pallet <em>fills</em>: past capacity <c>full</c> goes
/// high and further index pulses are refused outright, so the program has to
/// handle a pallet change instead of counting for ever.
///
/// <b>Positions are metres in the work-plane frame.</b> <c>nextx</c> and
/// <c>nextz</c> are offsets from the pallet centre and <c>nexty</c> is the
/// height of the surface the next carton lands on, measured from this part's
/// origin. Since every part's origin sits on the work plane, an arm's
/// <c>height</c> and this station's <c>nexty</c> are numbers in the same frame
/// and can be compared directly — which is the convention doing real work
/// rather than being a tidiness rule.
///
/// Local space: the origin is the pallet centre on the work plane, and the
/// pallet's top deck is flush with a standard conveyor's carrying surface, with
/// the same lead-in wedges a conveyor has (gotcha 23) so a carton pushed across
/// rides up rather than into the edge.
/// </summary>
public partial class PalletStation : Node3D, IPart
{
    /// <summary>Slots across the pallet, along X.</summary>
    [Export] public int Columns { get; set; } = 3;

    /// <summary>Slots across the pallet, along Z.</summary>
    [Export] public int Rows { get; set; } = 2;

    /// <summary>How many layers make a full pallet.</summary>
    [Export] public int Layers { get; set; } = 3;

    /// <summary>Slot pitch along X and Z, metres. A standard carton is
    /// 0.20 × 0.24, so these leave a working gap rather than a butt joint.</summary>
    [Export] public float PitchX { get; set; } = 0.24f;
    [Export] public float PitchZ { get; set; } = 0.28f;

    /// <summary>Rise per layer, metres.</summary>
    [Export] public float LayerHeight { get; set; } = 0.12f;

    /// <summary>
    /// Turn the grid a quarter-turn on every odd layer.
    ///
    /// On by default, because an interlocked stack is what a palletiser is
    /// <em>for</em> — a column of identical layers falls off the truck — and
    /// because it is what stops "the pattern" being a list of six coordinates
    /// the program can hard-code. Saved but deliberately absent from the
    /// inspector: the panel offers sliders, text and tag pickers, and a
    /// two-state setting rendered as a slider from 0 to 1 is worse than one
    /// that lives in the scene file where a template can set it.
    /// </summary>
    [Export] public bool AlternateLayers { get; set; } = true;

    [Export] public float PalletWidth { get; set; } = 0.90f;
    [Export] public float PalletDepth { get; set; } = 0.80f;

    /// <summary>Pallet thickness, top deck to the floor of the runners.</summary>
    private const float PalletThickness = 0.12f;

    /// <summary>Top of the pallet, relative to the part origin. Flush with a
    /// standard conveyor's carrying surface, so a carton can be pushed straight
    /// across onto the bottom layer.</summary>
    private const float DeckTop = PartLayout.BeltSurface;

    private Area3D _stackZone = null!;
    private Node3D _dropMarker = null!;
    private StandardMaterial3D _markerMat = null!;

    private int _count;
    private bool _lastIndex;
    private bool _lastChange;

    /// <summary>
    /// Cartons placed on the current pallet.
    ///
    /// Counted from the controller's index pulses rather than from what is
    /// physically on the deck, and that is the honest model: a palletiser
    /// counts what it <em>released</em>. A carton that bounced off is a carton
    /// the machine believes is on the pallet, which is precisely the kind of
    /// disagreement a real line has and a check weigher exists to find.
    /// </summary>
    public int Count => _count;

    // Every one of these is clamped to at least one. Columns or Rows at zero
    // would be an integer modulo by zero in SlotOffset -- which throws rather
    // than producing a NaN, and would take the whole tick dispatch with it --
    // and a zero capacity would make `full` true on an empty pallet.
    private int EffectiveColumns => Mathf.Max(1, Columns);
    private int EffectiveRows => Mathf.Max(1, Rows);
    private int EffectiveLayers => Mathf.Max(1, Layers);

    public int SlotsPerLayer => EffectiveColumns * EffectiveRows;
    public int Capacity => SlotsPerLayer * EffectiveLayers;

    public bool IsFull => _count >= Capacity;

    /// <summary>A layer has just been completed — true from the index that
    /// finished it until the one that starts the next. A level, not an edge, so
    /// a program that scans slowly cannot miss it.</summary>
    public bool LayerComplete => _count > 0 && _count % SlotsPerLayer == 0;

    /// <summary>Which carton the published position describes. Held at the last
    /// slot once the pallet is full, so a full pallet does not report "next:
    /// slot 0, layer 0" and invite a program to place there.</summary>
    private int PositionIndex => Mathf.Min(_count, Capacity - 1);

    public int NextSlot => PositionIndex % SlotsPerLayer;
    public int NextLayer => PositionIndex / SlotsPerLayer;

    /// <summary>
    /// Where the next carton goes, in the part's own frame: X and Z offset from
    /// the pallet centre, Y the height of the surface it lands on.
    /// </summary>
    public Vector3 NextPosition => SlotOffset(NextSlot, NextLayer);

    public Vector3 SlotOffset(int slot, int layer)
    {
        bool turned = AlternateLayers && (layer & 1) == 1;

        int cols = turned ? EffectiveRows : EffectiveColumns;
        int rows = turned ? EffectiveColumns : EffectiveRows;
        float pitchX = turned ? PitchZ : PitchX;
        float pitchZ = turned ? PitchX : PitchZ;

        int column = slot % cols;
        int row = slot / cols % rows;

        return new Vector3(
            (column - (cols - 1) / 2.0f) * pitchX,
            DeckTop + layer * LayerHeight,
            (row - (rows - 1) / 2.0f) * pitchZ);
    }

    public override void _Ready()
    {
        BuildStand();
        BuildPallet();
        BuildDropMarker();

        _stackZone = new Area3D { Name = "StackZone", Monitoring = true };
        _stackZone.AddChild(new CollisionShape3D
        {
            Shape = new BoxShape3D
            {
                Size = new Vector3(PalletWidth, EffectiveLayers * LayerHeight + 0.45f, PalletDepth),
            },
            Position = new Vector3(0, DeckTop + (EffectiveLayers * LayerHeight + 0.45f) / 2.0f, 0),
        });
        AddChild(_stackZone);

        UpdateMarker();
    }

    private void BuildStand()
    {
        var frameMat = new StandardMaterial3D
        {
            AlbedoColor = new Color(0.22f, 0.24f, 0.27f),
            Metallic = 0.45f,
            Roughness = 0.50f,
        };

        float underside = DeckTop - PalletThickness;
        float legHeight = underside + PartLayout.FloorDrop;
        float legX = PalletWidth / 2.0f - 0.07f;
        float legZ = PalletDepth / 2.0f - 0.07f;

        foreach (int sx in new[] { -1, 1 })
        {
            foreach (int sz in new[] { -1, 1 })
            {
                AddChild(new MeshInstance3D
                {
                    Name = $"Leg{(sx < 0 ? "L" : "R")}{(sz < 0 ? "N" : "F")}",
                    Mesh = new BoxMesh { Size = new Vector3(0.07f, legHeight, 0.07f) },
                    MaterialOverride = frameMat,
                    Position = new Vector3(sx * legX, underside - legHeight / 2.0f, sz * legZ),
                });
            }
        }

        // A rail tying the legs together just under the pallet, so the stand
        // reads as a welded frame rather than four posts that happen to line up.
        foreach (int sz in new[] { -1, 1 })
        {
            AddChild(new MeshInstance3D
            {
                Name = sz < 0 ? "RailNear" : "RailFar",
                Mesh = new BoxMesh { Size = new Vector3(legX * 2.0f + 0.07f, 0.05f, 0.05f) },
                MaterialOverride = frameMat,
                Position = new Vector3(0, underside - 0.10f, sz * legZ),
            });
        }
    }

    /// <summary>
    /// The pallet: top boards, stringers and bottom runners, in timber.
    ///
    /// Worth the meshes. A flat grey slab would read as another conveyor deck,
    /// and the one thing a person has to understand at a glance here is that
    /// the stack is being built on something that will be driven away.
    /// </summary>
    private void BuildPallet()
    {
        var timber = new StandardMaterial3D
        {
            AlbedoColor = new Color(0.60f, 0.46f, 0.29f),
            Roughness = 0.88f,
        };
        var timberDark = new StandardMaterial3D
        {
            AlbedoColor = new Color(0.47f, 0.35f, 0.21f),
            Roughness = 0.90f,
        };

        var deck = new StaticBody3D { Name = "PalletDeck" };
        AddChild(deck);

        deck.AddChild(new CollisionShape3D
        {
            Shape = new BoxShape3D
            {
                Size = new Vector3(PalletWidth, PalletThickness, PalletDepth),
            },
            Position = new Vector3(0, DeckTop - PalletThickness / 2.0f, 0),
        });
        AddTransferRamps(deck);

        deck.PhysicsMaterialOverride = new PhysicsMaterial
        {
            // Timber under cardboard: grippier than a rubber belt, because a
            // stack that creeps while the next carton lands on it is a stack
            // that falls over.
            Friction = 0.85f,
            Bounce = 0.0f,
            Rough = true,
        };

        const float boardThickness = 0.022f;
        const int topBoards = 5;
        for (int i = 0; i < topBoards; i++)
        {
            float t = topBoards == 1 ? 0.5f : i / (float)(topBoards - 1);
            float z = Mathf.Lerp(-PalletDepth / 2.0f + 0.06f, PalletDepth / 2.0f - 0.06f, t);
            deck.AddChild(new MeshInstance3D
            {
                Name = $"TopBoard{i}",
                Mesh = new BoxMesh { Size = new Vector3(PalletWidth, boardThickness, 0.10f) },
                MaterialOverride = timber,
                Position = new Vector3(0, DeckTop - boardThickness / 2.0f, z),
            });
        }

        foreach (int sx in new[] { -1, 0, 1 })
        {
            deck.AddChild(new MeshInstance3D
            {
                Name = $"Stringer{(sx < 0 ? "L" : sx == 0 ? "C" : "R")}",
                Mesh = new BoxMesh
                {
                    Size = new Vector3(0.09f, PalletThickness - boardThickness * 2.0f, PalletDepth),
                },
                MaterialOverride = timberDark,
                Position = new Vector3(sx * (PalletWidth / 2.0f - 0.06f),
                                       DeckTop - PalletThickness / 2.0f, 0),
            });
            deck.AddChild(new MeshInstance3D
            {
                Name = $"Runner{(sx < 0 ? "L" : sx == 0 ? "C" : "R")}",
                Mesh = new BoxMesh { Size = new Vector3(0.09f, boardThickness, PalletDepth) },
                MaterialOverride = timber,
                Position = new Vector3(sx * (PalletWidth / 2.0f - 0.06f),
                                       DeckTop - PalletThickness + boardThickness / 2.0f, 0),
            });
        }
    }

    private const float RampRun = 0.10f;
    private const float RampRise = 0.05f;
    private const float RampThickness = 0.04f;

    /// <summary>
    /// The same lead-in wedge <see cref="ConveyorBelt.AddTransferRamps"/> puts
    /// on a belt, for the same reason (gotcha 23): a carton resting on a belt
    /// settles a centimetre or two inside it, and would then meet the vertical
    /// edge of the pallet head-on and stop dead. Collision only, so it does not
    /// grow the part's selection box.
    /// </summary>
    private void AddTransferRamps(StaticBody3D deck)
    {
        float theta = Mathf.Atan2(RampRise, RampRun);
        float dx = RampRun / 2.0f * Mathf.Cos(theta) - RampThickness / 2.0f * Mathf.Sin(theta);
        float dy = RampRun / 2.0f * Mathf.Sin(theta) + RampThickness / 2.0f * Mathf.Cos(theta);

        foreach (int end in new[] { -1, 1 })
        {
            var ramp = new CollisionShape3D
            {
                Name = end < 0 ? "TailTransferRamp" : "HeadTransferRamp",
                Shape = new BoxShape3D
                {
                    Size = new Vector3(RampRun, RampThickness, PalletDepth),
                },
                Position = new Vector3(end * (PalletWidth / 2.0f + dx), DeckTop - dy, 0),
            };
            ramp.RotateZ(-end * theta);
            deck.AddChild(ramp);
        }
    }

    /// <summary>
    /// A lit plate on the slot the next carton is meant to land on.
    ///
    /// The pattern is the part, and until this existed the pattern was three
    /// float tags and a stack that grew in a way nobody could check by looking.
    /// Watching the marker hop along a row, jump back and rise a layer, then
    /// turn a quarter-turn on the next one, is the whole lesson in one glance.
    /// </summary>
    private void BuildDropMarker()
    {
        _markerMat = new StandardMaterial3D
        {
            AlbedoColor = new Color(0.20f, 0.95f, 0.45f),
            EmissionEnabled = true,
            Emission = new Color(0.20f, 0.95f, 0.45f),
            EmissionEnergyMultiplier = 1.1f,
            Roughness = 0.6f,
        };

        _dropMarker = new Node3D { Name = "DropMarker" };
        AddChild(_dropMarker);

        // An outline rather than a filled pad: a solid plate under a carton
        // looks like the carton is glowing, and after the first layer it would
        // be buried anyway.
        const float halfX = 0.11f;
        const float halfZ = 0.13f;
        const float bar = 0.012f;
        foreach (int sx in new[] { -1, 1 })
        {
            _dropMarker.AddChild(new MeshInstance3D
            {
                Name = sx < 0 ? "EdgeL" : "EdgeR",
                Mesh = new BoxMesh { Size = new Vector3(bar, 0.004f, halfZ * 2.0f) },
                MaterialOverride = _markerMat,
                Position = new Vector3(sx * halfX, 0, 0),
            });
        }
        foreach (int sz in new[] { -1, 1 })
        {
            _dropMarker.AddChild(new MeshInstance3D
            {
                Name = sz < 0 ? "EdgeN" : "EdgeF",
                Mesh = new BoxMesh { Size = new Vector3(halfX * 2.0f, 0.004f, bar) },
                MaterialOverride = _markerMat,
                Position = new Vector3(0, 0, sz * halfZ),
            });
        }
    }

    private void UpdateMarker()
    {
        if (_dropMarker is null) return;

        Vector3 next = NextPosition;
        _dropMarker.Position = next + new Vector3(0, 0.004f, 0);
        // A quarter-turn on an interlocked layer, so the marker shows the
        // orientation the carton is meant to arrive in as well as the place.
        bool turned = AlternateLayers && (NextLayer & 1) == 1;
        _dropMarker.Rotation = new Vector3(0, turned ? Mathf.Pi / 2.0f : 0.0f, 0);
        _dropMarker.Visible = !IsFull;

        if (_markerMat is null) return;
        var colour = IsFull ? new Color(0.95f, 0.25f, 0.15f) : new Color(0.20f, 0.95f, 0.45f);
        _markerMat.AlbedoColor = colour;
        _markerMat.Emission = colour;
    }

    /// <summary>One index: a carton has been placed, advance the pattern.
    /// Refused on a full pallet, which is the point — the position tags do not
    /// move, so a program that ignores <c>full</c> keeps placing on the same
    /// slot rather than walking off the end of the stack.</summary>
    public bool Index()
    {
        if (IsFull) return false;
        _count++;
        UpdateMarker();
        return true;
    }

    /// <summary>
    /// The full pallet leaves and an empty one arrives: the count goes back to
    /// zero and whatever is standing on the deck goes with it.
    ///
    /// Clearing the stack is not housekeeping. A pallet change that left the
    /// cartons behind would put the next layer inside the last one, and a line
    /// left running overnight would grow a tower through the roof.
    /// </summary>
    public void ChangePallet()
    {
        _count = 0;
        ClearStack();
        UpdateMarker();
    }

    private void ClearStack()
    {
        if (_stackZone is null) return;
        foreach (var body in _stackZone.GetOverlappingBodies())
        {
            if (body is BoxPhysics carton && IsInstanceValid(carton)) carton.QueueFree();
        }
    }

    // ---------- IPart

    public void DeclareTags(PartTagBuilder tags) => tags
        .Bit("index", $"Pallet {tags.Index} Index", TagKind.Output)
        .Bit("change", $"Pallet {tags.Index} Pallet Change", TagKind.Output)
        .Float("nextx", $"Pallet {tags.Index} Next X (m)", TagKind.Input)
        .Float("nexty", $"Pallet {tags.Index} Next Y (m)", TagKind.Input)
        .Float("nextz", $"Pallet {tags.Index} Next Z (m)", TagKind.Input)
        .Int("slot", $"Pallet {tags.Index} Next Slot", TagKind.Input)
        .Int("layer", $"Pallet {tags.Index} Next Layer", TagKind.Input)
        .Int("count", $"Pallet {tags.Index} Cartons", TagKind.Input)
        .Bit("layerdone", $"Pallet {tags.Index} Layer Complete", TagKind.Input)
        .Bit("full", $"Pallet {tags.Index} Full", TagKind.Input);

    public void CaptureSettings(PartSettings settings)
    {
        settings.Put("columns", Columns);
        settings.Put("rows", Rows);
        settings.Put("layers", Layers);
        settings.Put("pitch_x", PitchX);
        settings.Put("pitch_z", PitchZ);
        settings.Put("layer_height", LayerHeight);
        settings.Put("alternate", AlternateLayers);
        settings.Put("pallet_width", PalletWidth);
        settings.Put("pallet_depth", PalletDepth);
    }

    public void ApplySettings(PartSettings settings)
    {
        // Clamped on the way in, not only where they are used: a scene file is
        // editable by hand, and "columns: 0" is a modulo by zero.
        if (settings.Whole("columns") is { } columns) Columns = Mathf.Max(1, columns);
        if (settings.Whole("rows") is { } rows) Rows = Mathf.Max(1, rows);
        if (settings.Whole("layers") is { } layers) Layers = Mathf.Max(1, layers);
        if (settings.Number("pitch_x") is { } pitchX) PitchX = pitchX;
        if (settings.Number("pitch_z") is { } pitchZ) PitchZ = pitchZ;
        if (settings.Number("layer_height") is { } height) LayerHeight = height;
        if (settings.Flag("alternate") is { } alternate) AlternateLayers = alternate;
        if (settings.Number("pallet_width") is { } width) PalletWidth = width;
        if (settings.Number("pallet_depth") is { } depth) PalletDepth = depth;
    }

    public void StepPart(PartTick tick)
    {
        bool index = tick.Bit("index");
        bool change = tick.Bit("change");

        // A pallet change first: a scan that both indexes and changes has
        // plainly finished with this pallet, and counting a carton onto one
        // that is driving away is the wrong of the two answers.
        if (change && !_lastChange) ChangePallet();
        else if (index && !_lastIndex) Index();

        _lastIndex = index;
        _lastChange = change;

        Vector3 next = NextPosition;
        tick.Write("nextx", (double)next.X);
        tick.Write("nexty", (double)next.Y);
        tick.Write("nextz", (double)next.Z);
        tick.Write("slot", NextSlot);
        tick.Write("layer", NextLayer);
        tick.Write("count", _count);
        tick.Write("layerdone", LayerComplete);
        tick.Write("full", IsFull);
    }

    /// <summary>The pallet's own size and the slot pitch are build-time
    /// geometry and deliberately absent, the same call <see cref="TurnTable"/>
    /// makes about its deck radius. The pattern counts are not: they are read
    /// every tick, so moving one re-indexes immediately.</summary>
    public void DescribeControls(IPartInspector ui)
    {
        ui.Slider("Columns", Columns, 1.0f, 8.0f, 1.0f,
                  value => { Columns = Mathf.Max(1, (int)value); UpdateMarker(); });
        ui.Slider("Rows", Rows, 1.0f, 8.0f, 1.0f,
                  value => { Rows = Mathf.Max(1, (int)value); UpdateMarker(); });
        ui.Slider("Layers", Layers, 1.0f, 12.0f, 1.0f,
                  value => { Layers = Mathf.Max(1, (int)value); UpdateMarker(); });
        ui.Slider("Layer Height (m)", LayerHeight, 0.05f, 0.6f, 0.01f,
                  value => { LayerHeight = value; UpdateMarker(); });
    }

    public void ResetPart(PartReset reset)
    {
        ChangePallet();
        _lastIndex = false;
        _lastChange = false;
        reset.Write("count", 0);
        reset.Write("slot", 0);
        reset.Write("layer", 0);
        reset.Write("full", false);
        reset.Write("layerdone", false);
    }

    /// <summary>A click steps the pattern by hand, which is how somebody
    /// building a scene finds out where the fifth carton is meant to go without
    /// writing a program first. A pulse, not a latch: <c>index</c> means a
    /// rising edge.</summary>
    public PartOperation? Operation => new("pallet station", "index");

    public void Operate(PartOperate op) => op.PulseBit("index");
}
