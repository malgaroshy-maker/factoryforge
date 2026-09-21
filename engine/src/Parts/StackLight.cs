using FactoryForge.TagBus;
using Godot;

namespace FactoryForge.Parts;

/// <summary>
/// Industrial 3-stage stack light post with dynamic indicator lamp emission glow.
/// </summary>
public partial class StackLight : Node3D, IPart
{
    private MeshInstance3D _greenLampMesh = null!;
    private MeshInstance3D _yellowLampMesh = null!;
    private MeshInstance3D _redLampMesh = null!;

    private StandardMaterial3D _greenMat = null!;
    private StandardMaterial3D _yellowMat = null!;
    private StandardMaterial3D _redMat = null!;

    // One real light per lamp (CP-12). A tower whose lamps only changed their
    // own albedo lit nothing around them, so at a glance an amber tower and a
    // dark one differed by a few pixels. A stack light exists to be noticed
    // from across a building; the light it throws is most of how that works.
    private OmniLight3D _greenLight = null!;
    private OmniLight3D _yellowLight = null!;
    private OmniLight3D _redLight = null!;

    public override void _Ready()
    {
        // Geometry is authored from the floor up; the part origin is on the work
        // plane like every other part, so the whole tower drops to stand on it.
        var column = new Node3D { Name = "Tower", Position = new Vector3(0, -PartLayout.FloorDrop, 0) };
        AddChild(column);

        var postMat = new StandardMaterial3D
        {
            AlbedoColor = new Color(0.25f, 0.25f, 0.28f),
            Metallic = 0.8f,
            Roughness = 0.3f,
        };

        // Vertical post shaft
        var post = new MeshInstance3D
        {
            Mesh = new CylinderMesh { TopRadius = 0.025f, BottomRadius = 0.025f, Height = 0.70f },
            Position = new Vector3(0, 0.35f, 0),
            MaterialOverride = postMat,
        };
        column.AddChild(post);

        // Lamps stack red-amber-green from the top, the industrial convention.
        // Green lamp dome (bottom)
        _greenMat = CreateLampMaterial(new Color(0.1f, 0.4f, 0.1f));
        _greenLampMesh = new MeshInstance3D
        {
            Mesh = new CylinderMesh
            {
                TopRadius = PartLayout.StackLightDiameter / 2,
                BottomRadius = PartLayout.StackLightDiameter / 2,
                Height = 0.08f,
            },
            Position = new Vector3(0, 0.45f, 0),
            MaterialOverride = _greenMat,
        };
        column.AddChild(_greenLampMesh);
        _greenLight = AddLampLight(column, 0.45f, new Color(0.25f, 1.0f, 0.35f));

        // Yellow lamp dome (middle)
        _yellowMat = CreateLampMaterial(new Color(0.4f, 0.35f, 0.1f));
        _yellowLampMesh = new MeshInstance3D
        {
            Mesh = new CylinderMesh
            {
                TopRadius = PartLayout.StackLightDiameter / 2,
                BottomRadius = PartLayout.StackLightDiameter / 2,
                Height = 0.08f,
            },
            Position = new Vector3(0, 0.55f, 0),
            MaterialOverride = _yellowMat,
        };
        column.AddChild(_yellowLampMesh);
        _yellowLight = AddLampLight(column, 0.55f, new Color(1.0f, 0.85f, 0.25f));

        // Red lamp dome (top)
        _redMat = CreateLampMaterial(new Color(0.4f, 0.1f, 0.1f));
        _redLampMesh = new MeshInstance3D
        {
            Mesh = new CylinderMesh
            {
                TopRadius = PartLayout.StackLightDiameter / 2,
                BottomRadius = PartLayout.StackLightDiameter / 2,
                Height = 0.08f,
            },
            Position = new Vector3(0, 0.65f, 0),
            MaterialOverride = _redMat,
        };
        column.AddChild(_redLampMesh);
        _redLight = AddLampLight(column, 0.65f, new Color(1.0f, 0.20f, 0.18f));

        // Sun cap on top, so the tower reads as a finished assembly rather
        // than as three discs on a stick.
        column.AddChild(new MeshInstance3D
        {
            Name = "TopCap",
            Mesh = new SphereMesh
            {
                Radius = PartLayout.StackLightDiameter / 2,
                Height = PartLayout.StackLightDiameter * 0.7f,
                IsHemisphere = true,
            },
            Position = new Vector3(0, 0.69f, 0),
            MaterialOverride = postMat,
        });
    }

    /// <summary>A short-range unshadowed light inside a lamp. Shadows are off
    /// deliberately: three shadow-casting lights per tower, on a scene that can
    /// hold several towers, buys nothing a viewer would notice and costs a
    /// shadow map each.</summary>
    private static OmniLight3D AddLampLight(Node3D parent, float y, Color colour)
    {
        var light = new OmniLight3D
        {
            LightColor = colour,
            LightEnergy = 0.0f,
            OmniRange = 0.9f,
            ShadowEnabled = false,
            Position = new Vector3(0, y, 0),
        };
        parent.AddChild(light);
        return light;
    }

    /// <summary>Local-space Y of each lamp's centre, matching the literals
    /// <c>_Ready</c> builds them at (column is offset by -FloorDrop, lamps sit
    /// at 0.45/0.55/0.65 within it) -- kept here rather than read back off the
    /// mesh instances so a hit test never has to ask a lamp where it is.</summary>
    private const float LampRadius = PartLayout.StackLightDiameter / 2 + 0.03f;

    /// <summary>
    /// Which lamp, if any, a click hits -- "green"/"yellow"/"red", matching
    /// the tag suffix that lamp drives. Tested as three spheres rather than the
    /// tower's bounding box, the same reasoning as <see cref="ButtonPanel.HitTest"/>:
    /// the box covers the whole post, not just one dome (UX-37).
    /// </summary>
    public string? HitTest(Vector3 worldOrigin, Vector3 worldDirection)
    {
        var toLocal = GlobalTransform.AffineInverse();
        Vector3 origin = toLocal * worldOrigin;
        Vector3 dir = (toLocal.Basis * worldDirection).Normalized();

        string? best = null;
        float nearest = float.MaxValue;

        foreach (var (name, y) in new (string Name, float Y)[]
                 { ("green", 0.45f), ("yellow", 0.55f), ("red", 0.65f) })
        {
            var centre = new Vector3(0, y - PartLayout.FloorDrop, 0);
            if (RayHit.Sphere(origin, dir, centre, LampRadius) is not { } t) continue;
            if (t >= nearest) continue;

            nearest = t;
            best = name;
        }

        return best;
    }

    private static StandardMaterial3D CreateLampMaterial(Color baseColor)
    {
        return new StandardMaterial3D
        {
            AlbedoColor = baseColor,
            EmissionEnabled = true,
            Emission = baseColor,
            EmissionEnergyMultiplier = 0.2f,
        };
    }

    public void SetGreenLamp(bool on)
    {
        if (_greenMat is null) return;
        Color color = on ? new Color(0.2f, 1.0f, 0.3f) : new Color(0.1f, 0.4f, 0.1f);
        _greenMat.AlbedoColor = color;
        _greenMat.Emission = color;
        _greenMat.EmissionEnergyMultiplier = on ? 3.0f : 0.2f;
        if (_greenLight is not null) _greenLight.LightEnergy = on ? 1.6f : 0.0f;
    }

    public void SetYellowLamp(bool on)
    {
        if (_yellowMat is null) return;
        Color color = on ? new Color(1.0f, 0.9f, 0.2f) : new Color(0.4f, 0.35f, 0.1f);
        _yellowMat.AlbedoColor = color;
        _yellowMat.Emission = color;
        _yellowMat.EmissionEnergyMultiplier = on ? 3.0f : 0.2f;
        if (_yellowLight is not null) _yellowLight.LightEnergy = on ? 1.6f : 0.0f;
    }

    public void SetRedLamp(bool on)
    {
        if (_redMat is null) return;
        Color color = on ? new Color(1.0f, 0.15f, 0.15f) : new Color(0.4f, 0.1f, 0.1f);
        _redMat.AlbedoColor = color;
        _redMat.Emission = color;
        _redMat.EmissionEnergyMultiplier = on ? 3.0f : 0.2f;
        if (_redLight is not null) _redLight.LightEnergy = on ? 1.6f : 0.0f;
    }

    // ---------- IPart (HP-34)

    public void DeclareTags(PartTagBuilder tags) => tags
        .Bit("green", $"StackLight {tags.Index} Green", TagKind.Output)
        .Bit("yellow", $"StackLight {tags.Index} Yellow", TagKind.Output)
        .Bit("red", $"StackLight {tags.Index} Red", TagKind.Output);

    public void StepPart(PartTick tick)
    {
        if (tick.TryBit("green", out bool green)) SetGreenLamp(green);
        if (tick.TryBit("yellow", out bool yellow)) SetYellowLamp(yellow);
        if (tick.TryBit("red", out bool red)) SetRedLamp(red);
    }

    /// <summary>Precise: each lamp is separately clickable, and a bounding box
    /// would cover the whole tower and fire the nearest one wherever you
    /// clicked.</summary>
    public PartOperation? Operation => new("stack light", Precise: true);

    public string? HitTestRegion(Vector3 from, Vector3 direction) => HitTest(from, direction);

    public void Operate(PartOperate op) => op.ToggleBit(op.Region);
}
