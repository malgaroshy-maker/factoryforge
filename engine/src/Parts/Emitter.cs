using FactoryForge.TagBus;
using Godot;

namespace FactoryForge.Parts;

/// <summary>
/// Spawns BoxPhysics instances onto the conveyor belt when triggered.
/// </summary>
public partial class Emitter : Node3D, IPart
{
    /// <summary>Gap between the belt surface and the underside of a new box.
    /// Boxes are *placed* on the belt, not dropped onto it: the old 0.20 m drop
    /// made every carton land, bounce and rock before it settled.</summary>
    [Export] public float DropClearance { get; set; } = 0.002f;

    /// <summary>
    /// A feed gantry straddling the belt, so the emitter is something you can
    /// see and select. It had no mesh at all: placing "Box Emitter" from the
    /// palette put an invisible node in the scene.
    /// </summary>
    public override void _Ready()
    {
        const float span = 0.62f;
        const float legHeight = PartLayout.FloorDrop + 0.42f;

        var frameMat = new StandardMaterial3D
        {
            AlbedoColor = new Color(0.55f, 0.57f, 0.60f),
            Metallic = 0.35f,
            Roughness = 0.50f,
        };
        var spoutMat = new StandardMaterial3D
        {
            AlbedoColor = new Color(0.95f, 0.75f, 0.10f),
            Roughness = 0.40f,
        };

        foreach (int side in new[] { -1, 1 })
        {
            AddChild(new MeshInstance3D
            {
                Mesh = new BoxMesh { Size = new Vector3(0.05f, legHeight, 0.05f) },
                MaterialOverride = frameMat,
                Position = new Vector3(0, 0.42f - legHeight / 2, side * span / 2),
            });
        }

        AddChild(new MeshInstance3D
        {
            Mesh = new BoxMesh { Size = new Vector3(0.06f, 0.05f, span) },
            MaterialOverride = frameMat,
            Position = new Vector3(0, 0.42f, 0),
        });

        // Discharge spout the cartons appear from.
        AddChild(new MeshInstance3D
        {
            Mesh = new BoxMesh { Size = new Vector3(0.24f, 0.16f, 0.30f) },
            MaterialOverride = spoutMat,
            Position = new Vector3(0, 0.30f, 0),
        });
    }

    /// <summary>Every Nth item is metal, so a material-sensing scene has
    /// something to sort. 0 disables it, which is the default and leaves
    /// existing scenes emitting cardboard only.</summary>
    [Export] public int MetalEvery { get; set; }

    /// <summary>
    /// How often a tall carton comes down the line.
    ///
    /// <c>-1</c>, the default, takes the alternation from the host, which is
    /// what a sorting line needs: two emitters feeding one lane must not each
    /// decide independently, or the "mixed stream" is two independent streams
    /// that happen to agree. <c>0</c> never emits a tall one, <c>1</c> emits
    /// nothing else, and N emits every Nth.
    ///
    /// This exists because a palletiser is a one-SKU machine and the library
    /// had no way to say so. A layer height has to be the carton height, and a
    /// stream alternating 0.10 m and 0.30 m cartons cannot have one -- the
    /// stack a cell built out of it toppled on the second layer, which is
    /// correct physics and an impossible scene.
    ///
    /// Saved but deliberately absent from the inspector, the same call
    /// <see cref="PalletStation.AlternateLayers"/> makes: the panel offers
    /// sliders, text and tag pickers, and a four-way meaning packed onto a
    /// slider that starts at -1 is worse than a setting a template sets.
    /// </summary>
    [Export] public int TallEvery { get; set; } = -1;

    private int _emitted;

    /// <summary>Zero the metal cadence counter. Called on scene reset so the
    /// metal phase does not drift from wherever the previous run left it —
    /// the regression contract requires a reset to fully reset.</summary>
    public void ResetCount() { _emitted = 0; _shaped = 0; }

    /// <summary>Cartons emitted since the last reset, for the tall cadence.
    /// Separate from <see cref="_emitted"/> so the metal phase and the shape
    /// phase cannot be made to share a period by accident.</summary>
    private int _shaped;

    /// <summary>Is the next carton a tall one? See <see cref="TallEvery"/> for
    /// why the default defers to the host.</summary>
    private bool NextIsTall(PartTick tick)
    {
        if (TallEvery < 0) return tick.Host.NextAlternate();
        _shaped++;
        return TallEvery > 0 && _shaped % TallEvery == 0;
    }

    public BoxPhysics SpawnBox(bool isTall)
    {
        _emitted++;
        bool metal = MetalEvery > 0 && _emitted % MetalEvery == 0;
        var box = new BoxPhysics { IsTall = isTall, IsMetal = metal };
        GetParent()?.AddChild(box);

        // Height is only known once IsTall is set, so the resting position is
        // computed here rather than baked into a fixed offset.
        float centreY = PartLayout.BeltSurface + DropClearance + box.Height / 2.0f;
        box.GlobalPosition = GlobalPosition + new Vector3(0, centreY, 0);
        return box;
    }

    // ---------- IPart (HP-34)

    /// <summary>Has the current rising edge already produced a carton? Holding
    /// the tag high must not spawn one every frame, which is how a real emitter
    /// input behaves.
    ///
    /// This lives on the part rather than in a dictionary in the editor keyed
    /// by instance id, which is what it used to be — the state belongs to the
    /// machine, and a renamed or duplicated emitter kept the old key's edge.
    /// </summary>
    private bool _edgeSpent;

    public void DeclareTags(PartTagBuilder tags) =>
        tags.Bit("emit", $"Emitter {tags.Index} (Emit)", TagKind.Output);

    public void CaptureSettings(PartSettings settings)
    {
        settings.Put("metal_every", MetalEvery);
        settings.Put("tall_every", TallEvery);
        // How far above the belt a carton is released. Two millimetres by
        // default, and the number somebody changes when cartons bounce or clip
        // on spawn -- a fix that silently reverted on every reload.
        settings.Put("drop_clearance", DropClearance);
    }

    public void ApplySettings(PartSettings settings)
    {
        if (settings.Whole("metal_every") is { } metal) MetalEvery = metal;
        if (settings.Whole("tall_every") is { } tall) TallEvery = tall;
        if (settings.Number("drop_clearance") is { } clearance) DropClearance = clearance;
    }

    public void StepPart(PartTick tick)
    {
        if (!tick.TryBit("emit", out bool emit)) return;

        if (!emit) { _edgeSpent = false; return; }
        if (_edgeSpent) return;

        // The edge is left unspent while capped, so the same rising edge spawns
        // the instant the count drops rather than needing the signal to
        // re-pulse. See FF-12.
        if (!tick.Host.CanSpawnItem) return;

        _edgeSpent = true;
        SpawnBox(NextIsTall(tick));
    }

    public void DescribeControls(IPartInspector ui) =>
        // The setting that makes an inductive sensor a different part from a
        // photoelectric one. Read fresh on every emission, so no rebuild.
        ui.Slider("Metal every Nth", MetalEvery, 0, 10, 1, value => MetalEvery = (int)value);

    public void ResetPart(PartReset reset)
    {
        ResetCount();
        _edgeSpent = false;
    }

    public PartOperation? Operation => new("emitter", "emit");

    /// <summary>A pulse, not a latch: the tick above only spawns on the rising
    /// edge, so a click that left the tag high would look broken.</summary>
    public void Operate(PartOperate op) => op.PulseBit("emit");
}
