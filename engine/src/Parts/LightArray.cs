using FactoryForge.TagBus;
using Godot;

namespace FactoryForge.Parts;

/// <summary>
/// Light curtain: a vertical stack of beams across the lane. Instead of the
/// single bit a photoelectric sensor gives you, it reports how far up the
/// curtain the tallest blocked beam sits — a measurement, not a presence.
///
/// That is what lets one part replace the low/high sensor pair the sorting line
/// uses: rather than inferring height from two bits, the PLC reads the height
/// directly and can sort into as many classes as it likes.
/// </summary>
public partial class LightArray : Node3D, IPart
{
    /// <summary>Beams in the curtain. More beams, finer resolution.</summary>
    [Export] public int BeamCount { get; set; } = 12;

    /// <summary>Height of the curtain above the belt surface.</summary>
    [Export] public float CurtainHeight { get; set; } = 0.48f;

    /// <summary>Span across the lane.</summary>
    [Export] public float Range { get; set; } = 0.75f;

    private readonly System.Collections.Generic.List<RayCast3D> _beams = new();
    private readonly System.Collections.Generic.List<MeshInstance3D> _beamMeshes = new();
    private StandardMaterial3D _litMat = null!;
    private StandardMaterial3D _darkMat = null!;
    private Label3D _readout = null!;

    /// <summary>Height of the tallest blocked beam, in metres above the belt.
    /// Zero when the curtain is clear.</summary>
    public float MeasuredHeight { get; private set; }

    /// <summary>True while anything at all is in the curtain.</summary>
    public bool IsBlocked { get; private set; }

    public override void _Ready() => BuildGeometry();

    /// <summary>
    /// Re-create the posts, beams and readout for the current
    /// <see cref="CurtainHeight"/>, <see cref="BeamCount"/> and
    /// <see cref="Range"/>. All three are read only while building, so without
    /// this the inspector's sliders moved a number nothing ever looked at
    /// again — the curtain kept the height and resolution it was constructed
    /// with. Same shape as <see cref="PhotoelectricSensor.Rebuild"/> and
    /// <see cref="Chute.Rebuild"/>.
    ///
    /// The two lists must be cleared here, not just repopulated: _Process
    /// walks them every frame, and a stale entry is a freed node.
    /// </summary>
    public void Rebuild()
    {
        foreach (var child in GetChildren())
        {
            RemoveChild(child);
            child.QueueFree();
        }
        _beams.Clear();
        _beamMeshes.Clear();
        BuildGeometry();
    }

    private void BuildGeometry()
    {
        var steel = new StandardMaterial3D
        {
            AlbedoColor = new Color(0.62f, 0.64f, 0.68f),
            Metallic = 0.35f,
            Roughness = 0.50f,
        };
        _litMat = new StandardMaterial3D
        {
            AlbedoColor = new Color(1.0f, 0.20f, 0.18f),
            EmissionEnabled = true,
            Emission = new Color(1.0f, 0.20f, 0.18f),
            EmissionEnergyMultiplier = 2.4f,
        };
        _darkMat = new StandardMaterial3D
        {
            AlbedoColor = new Color(0.35f, 0.12f, 0.12f),
            EmissionEnabled = true,
            Emission = new Color(0.35f, 0.12f, 0.12f),
            EmissionEnergyMultiplier = 0.35f,
        };

        float baseY = PartLayout.BeltSurface;

        // Emitter column on this side, receiver column across the lane.
        foreach (float z in new[] { 0.0f, -Range })
        {
            AddChild(new MeshInstance3D
            {
                Mesh = new BoxMesh
                {
                    Size = new Vector3(0.05f, CurtainHeight + PartLayout.FloorDrop + 0.06f, 0.05f),
                },
                MaterialOverride = steel,
                Position = new Vector3(
                    0,
                    baseY + CurtainHeight / 2 - (PartLayout.FloorDrop + 0.06f) / 2 + 0.03f,
                    z),
            });
            AddChild(new MeshInstance3D
            {
                Mesh = new BoxMesh { Size = new Vector3(0.14f, 0.02f, 0.14f) },
                MaterialOverride = steel,
                Position = new Vector3(0, -PartLayout.FloorDrop, z),
            });
        }

        for (int i = 0; i < BeamCount; i++)
        {
            // Beams start just above the belt so the lowest item still breaks one.
            float y = baseY + 0.02f + (CurtainHeight - 0.02f) * i / Mathf.Max(BeamCount - 1, 1);

            var ray = new RayCast3D
            {
                TargetPosition = new Vector3(0, 0, -Range),
                Position = new Vector3(0, y, 0),
                Enabled = true,
                CollideWithBodies = true,
                CollideWithAreas = false,
            };
            AddChild(ray);
            _beams.Add(ray);

            var mesh = new MeshInstance3D
            {
                Mesh = new CylinderMesh { TopRadius = 0.006f, BottomRadius = 0.006f, Height = Range },
                Position = new Vector3(0, y, -Range / 2),
                MaterialOverride = _darkMat,
            };
            mesh.RotateX(Mathf.Pi / 2);
            AddChild(mesh);
            _beamMeshes.Add(mesh);
        }

        _readout = new Label3D
        {
            Text = "0 mm",
            Position = new Vector3(0, baseY + CurtainHeight + 0.14f, -Range / 2),
            Billboard = BaseMaterial3D.BillboardModeEnum.Enabled,
            FontSize = 84,
            PixelSize = 0.0015f,
            Modulate = new Color(1.0f, 0.55f, 0.45f),
        };
        AddChild(_readout);
    }

    public override void _Process(double delta)
    {
        float highest = 0.0f;
        bool any = false;
        float baseY = PartLayout.BeltSurface;

        for (int i = 0; i < _beams.Count; i++)
        {
            bool blocked = _beams[i].IsColliding();
            _beamMeshes[i].MaterialOverride = blocked ? _litMat : _darkMat;
            if (!blocked) continue;

            any = true;
            highest = Mathf.Max(highest, _beams[i].Position.Y - baseY);
        }

        IsBlocked = any;
        MeasuredHeight = any ? highest : 0.0f;
        _readout.Text = $"{MeasuredHeight * 1000.0f:0} mm";
    }

    // ---------- IPart (HP-34)

    /// <summary>A measurement and a bit: how far up the curtain the tallest
    /// blocked beam sits, and whether anything is blocked at all.</summary>
    public void DeclareTags(PartTagBuilder tags) => tags
        .Float("height", $"Light Array {tags.Index} Height (m)", TagKind.Input)
        .Bit("blocked", $"Light Array {tags.Index} Blocked", TagKind.Input);

    public void CaptureSettings(PartSettings settings)
    {
        settings.Put("beams", BeamCount);
        settings.Put("curtain_height", CurtainHeight);
        settings.Put("range", Range);
    }

    public void ApplySettings(PartSettings settings)
    {
        if (settings.Whole("beams") is { } beams) BeamCount = beams;
        if (settings.Number("curtain_height") is { } height) CurtainHeight = height;
        if (settings.Number("range") is { } range) Range = range;
    }

    public void StepPart(PartTick tick)
    {
        tick.Write("height", (double)MeasuredHeight);
        tick.Write("blocked", IsBlocked);
    }

    public void DescribeControls(IPartInspector ui)
    {
        // Both settings are read only while the curtain is built, so both need
        // the rebuild alongside them. Until LE-01 they did not have it, and
        // Curtain Height was the one control in the panel that moved and did
        // nothing.
        ui.Slider("Curtain Height (m)", CurtainHeight, 0.1f, 1.0f, 0.02f,
                  value => { CurtainHeight = value; Rebuild(); });
        ui.Slider("Beams", BeamCount, 2, 24, 1,
                  value => { BeamCount = (int)value; Rebuild(); });
    }
}
