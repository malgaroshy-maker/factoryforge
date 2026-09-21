using System.Collections.Generic;
using FactoryForge.TagBus;
using Godot;

namespace FactoryForge.Parts;

/// <summary>
/// Conveyor belt with an integrated load cell scale that measures the total weight of boxes on the belt.
/// </summary>
public partial class WeighingConveyor : ConveyorBelt
{
    private Area3D _scaleArea = null!;
    private readonly HashSet<BoxPhysics> _boxesOnScale = new();

    private const float KgToGrams = 1000.0f;

    /// <summary>Grams on the deck right now.</summary>
    public float MeasuredWeight { get; private set; }

    /// <summary>How many cartons the load cell is carrying. A checkweigher
    /// holding two at once reads the sum, which is neither carton's weight, so
    /// this is worth being able to check rather than assume.</summary>
    public int CartonsOnScale { get; private set; }

    /// <summary>The most it has ever carried at once, for the same reason.</summary>
    public int PeakCartonsOnScale { get; private set; }

    /// <summary>The reading, on the scale (LE-08). Every other part that
    /// measures something shows it on itself — the light curtain's height, the
    /// tank's level, the display's number — and this one computed a weight and
    /// displayed it nowhere, so watching a carton land on the scale told you
    /// nothing without hunting its tag down in the inspector.</summary>
    private Label3D _readout = null!;

    public override void _Ready()
    {
        base._Ready();

        // 3D scale frame indicator (yellow industrial weigh frame)
        var scaleMat = new StandardMaterial3D
        {
            AlbedoColor = new Color(0.9f, 0.7f, 0.1f),
            Metallic = 0.5f,
        };

        var scaleFrame = new MeshInstance3D
        {
            Mesh = new BoxMesh { Size = new Vector3(Size.X * 0.8f, 0.04f, Size.Z * 1.05f) },
            Position = new Vector3(0, -0.04f, 0),
            MaterialOverride = scaleMat,
        };
        AddChild(scaleFrame);

        // Same treatment as LevelTank's and LightArray's readouts: a large font
        // scaled right down, so it reads at working distance without becoming a
        // billboard across the scene.
        _readout = new Label3D
        {
            Text = "0 g",
            Position = new Vector3(0, Size.Y + 0.34f, 0),
            Billboard = BaseMaterial3D.BillboardModeEnum.Enabled,
            FontSize = 84,
            PixelSize = 0.0015f,
            Modulate = new Color(1.0f, 0.85f, 0.35f),
        };
        AddChild(_readout);

        // Weighing detection Area3D
        _scaleArea = new Area3D { Name = "ScaleArea" };
        var col = new CollisionShape3D
        {
            Shape = new BoxShape3D { Size = new Vector3(Size.X * 0.8f, 0.30f, Size.Z) },
            Position = new Vector3(0, 0.15f, 0),
        };
        _scaleArea.AddChild(col);
        AddChild(_scaleArea);

        _scaleArea.BodyEntered += OnBodyEntered;
        _scaleArea.BodyExited += OnBodyExited;
    }

    private void OnBodyEntered(Node3D body)
    {
        if (body is BoxPhysics box)
        {
            _boxesOnScale.Add(box);
            RecalculateWeight();
        }
    }

    private void OnBodyExited(Node3D body)
    {
        if (body is BoxPhysics box)
        {
            _boxesOnScale.Remove(box);
            RecalculateWeight();
        }
    }

    private void RecalculateWeight()
    {
        float total = 0f;
        int cartons = 0;
        foreach (var box in _boxesOnScale)
        {
            if (IsInstanceValid(box))
            {
                // BoxPhysics.Mass is kilograms -- density in kg/m3 times the
                // carton's volume -- so grams is a straight x1000, and the tag
                // is an Int, which grams suits and kilograms would not. The old
                // "x10, scale factor 10kg per mass unit" was neither: it showed
                // a documented 2.16 kg carton as "21 g".
                total += box.Mass * KgToGrams;
                cartons++;
            }
        }
        CartonsOnScale = cartons;
        PeakCartonsOnScale = Mathf.Max(PeakCartonsOnScale, cartons);
        MeasuredWeight = total;
        // Only here, not every frame: the weight changes exactly when a carton
        // enters or leaves, and setting Label3D.Text rebuilds its glyph mesh.
        if (_readout is not null) _readout.Text = $"{MeasuredWeight:0} g";
    }

    // ---------- IPart (HP-34)

    public override void DeclareTags(PartTagBuilder tags) => tags
        .Bit("rotate", $"WeighConveyor {tags.Index} Rotate", TagKind.Output)
        .Int("weight", $"WeighConveyor {tags.Index} Weight", TagKind.Input)
        .Bit("fault", $"WeighConveyor {tags.Index} Drive Fault", TagKind.Input);

    /// <summary>
    /// The fault line here is HP-35, and its absence is what the old design's
    /// drift looked like. The tag was registered exactly as every other
    /// conveyor's is, appeared in the inspector and could be forced -- and
    /// nothing dispatched it, because registering a tag and acting on it were
    /// edits to two different files. <c>SetFaulted</c> was inherited and simply
    /// never called for this subclass. It cannot go missing again: the base
    /// class implements the tick and the subclass has to go out of its way not
    /// to call it.
    /// </summary>
    public override void StepPart(PartTick tick)
    {
        if (tick.TryBit("fault", out bool faulted)) SetFaulted(faulted);
        if (tick.TryBit("rotate", out bool rotate)) SetRunning(rotate);
        tick.Write("weight", (int)MeasuredWeight);
    }

    public override PartOperation? Operation => new("weigh conveyor", "rotate");
}
