using FactoryForge.TagBus;
using Godot;

namespace FactoryForge.Parts;

/// <summary>
/// A transfer turntable that indexes a carton through an angle (LP-02).
///
/// Every line built from this library so far has been a straight line, because
/// nothing in it could turn a product. A turntable is the cheapest way a real
/// plant changes direction, and as a control problem it is the first *rotary
/// index* here: a motion with two limit switches that has to finish before the
/// next thing starts, and which cannot be hurried, because the load is held on
/// by friction and nothing else.
///
/// That last part is not a detail. The deck is an <see cref="AnimatableBody3D"/>
/// and the carton is carried by contact friction rather than by being parented
/// to it, so a deck told to index too fast genuinely throws its load off the
/// edge. That is the real constraint on how fast a transfer can run, and it is
/// only teachable if the simulation actually has it.
///
/// Local space: the origin is the deck centre, on the work plane, so the
/// turntable drops onto the grid at the same height as the belts either side.
/// </summary>
public partial class TurnTable : Node3D, IPart
{
    /// <summary>Deck radius. The default is sized to take a carton off a
    /// standard 0.5 m belt with room to turn it.</summary>
    [Export] public float DeckRadius { get; set; } = 0.34f;

    /// <summary>Angle the deck indexes to, in degrees. 90° is the classic
    /// right-angle transfer; 180° turns a carton back the way it came.</summary>
    [Export] public float IndexAngle { get; set; } = 90.0f;

    /// <summary>Index rate, degrees per second. Deliberately slow by default:
    /// the load is held on by friction, and the speed at which it stops being
    /// held on is a thing worth discovering.</summary>
    [Export] public float IndexSpeed { get; set; } = 55.0f;

    private float DeckThickness => PartLayout.BeltThickness;

    private AnimatableBody3D _deck = null!;
    private StandardMaterial3D? _faultLampMat;

    private float _angle;

    /// <summary>Deck angle in degrees, 0 at home. Exposed for the same reason
    /// the diverter exposes its own: two limit switches cannot distinguish
    /// "seized at 40°" from "still travelling".</summary>
    public float Angle => _angle;

    public bool IsHome => _angle <= 0.5f;
    public bool IsAtIndex => _angle >= IndexAngle - 0.5f;

    /// <summary>Seized: the deck stops where it is. A turntable stuck between
    /// its two limits blocks the infeed and the outfeed at once, which is why
    /// it is worth being able to break.</summary>
    public bool IsFaulted { get; private set; }

    public override void _Ready()
    {
        var frameMat = new StandardMaterial3D
        {
            AlbedoColor = new Color(0.20f, 0.22f, 0.25f),
            Metallic = 0.45f,
            Roughness = 0.50f,
        };
        var deckMat = new StandardMaterial3D
        {
            AlbedoColor = new Color(0.30f, 0.32f, 0.35f),
            Metallic = 0.55f,
            Roughness = 0.45f,
        };

        // Pedestal and foot, down to the floor like every free-standing part.
        float pedestalHeight = PartLayout.FloorDrop - DeckThickness / 2.0f;
        AddChild(new MeshInstance3D
        {
            Name = "Pedestal",
            Mesh = new CylinderMesh
            {
                TopRadius = 0.11f, BottomRadius = 0.13f, Height = pedestalHeight,
            },
            MaterialOverride = frameMat,
            Position = new Vector3(0, -DeckThickness / 2.0f - pedestalHeight / 2.0f, 0),
        });
        AddChild(new MeshInstance3D
        {
            Name = "BaseFoot",
            Mesh = new CylinderMesh { TopRadius = 0.24f, BottomRadius = 0.24f, Height = 0.03f },
            MaterialOverride = frameMat,
            Position = new Vector3(0, -PartLayout.FloorDrop, 0),
        });

        _deck = new AnimatableBody3D
        {
            Name = "Deck",
            // Without this the deck teleports between frames and the solver
            // sees no surface velocity at all — the carton sits perfectly still
            // on a visibly spinning plate.
            SyncToPhysics = true,
        };
        _deck.AddChild(new MeshInstance3D
        {
            Name = "DeckPlate",
            Mesh = new CylinderMesh
            {
                TopRadius = DeckRadius, BottomRadius = DeckRadius, Height = DeckThickness,
            },
            MaterialOverride = deckMat,
        });
        _deck.AddChild(new CollisionShape3D
        {
            Shape = new CylinderShape3D { Radius = DeckRadius, Height = DeckThickness },
        });
        // Grippy, because friction is the only thing holding the load on. At
        // the belt's own 0.70 a carton slides off during the first quarter turn
        // and the part reads as broken rather than as fast.
        _deck.PhysicsMaterialOverride = new PhysicsMaterial { Friction = 0.95f, Bounce = 0.0f, Rough = true };

        // A painted lane stripe across the deck. A bare disc looks identical at
        // every angle — the same lesson the roller deck and the selector knob
        // both taught — so without this the index is invisible.
        _deck.AddChild(new MeshInstance3D
        {
            Name = "LaneStripe",
            Mesh = new BoxMesh { Size = new Vector3(DeckRadius * 2.0f * 0.95f, 0.004f, 0.07f) },
            MaterialOverride = new StandardMaterial3D
            {
                AlbedoColor = new Color(0.95f, 0.75f, 0.10f),
                Roughness = 0.60f,
            },
            Position = new Vector3(0, DeckThickness / 2.0f + 0.002f, 0),
        });

        AddChild(_deck);

        AddTransferCollar();
        BuildFaultLamp();
        ApplyAngle();
    }

    /// <summary>Wedge count around the rim. Twelve is enough that the polygon
    /// reads as a circle to a 0.25 m carton and few enough to be free.</summary>
    private const int CollarSegments = 12;

    private const float CollarRun = 0.10f;
    private const float CollarRise = 0.05f;
    private const float CollarThickness = 0.04f;

    /// <summary>
    /// A lead-in collar around the rim, sloping from below deck level up to it.
    ///
    /// This is <see cref="ConveyorBelt.AddTransferRamps"/>'s fix, in the round.
    /// A rigid body resting on a deck settles a centimetre or two *inside* it —
    /// the solver's contact slop — so a carton arriving from a belt meets the
    /// vertical rim of the deck head-on and wedges against it. The wedge
    /// removes the face: a carton arriving low rides up it, and one at the
    /// correct height never touches it.
    ///
    /// It is static, not part of the deck, so it does not turn with the load —
    /// a rotating ramp under a carton is a carton being kicked.
    ///
    /// Collision only: <see cref="Editor.PartBounds"/> measures meshes, so the
    /// collar does not grow the part's selection box.
    /// </summary>
    private void AddTransferCollar()
    {
        var collar = new StaticBody3D { Name = "TransferCollar" };
        AddChild(collar);

        float theta = Mathf.Atan2(CollarRise, CollarRun);
        float halfLength = CollarRun / 2.0f;
        float halfThickness = CollarThickness / 2.0f;
        float dr = halfLength * Mathf.Cos(theta) - halfThickness * Mathf.Sin(theta);
        float dy = halfLength * Mathf.Sin(theta) + halfThickness * Mathf.Cos(theta);

        // Chord width of one segment, plus a little so neighbours overlap
        // rather than leaving a slot a carton corner can drop into.
        float segmentWidth = 2.0f * Mathf.Pi * (DeckRadius + dr) / CollarSegments * 1.25f;

        for (int i = 0; i < CollarSegments; i++)
        {
            float bearing = Mathf.Tau * i / CollarSegments;
            var wedge = new CollisionShape3D
            {
                Name = $"CollarWedge{i}",
                Shape = new BoxShape3D { Size = new Vector3(CollarRun, CollarThickness, segmentWidth) },
            };
            // Build the wedge on +X first (tilt about Z, exactly as the belt's
            // ramp does), then swing the whole thing round to its bearing.
            var tilt = new Basis(Vector3.Back, -theta);
            var swing = new Basis(Vector3.Up, bearing);
            wedge.Basis = swing * tilt;
            wedge.Position = swing * new Vector3(DeckRadius + dr, DeckThickness / 2.0f - dy, 0);
            collar.AddChild(wedge);
        }
    }

    private void BuildFaultLamp()
    {
        _faultLampMat = new StandardMaterial3D
        {
            AlbedoColor = new Color(0.30f, 0.06f, 0.06f),
            Metallic = 0.10f,
            Roughness = 0.35f,
        };
        // Off the deck, on the fixed base, or it would ride round with the load.
        var mount = new Vector3(0, -DeckThickness / 2.0f, DeckRadius + 0.12f);
        const float stalk = 0.26f;

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

    /// <summary>Index toward the outfeed angle, or back home. One tick.</summary>
    public void UpdateIndex(bool index, float delta)
    {
        if (IsFaulted) return;

        float target = index ? IndexAngle : 0.0f;
        _angle = Mathf.MoveToward(_angle, target, IndexSpeed * delta);
        ApplyAngle();
    }

    /// <summary>Square it back up to the infeed, for a scene reset.</summary>
    public void ResetDeck()
    {
        _angle = 0.0f;
        ApplyAngle();
    }

    private void ApplyAngle()
    {
        if (_deck is null) return;
        _deck.Rotation = new Vector3(0, Mathf.DegToRad(_angle), 0);
    }

    // ---------- IPart (HP-34)

    public void DeclareTags(PartTagBuilder tags) => tags
        .Bit("index", $"Turntable {tags.Index} (Index)", TagKind.Output)
        .Bit("athome", $"Turntable {tags.Index} (At Home)", TagKind.Input, initial: true)
        .Bit("atindex", $"Turntable {tags.Index} (At Index)", TagKind.Input)
        .Bit("fault", $"Turntable {tags.Index} Drive Fault", TagKind.Input);

    public void CaptureSettings(PartSettings settings)
    {
        settings.Put("deck_radius", DeckRadius);
        settings.Put("index_angle", IndexAngle);
        settings.Put("index_speed", IndexSpeed);
    }

    public void ApplySettings(PartSettings settings)
    {
        if (settings.Number("deck_radius") is { } radius) DeckRadius = radius;
        if (settings.Number("index_angle") is { } angle) IndexAngle = angle;
        if (settings.Number("index_speed") is { } speed) IndexSpeed = speed;
    }

    public void StepPart(PartTick tick)
    {
        if (!tick.TryBit("index", out bool index)) return;

        if (tick.TryBit("fault", out bool faulted)) SetFaulted(faulted);
        UpdateIndex(index, tick.Dt);
        tick.Write("athome", IsHome);
        tick.Write("atindex", IsAtIndex);
    }

    /// <summary>Deck radius is build-time geometry, so it is deliberately not
    /// offered here rather than offered and silently ignored.</summary>
    public void DescribeControls(IPartInspector ui)
    {
        ui.Slider("Index Angle (deg)", IndexAngle, 15.0f, 180.0f, 5.0f,
                  value => IndexAngle = value);
        ui.Slider("Index Speed (deg/s)", IndexSpeed, 10.0f, 300.0f, 5.0f,
                  value => IndexSpeed = value);
    }

    public void ResetPart(PartReset reset)
    {
        ResetDeck();
        reset.Write("athome", true);
        reset.Write("atindex", false);
    }

    public PartOperation? Operation => new("turntable", "index");

    public void Operate(PartOperate op) => op.ToggleBit("index");
}
