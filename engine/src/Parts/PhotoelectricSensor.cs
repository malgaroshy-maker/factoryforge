using FactoryForge.TagBus;
using Godot;

namespace FactoryForge.Parts;

/// <summary>How a sensor decides what counts as a detection.</summary>
public enum SensingMode
{
    /// <summary>Bounces light off the item itself. Cheapest, shortest range.</summary>
    Diffuse,

    /// <summary>Beams to a reflector on the far side and watches for the return
    /// to break. Sees anything opaque, including matt and dark items a diffuse
    /// sensor would miss, so it gets a reflector post.</summary>
    Retroreflective,

    /// <summary>Responds to metal only. Cardboard passes straight through it,
    /// which is what makes it a material sorter rather than a presence sensor.</summary>
    Inductive,
}

/// <summary>
/// Photoelectric / proximity sensor. A RayCast3D across the lane decides
/// presence; <see cref="Mode"/> decides what the sensor is willing to see.
/// </summary>
public partial class PhotoelectricSensor : Node3D, IPart
{
    /// <summary>Beam height above the carrying surface. This is the property that
    /// decides what the sensor can see, so it is measured from the belt top, not
    /// from the part origin.</summary>
    [Export] public float HeightAboveBelt { get; set; } = 0.20f;
    [Export] public float Range { get; set; } = 0.60f;
    [Export] public string SensorName { get; set; } = "Sensor";

    /// <summary>What this sensor responds to. Changing it re-colours the head
    /// and adds or removes the reflector, so the three read differently in the
    /// scene rather than being one part with three names.</summary>
    [Export] public SensingMode Mode { get; set; } = SensingMode.Diffuse;

    /// <summary>
    /// True when this part is only a *view* of a sensor the simulation already
    /// owns. Its raycast then decides nothing — the beam is lit from the tag
    /// instead, so the simulated detection is not clobbered by a raycast that
    /// has no physics box to hit.
    /// </summary>
    [Export] public bool VisualOnly { get; set; }

    /// <summary>Head colour per mode, so the three are distinguishable at a
    /// glance: industrial yellow for diffuse, red for retroreflective, steel
    /// blue for inductive.</summary>
    private Color HeadTint => Mode switch
    {
        SensingMode.Retroreflective => new Color(0.85f, 0.25f, 0.20f),
        SensingMode.Inductive => new Color(0.30f, 0.55f, 0.80f),
        _ => new Color(0.95f, 0.75f, 0.10f),
    };

    /// <summary>Beam height relative to the part origin — the origin is on the
    /// work plane, the beam is measured from the belt surface above it.</summary>
    private float BeamY => PartLayout.BeltSurface + HeightAboveBelt;

    private RayCast3D _rayCast = null!;
    private MeshInstance3D _beamMesh = null!;
    private StandardMaterial3D _beamMaterial = null!;

    private static readonly Color BeamOff = new(0.35f, 0.10f, 0.10f);
    private static readonly Color BeamOn = new(1.0f, 0.15f, 0.15f);

    private StandardMaterial3D? _ledMaterial;

    public bool IsDetected
    {
        get
        {
            if (!_rayCast.IsColliding()) return false;
            // An inductive sensor ignores everything that is not metal, so a
            // cardboard carton passes it as if the lane were empty.
            if (Mode != SensingMode.Inductive) return true;
            return _rayCast.GetCollider() is Node node && FindItem(node) is { IsMetal: true };
        }
    }

    private static BoxPhysics? FindItem(Node node)
    {
        for (Node? n = node; n is not null; n = n.GetParent())
        {
            if (n is BoxPhysics box) return box;
        }
        return null;
    }

    public override void _Ready() => BuildGeometry();

    /// <summary>
    /// Re-create the beam, post and raycast for the current Range and mounting
    /// height. Both are built once from the exported values, so without this a
    /// change in the inspector moved nothing — the sensor kept the reach and
    /// height it was constructed with.
    /// </summary>
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
        var visual = IndustrialMeshBuilder.BuildDetailedSensor(Range, BeamY, PartLayout.FloorDrop,
                                                              HeadTint, Mode == SensingMode.Retroreflective);
        AddChild(visual);

        _rayCast = new RayCast3D
        {
            TargetPosition = new Vector3(0, 0, -Range),
            Position = new Vector3(0, BeamY, 0),
            Enabled = true,
            CollideWithBodies = true,
            CollideWithAreas = false,
        };
        AddChild(_rayCast);

        _beamMaterial = new StandardMaterial3D
        {
            AlbedoColor = BeamOff,
            EmissionEnabled = true,
            Emission = BeamOff,
            EmissionEnergyMultiplier = 0.4f,
        };

        _beamMesh = new MeshInstance3D
        {
            Name = "BeamVisual",
            Mesh = new CylinderMesh
            {
                TopRadius = 0.012f,
                BottomRadius = 0.012f,
                Height = Range,
            },
            Position = new Vector3(0, BeamY, -Range / 2.0f),
            MaterialOverride = _beamMaterial,
        };
        _beamMesh.RotateX(Mathf.Pi / 2);
        AddChild(_beamMesh);

        // Output-state LED on the head (CP-13). Every real photo-eye has one,
        // and it is what you look at first when a line stops: it tells you
        // whether the sensor sees anything at all, separately from whether the
        // controller is reading it. Here it does the same job for the beam,
        // which is only visible from angles where nothing is standing in it.
        _ledMaterial = new StandardMaterial3D
        {
            AlbedoColor = new Color(0.18f, 0.28f, 0.18f),
            EmissionEnabled = true,
            Emission = new Color(0.20f, 1.0f, 0.35f),
            EmissionEnergyMultiplier = 0.0f,
        };
        AddChild(new MeshInstance3D
        {
            Name = "StatusLed",
            Mesh = new SphereMesh { Radius = 0.013f, Height = 0.026f },
            MaterialOverride = _ledMaterial,
            Position = new Vector3(0.031f, BeamY + 0.03f, 0.04f),
        });
    }

    public override void _Process(double delta)
    {
        if (VisualOnly) return;   // beam is driven from the tag by SceneEditor
        SetBeamActive(IsDetected);
    }

    public void SetBeamActive(bool detected)
    {
        _beamMaterial.AlbedoColor = detected ? BeamOn : BeamOff;
        _beamMaterial.Emission = detected ? BeamOn : BeamOff;
        _beamMaterial.EmissionEnergyMultiplier = detected ? 3.0f : 0.4f;

        if (_ledMaterial is null) return;
        _ledMaterial.AlbedoColor = detected ? new Color(0.35f, 1.0f, 0.45f) : new Color(0.18f, 0.28f, 0.18f);
        _ledMaterial.EmissionEnergyMultiplier = detected ? 3.5f : 0.0f;
    }

    // ---------- IPart (HP-34)

    /// <summary>One declaration for all three catalogue types. A
    /// retroreflective and an inductive sensor are this part with a different
    /// <see cref="Mode"/>, and they expose the same one contact.</summary>
    public void DeclareTags(PartTagBuilder tags) =>
        tags.Bit("detect", $"Sensor {tags.Index} (Detect)", TagKind.Input);

    public void CaptureSettings(PartSettings settings)
    {
        settings.Put("range", Range);
        settings.Put("height", HeightAboveBelt);
        settings.PutEnum("mode", Mode);
        settings.Put("visual_only", VisualOnly);
    }

    public void ApplySettings(PartSettings settings)
    {
        if (settings.Number("range") is { } range) Range = range;
        if (settings.Number("height") is { } height) HeightAboveBelt = height;
        if (settings.Enumeration<SensingMode>("mode") is { } mode) Mode = mode;
        if (settings.Flag("visual_only") is { } visualOnly) VisualOnly = visualOnly;
    }

    public void StepPart(PartTick tick)
    {
        if (!tick.Has("detect")) return;

        // A VisualOnly sensor is a *view* of a tag the simulation owns: it
        // follows the tag rather than publishing it, so the two can never be
        // two authorities for one bit.
        if (VisualOnly)
        {
            if (tick.TryBit("detect", out bool detected)) SetBeamActive(detected);
        }
        else
        {
            tick.Write("detect", IsDetected);
        }
    }

    public void DescribeControls(IPartInspector ui)
    {
        ui.Slider("Beam Range (m)", Range, 0.1f, 2.0f, 0.05f,
                  value => { Range = value; Rebuild(); });
        ui.Slider("Beam Height (m)", HeightAboveBelt, 0.01f, 0.6f, 0.01f,
                  value => { HeightAboveBelt = value; Rebuild(); });
    }
}
