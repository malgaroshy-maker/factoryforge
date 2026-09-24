using FactoryForge.TagBus;
using Godot;

namespace FactoryForge.Parts;

/// <summary>
/// Inclined gravity chute that carries diverted boxes from the belt edge down to
/// floor level.
///
/// The ramp is anchored by its **high lip**, not by its centre. The lip is the
/// only part whose position actually matters — it has to sit just below the belt
/// surface and just outside the belt edge, or a box shoved off the belt hits a
/// raised edge instead of sliding onto the ramp. Tilting a centre-anchored slab
/// lifts the lip by half the length times sin(incline), which at 25° over 0.7 m
/// put it 12 cm *above* the belt.
///
/// And "the lip" means the top edge a carton actually meets, not the slab's
/// centre line (IP-40). Anchoring the centre line 15 mm down left the top
/// surface half a thickness above it — t/2·cos(incline), 17 mm for a 0.04 m
/// slab at 30° — so the edge stood 2 mm *proud* of the belt. A pusher's shove
/// climbs 2 mm; a carton that only belt friction moves, turned off the lane by
/// a pivot diverter, stopped dead against it. <see cref="LipDrop"/> is now
/// measured to the top surface, so the default lip really is 15 mm down.
///
/// The incline and the surface friction are a matched pair: a box only slides
/// when tan(incline) &gt; µ. At the original 12° with no physics material at all,
/// the ramp inherited Godot's default µ = 1.0 and boxes simply parked on it.
///
/// The tilt lives on the child shapes, never on the part's own transform: the
/// node itself stays square on its grid point, so saving and reloading a scene
/// round-trips instead of tilting the ramp another notch every time.
/// </summary>
public partial class Chute : StaticBody3D, IPart
{
    public const float DefaultRampLength = 0.80f;
    public const float DefaultThickness = 0.04f;
    public const float DefaultInclineDegrees = 30.0f;
    public const float DefaultLipSetback = 0.24f;
    public const float DefaultLipDrop = 0.015f;

    [Export] public float RampLength { get; set; } = DefaultRampLength;
    [Export] public float RampWidth { get; set; } = 0.50f;
    [Export] public float RampThickness { get; set; } = DefaultThickness;

    /// <summary>
    /// 30° clears the friction cone under any of the ways a physics engine can
    /// combine two materials' friction — including the pessimistic max(µ) — so
    /// the box always slides instead of parking halfway down. It is also simply
    /// what a gravity discharge chute looks like; they run 30–45°.
    /// </summary>
    [Export] public float InclineAngleDegrees { get; set; } = DefaultInclineDegrees;

    /// <summary>Polished steel chute against cardboard.</summary>
    [Export] public float SurfaceFriction { get; set; } = 0.15f;

    /// <summary>Distance from the part's grid point back towards the belt, to
    /// where the high lip sits. One cell minus the belt half-width, less a
    /// centimetre of clearance, puts the lip just off the belt edge.</summary>
    [Export] public float LipSetback { get; set; } = DefaultLipSetback;

    /// <summary>How far the lip — the ramp's top surface at its high edge —
    /// sits below the belt's carrying surface, so a box steps down onto the
    /// ramp rather than catching on it. Until IP-40 this was measured to the
    /// slab's centre line, which put the edge above the belt.</summary>
    [Export] public float LipDrop { get; set; } = DefaultLipDrop;

    public static float InclineRadians => Mathf.DegToRad(DefaultInclineDegrees);

    /// <summary>Unit normal of the deck, for standing something on it.</summary>
    public static Vector3 SurfaceNormal =>
        new(0, Mathf.Cos(InclineRadians), Mathf.Sin(InclineRadians));

    /// <summary>
    /// A point on the deck surface <paramref name="distance"/> metres down the
    /// ramp, relative to the part origin.
    ///
    /// This exists so the deterministic scene's view can slide its boxes down
    /// the *real* ramp instead of an assumed one. The old view hardcoded a
    /// 0.21 drop per metre — tan(12°), the incline this chute used to have — so
    /// once the ramp changed, boxes glided through the air above it.
    ///
    /// Describes a chute left at its default settings, which is what the default
    /// scene places.
    /// </summary>
    public static Vector3 SurfaceOffset(float distance)
    {
        // The lip is on the surface (IP-40), so walking down the slope from it
        // stays on the surface: there is no half-thickness to add.
        var downSlope = new Vector3(0, -Mathf.Sin(InclineRadians), Mathf.Cos(InclineRadians));
        return LipOffset(DefaultLipDrop, DefaultLipSetback) + downSlope * distance;
    }

    /// <summary>The high edge of the ramp's top surface, relative to the part
    /// origin: <paramref name="lipDrop"/> below the belt surface and
    /// <paramref name="lipSetback"/> back towards the belt.</summary>
    public static Vector3 LipOffset(float lipDrop, float lipSetback) =>
        new(0, PartLayout.BeltSurface - lipDrop, -lipSetback);

    private const string DeckShapeName = "DeckShape";

    /// <summary>
    /// Where the top surface's high edge actually is, read off the deck's
    /// collision shape rather than worked out from the settings — the settings
    /// agreed with themselves the whole time the edge stood proud. Relative to
    /// the part origin; NaN before the geometry is built. For the tests
    /// (IP-40).
    /// </summary>
    public Vector3 DeckLipFromShape()
    {
        if (GetNodeOrNull<CollisionShape3D>(DeckShapeName) is { Shape: BoxShape3D box } shape)
            return shape.Transform * new Vector3(0, box.Size.Y / 2.0f, -box.Size.Z / 2.0f);
        return new Vector3(float.NaN, float.NaN, float.NaN);
    }

    public override void _Ready() => BuildGeometry();

    /// <summary>Re-create the deck for the current incline and friction, both of
    /// which are baked in at construction.</summary>
    public void Rebuild()
    {
        foreach (var child in GetChildren())
        {
            RemoveChild(child);
            child.QueueFree();
        }
        BuildGeometry();
    }

    private void BuildGeometry()
    {
        PhysicsMaterialOverride = new PhysicsMaterial
        {
            Friction = SurfaceFriction,
            Bounce = 0.0f,
        };

        var basis = Basis.Identity.Rotated(Vector3.Right, Mathf.DegToRad(InclineAngleDegrees));

        // Anchor at the lip -- the top surface's high edge -- then go half the
        // thickness into the slab and half the ramp down the slope to find its
        // centre. Rotating the basis first keeps both steps right for any
        // incline. Leaving out the first step was IP-40: the centre line sat at
        // the lip's height and the surface stood proud of the belt.
        var lip = LipOffset(LipDrop, LipSetback);
        var centre = lip + basis * new Vector3(0, -RampThickness / 2.0f, RampLength / 2.0f);
        var ramp = new Transform3D(basis, centre);

        var chuteMat = new StandardMaterial3D
        {
            AlbedoColor = new Color(0.48f, 0.45f, 0.42f),
            Roughness = 0.55f,
            Metallic = 0.35f,
        };

        var deckSize = new Vector3(RampWidth, RampThickness, RampLength);
        AddChild(new MeshInstance3D
        {
            Mesh = new BoxMesh { Size = deckSize },
            MaterialOverride = chuteMat,
            Transform = ramp,
        });
        AddChild(new CollisionShape3D
        {
            Name = DeckShapeName,
            Shape = new BoxShape3D { Size = deckSize },
            Transform = ramp,
        });

        // Side rails, so a box that arrives skewed is guided rather than lost.
        var railSize = new Vector3(0.03f, 0.09f, RampLength);
        foreach (int side in new[] { -1, 1 })
        {
            var railXform = ramp.TranslatedLocal(
                new Vector3(side * (RampWidth / 2 - 0.015f), 0.065f, 0));

            AddChild(new MeshInstance3D
            {
                Mesh = new BoxMesh { Size = railSize },
                MaterialOverride = chuteMat,
                Transform = railXform,
            });
            AddChild(new CollisionShape3D
            {
                Shape = new BoxShape3D { Size = railSize },
                Transform = railXform,
            });
        }

        // Support leg under the low end, so the ramp is a machine and not a
        // slab hanging in the air.
        var footY = (ramp * new Vector3(0, 0, RampLength / 2.0f)).Y;
        float legHeight = Mathf.Max(footY + PartLayout.FloorDrop - 0.02f, 0.05f);
        AddChild(new MeshInstance3D
        {
            Mesh = new BoxMesh { Size = new Vector3(0.06f, legHeight, 0.06f) },
            MaterialOverride = chuteMat,
            Position = new Vector3(0, footY - legHeight / 2.0f,
                                   (ramp * new Vector3(0, 0, RampLength / 2.0f)).Z),
        });
    }

    // ---------- IPart (HP-34)

    /// <summary>Nothing. A chute is gravity and a surface; it has no I/O at
    /// all, and the palette says so.</summary>
    public void DeclareTags(PartTagBuilder tags) { }

    public void CaptureSettings(PartSettings settings)
    {
        settings.Put("incline", InclineAngleDegrees);
        settings.Put("friction", SurfaceFriction);
        settings.Put("ramp_length", RampLength);
        // Width and thickness are the deck itself; the lip is where a carton
        // leaves the belt, and it is the setting people reach for when cartons
        // catch on the transfer -- exactly the tuning a reload should not undo.
        settings.Put("ramp_width", RampWidth);
        settings.Put("ramp_thickness", RampThickness);
        settings.Put("lip_setback", LipSetback);
        settings.Put("lip_drop", LipDrop);
    }

    public void ApplySettings(PartSettings settings)
    {
        if (settings.Number("incline") is { } incline) InclineAngleDegrees = incline;
        if (settings.Number("friction") is { } friction) SurfaceFriction = friction;
        if (settings.Number("ramp_length") is { } length) RampLength = length;
        if (settings.Number("ramp_width") is { } width) RampWidth = width;
        if (settings.Number("ramp_thickness") is { } thickness) RampThickness = thickness;
        if (settings.Number("lip_setback") is { } setback) LipSetback = setback;
        if (settings.Number("lip_drop") is { } drop) LipDrop = drop;
    }

    public void DescribeControls(IPartInspector ui)
    {
        ui.Slider("Incline (deg)", InclineAngleDegrees, 5.0f, 55.0f, 1.0f,
                  value => { InclineAngleDegrees = value; Rebuild(); });
        ui.Slider("Surface Friction", SurfaceFriction, 0.02f, 1.0f, 0.02f,
                  value => { SurfaceFriction = value; Rebuild(); });
    }
}
