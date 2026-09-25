using Godot;

namespace FactoryForge.Parts;

/// <summary>
/// A raised floor: the deck that parts on a level above the ground stand on
/// (IP-15).
///
/// <b>Placed on the level it holds up.</b> Like every part, its origin sits on
/// its level's work plane; its deck's top surface is <see cref="PartLayout.FloorDrop"/>
/// below that, which is exactly that level's floor. So a belt, a sensor or a
/// panel placed on the same level lands with its legs on this deck without
/// knowing it is on a mezzanine at all -- the reason levels needed no change to
/// any other part. Its own legs reach from the deck down to the ground,
/// however high that is, and follow it if it is moved to another level.
///
/// <b>One deck under many machines, not a platform per part.</b> The other way
/// to give a raised part something to stand on is for every part to draw its
/// own stilts when it finds itself above level 0. That was rejected: a raised
/// line of four belts and three sensors would stand on seven overlapping
/// platforms, each z-fighting with its neighbour, and a carton that fell off
/// one belt would fall through the gaps between them. A real mezzanine is a
/// structure in its own right -- sized to the equipment, placed first, walked
/// on -- and so is this: a scene file says where it is and how big.
///
/// <b>It is solid.</b> The deck is a collider, so a carton that leaves a raised
/// belt anywhere but where it should lands on the mezzanine, as it would in a
/// building, rather than falling through the floor of the level above.
///
/// No I/O at all: a static structure, with nothing for a controller to drive
/// and nothing to report -- the same standing as <see cref="Chute"/>.
/// </summary>
public partial class Mezzanine : StaticBody3D, IPart
{
    /// <summary>Deck length along the part's X, metres.</summary>
    [Export] public float DeckLengthX { get; set; } = 2.0f;

    /// <summary>Deck width along the part's Z, metres.</summary>
    [Export] public float DeckWidthZ { get; set; } = 1.5f;

    /// <summary>Checker plate, steel on board-backed steel: thick enough to be
    /// a floor in the render, thin enough that the level below keeps its
    /// headroom.</summary>
    public const float DeckThickness = 0.06f;

    private const float PostSize = 0.10f;
    private const float PostInset = 0.10f;
    private const float MaxPostSpacing = 1.6f;
    private const float BeamDepth = 0.10f;

    private const float MinSize = 0.5f;
    private const float MaxSize = 8.0f;

    /// <summary>Deck top, relative to the part origin: the level's floor.</summary>
    public static float DeckTopLocal => -PartLayout.FloorDrop;

    private Node3D? _legs;
    private float _legsBuiltForY = float.NaN;
    private readonly System.Collections.Generic.List<Node> _built = new();

    private T Add<T>(T node) where T : Node
    {
        AddChild(node);
        _built.Add(node);
        return node;
    }

    private static StandardMaterial3D FrameMaterial => new()
    {
        AlbedoColor = new Color(0.26f, 0.29f, 0.33f),
        Metallic = 0.5f,
        Roughness = 0.5f,
    };

    public override void _Ready()
    {
        Build();
        // The legs reach the ground, so they depend on how high the deck is,
        // and a drag or a level change moves it. Told about it rather than
        // polling every frame.
        SetNotifyTransform(true);
    }

    public override void _Notification(int what)
    {
        if (what == NotificationTransformChanged) BuildLegs();
    }

    /// <summary>Rebuild everything for the current size.</summary>
    public void Rebuild()
    {
        // Only what Build made: the editor hangs things on a part too (its
        // name label), and clearing every child would take those with it.
        // Collision shapes have to be direct children of the body, so the
        // geometry cannot simply live under one container node.
        foreach (var child in _built)
        {
            if (!IsInstanceValid(child)) continue;
            RemoveChild(child);
            child.QueueFree();
        }
        _built.Clear();
        if (_legs is not null && IsInstanceValid(_legs))
        {
            RemoveChild(_legs);
            _legs.QueueFree();
        }
        _legs = null;
        _legsBuiltForY = float.NaN;
        Build();
    }

    private void Build()
    {
        PhysicsMaterialOverride = new PhysicsMaterial { Friction = 0.5f, Bounce = 0.0f };

        var deckSize = new Vector3(DeckLengthX, DeckThickness, DeckWidthZ);
        var deckCentre = new Vector3(0, DeckTopLocal - DeckThickness / 2.0f, 0);

        Add(new MeshInstance3D
        {
            Name = "DeckPlate",
            Mesh = new BoxMesh { Size = deckSize },
            MaterialOverride = new StandardMaterial3D
            {
                AlbedoColor = new Color(0.52f, 0.54f, 0.56f),
                Metallic = 0.6f,
                Roughness = 0.42f,
            },
            Position = deckCentre,
        });
        Add(new CollisionShape3D
        {
            Name = "DeckShape",
            Shape = new BoxShape3D { Size = deckSize },
            Position = deckCentre,
        });

        // Safety-yellow nosing along every edge: the one thing on a real
        // mezzanine that says "the floor stops here", and the only cue in a
        // render that the grey slab is at a height worth minding.
        var nosing = new StandardMaterial3D
        {
            AlbedoColor = new Color(0.95f, 0.75f, 0.10f),
            Roughness = 0.5f,
        };
        foreach (int side in new[] { -1, 1 })
        {
            Add(new MeshInstance3D
            {
                Name = side < 0 ? "NosingNear" : "NosingFar",
                Mesh = new BoxMesh { Size = new Vector3(DeckLengthX, 0.004f, 0.05f) },
                MaterialOverride = nosing,
                Position = new Vector3(0, DeckTopLocal + 0.002f, side * (DeckWidthZ / 2.0f - 0.025f)),
            });
            Add(new MeshInstance3D
            {
                Name = side < 0 ? "NosingTail" : "NosingHead",
                Mesh = new BoxMesh { Size = new Vector3(0.05f, 0.004f, DeckWidthZ) },
                MaterialOverride = nosing,
                Position = new Vector3(side * (DeckLengthX / 2.0f - 0.025f), DeckTopLocal + 0.002f, 0),
            });

            // The edge beams the deck sits on.
            Add(new MeshInstance3D
            {
                Name = side < 0 ? "BeamNear" : "BeamFar",
                Mesh = new BoxMesh { Size = new Vector3(DeckLengthX, BeamDepth, 0.08f) },
                MaterialOverride = FrameMaterial,
                Position = new Vector3(0, DeckTopLocal - DeckThickness - BeamDepth / 2.0f,
                                       side * (DeckWidthZ / 2.0f - 0.04f)),
            });
        }

        BuildLegs();
    }

    /// <summary>
    /// Posts from under the edge beams to the ground, at the corners and no
    /// more than <see cref="MaxPostSpacing"/> apart. Sized from where the deck
    /// actually is, which is why they rebuild when it moves; a deck on level 0
    /// is a floor plate lying on the ground and has none.
    /// </summary>
    private void BuildLegs()
    {
        if (!IsInsideTree()) return;

        float originY = GlobalPosition.Y;
        if (Mathf.IsEqualApprox(originY, _legsBuiltForY)) return;
        _legsBuiltForY = originY;

        if (_legs is not null)
        {
            RemoveChild(_legs);
            _legs.QueueFree();
            _legs = null;
        }

        float underside = DeckTopLocal - DeckThickness - BeamDepth;
        float height = originY + underside;          // world height of the beam's underside
        if (height < 0.02f) return;

        _legs = new Node3D { Name = "Legs" };
        AddChild(_legs);

        var mat = FrameMaterial;
        float spanX = DeckLengthX - 2.0f * PostInset;
        int bays = Mathf.Max(1, Mathf.CeilToInt(spanX / MaxPostSpacing));
        foreach (int side in new[] { -1, 1 })
        {
            for (int i = 0; i <= bays; i++)
            {
                float x = -spanX / 2.0f + spanX * i / bays;
                _legs.AddChild(new MeshInstance3D
                {
                    Name = $"Post{(side < 0 ? "N" : "F")}{i}",
                    Mesh = new BoxMesh { Size = new Vector3(PostSize, height, PostSize) },
                    MaterialOverride = mat,
                    Position = new Vector3(x, underside - height / 2.0f,
                                           side * (DeckWidthZ / 2.0f - PostInset)),
                });
            }
        }
    }

    // ---------- IPart

    public void DeclareTags(PartTagBuilder tags) { }

    public void CaptureSettings(PartSettings settings)
    {
        settings.Put("size_x", DeckLengthX);
        settings.Put("size_z", DeckWidthZ);
    }

    public void ApplySettings(PartSettings settings)
    {
        if (settings.Number("size_x") is { } x) DeckLengthX = Mathf.Clamp(x, MinSize, MaxSize);
        if (settings.Number("size_z") is { } z) DeckWidthZ = Mathf.Clamp(z, MinSize, MaxSize);
    }

    /// <summary>Both sizes rebuild the deck: they are geometry, and a slider
    /// that moved and left the deck as it was would be a UI that configures
    /// nothing (gotcha 11).</summary>
    public void DescribeControls(IPartInspector ui)
    {
        ui.Slider("Deck Length (m)", DeckLengthX, MinSize, MaxSize, 0.1f, value =>
        {
            DeckLengthX = value;
            Rebuild();
        });
        ui.Slider("Deck Width (m)", DeckWidthZ, MinSize, MaxSize, 0.1f, value =>
        {
            DeckWidthZ = value;
            Rebuild();
        });
    }
}
