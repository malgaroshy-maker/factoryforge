using FactoryForge.TagBus;
using Godot;

namespace FactoryForge.Parts;

/// <summary>
/// A maintained rotary selector (CP-09).
///
/// Every operator input in the library so far is momentary or latching: Start
/// and Stop pulse for one scan, the mushroom latches until Reset. A selector is
/// the third kind, and the one an Auto/Manual program is built around — it
/// *stays where it is put*, and the controller reads a position rather than an
/// edge. A student who has only ever handled momentary bits has never had to
/// write "if the selector is in Manual, ignore the sequencer".
///
/// The position is an Int, not a set of bits, because that is what it is: one
/// switch in one of N positions, and only one at a time. Publishing three
/// mutually exclusive bits would invite a program that handles two of them
/// being true, a state the hardware cannot produce.
/// </summary>
public partial class SelectorSwitch : Node3D, IPart
{
    /// <summary>How many detents. Two is Off/On, three is the classic
    /// Manual / Off / Auto.</summary>
    [Export] public int PositionCount { get; set; } = 3;

    /// <summary>Labels drawn on the escutcheon, in position order. Extra
    /// labels are ignored; missing ones fall back to the index.</summary>
    [Export] public string Labels { get; set; } = "MAN,OFF,AUTO";

    private const float PlateY = 0.44f;
    private const float PlateRadius = 0.11f;

    /// <summary>Total sweep across all detents, degrees. A real selector's
    /// throw, not a full turn.</summary>
    private const float SweepDegrees = 90.0f;

    /// <summary>-1 means "nobody has said", so <c>_Ready</c> may pick the
    /// default. It matters because <see cref="Editor.PartProperties"/> applies
    /// a saved detent <em>before</em> the node enters the tree, and a _Ready
    /// that assigned unconditionally would overwrite it — which is exactly what
    /// it did, and the save/load self-test caught it.</summary>
    private int _position = -1;

    /// <summary>Which detent the switch is in, 0-based. Called Detent rather
    /// than Position because Node3D already owns that name, and hiding a base
    /// member every caller in the editor reaches for would be a trap.</summary>
    public int Detent
    {
        get => _position;
        set
        {
            _position = Mathf.Clamp(value, 0, Mathf.Max(PositionCount - 1, 0));
            ApplyPosition();
        }
    }

    private Node3D _knob = null!;
    private Label3D _readout = null!;

    public override void _Ready()
    {
        // Default the switch to the middle detent on an odd count (OFF on a
        // three-position) and to 0 on an even one, so a placed selector starts
        // somewhere a program would consider safe rather than in Auto. Only
        // when nothing has been restored into it -- see _position's own note.
        if (_position < 0) _position = PositionCount % 2 == 1 ? PositionCount / 2 : 0;
        _position = Mathf.Clamp(_position, 0, Mathf.Max(PositionCount - 1, 0));

        var caseMat = new StandardMaterial3D
        {
            AlbedoColor = new Color(0.20f, 0.22f, 0.25f),
            Metallic = 0.45f,
            Roughness = 0.45f,
        };
        var plateMat = new StandardMaterial3D
        {
            AlbedoColor = new Color(0.14f, 0.15f, 0.17f),
            Metallic = 0.30f,
            Roughness = 0.55f,
        };

        float postHeight = PartLayout.FloorDrop + PlateY - PlateRadius;
        AddChild(new MeshInstance3D
        {
            Name = "Post",
            Mesh = new BoxMesh { Size = new Vector3(0.05f, postHeight, 0.05f) },
            MaterialOverride = caseMat,
            Position = new Vector3(0, PlateY - PlateRadius - postHeight / 2, 0),
        });
        AddChild(new MeshInstance3D
        {
            Name = "BaseFoot",
            Mesh = new BoxMesh { Size = new Vector3(0.18f, 0.025f, 0.18f) },
            MaterialOverride = caseMat,
            Position = new Vector3(0, -PartLayout.FloorDrop, 0),
        });

        // Escutcheon plate, facing +Z like every other operator control here,
        // so it reads correctly at the default rotation and turns with R.
        AddChild(new MeshInstance3D
        {
            Name = "Plate",
            Mesh = new BoxMesh { Size = new Vector3(PlateRadius * 2, PlateRadius * 2, 0.035f) },
            MaterialOverride = plateMat,
            Position = new Vector3(0, PlateY, 0),
        });

        BuildDetentMarks();

        _knob = new Node3D { Name = "KnobPivot", Position = new Vector3(0, PlateY, 0.020f) };
        AddChild(_knob);

        var knobMat = new StandardMaterial3D
        {
            AlbedoColor = new Color(0.10f, 0.10f, 0.12f),
            Roughness = 0.40f,
        };
        var boss = new MeshInstance3D
        {
            Name = "KnobBoss",
            Mesh = new CylinderMesh { TopRadius = 0.030f, BottomRadius = 0.034f, Height = 0.028f },
            MaterialOverride = knobMat,
            Position = new Vector3(0, 0, 0.014f),
        };
        boss.RotateX(Mathf.Pi / 2);
        _knob.AddChild(boss);

        // The flag. A cylindrical knob looks identical at every angle, so
        // without a pointer the switch would have no readable position at all —
        // the same lesson the roller deck's seam taught.
        _knob.AddChild(new MeshInstance3D
        {
            Name = "KnobFlag",
            Mesh = new BoxMesh { Size = new Vector3(0.018f, 0.075f, 0.012f) },
            MaterialOverride = new StandardMaterial3D
            {
                AlbedoColor = new Color(0.92f, 0.92f, 0.95f),
                Roughness = 0.35f,
            },
            Position = new Vector3(0, 0.044f, 0.030f),
        });

        // On the plate, not hanging below it on the post. Off the plate the
        // label had no background to read against and rendered as a smear of
        // glyphs beside the machine -- a legend nobody can read is not a
        // legend, and the position it names is the part's whole output.
        _readout = new Label3D
        {
            Name = "PositionLabel",
            Text = LabelFor(_position),
            FontSize = 48,
            PixelSize = BaseLabelPixelSize,
            Modulate = new Color(0.97f, 0.97f, 1.0f),
            OutlineSize = 6,
            OutlineModulate = new Color(0.05f, 0.05f, 0.06f),
            Position = new Vector3(0, PlateY - PlateRadius * 0.66f, 0.020f),
        };
        AddChild(_readout);
        FitLabel();

        ApplyPosition();
    }

    /// <summary>One tick mark per detent, so the plate says what the positions
    /// are rather than leaving the knob's angle to be interpreted.</summary>
    private void BuildDetentMarks()
    {
        var markMat = new StandardMaterial3D { AlbedoColor = new Color(0.85f, 0.86f, 0.88f) };
        int count = Mathf.Max(PositionCount, 1);

        for (int i = 0; i < count; i++)
        {
            float angle = Mathf.DegToRad(AngleFor(i));
            float radius = PlateRadius * 0.62f;
            var mark = new MeshInstance3D
            {
                Name = $"Detent{i}",
                Mesh = new BoxMesh { Size = new Vector3(0.006f, 0.020f, 0.003f) },
                MaterialOverride = markMat,
                Position = new Vector3(Mathf.Sin(angle) * radius, PlateY + Mathf.Cos(angle) * radius, 0.019f),
            };
            mark.RotateZ(-angle);
            AddChild(mark);
        }
    }

    private float AngleFor(int position)
    {
        int count = Mathf.Max(PositionCount, 1);
        if (count == 1) return 0.0f;
        float fraction = position / (float)(count - 1);
        return -SweepDegrees / 2.0f + SweepDegrees * fraction;
    }

    private string LabelFor(int position)
    {
        var parts = Labels.Split(',');
        return position >= 0 && position < parts.Length && parts[position].Length > 0
            ? parts[position].Trim()
            : position.ToString();
    }

    /// <summary>Click it round one detent, wrapping at the end — a hand on a
    /// selector, and the only gesture a single click can honestly express.
    /// </summary>
    public void Advance() => Detent = (_position + 1) % Mathf.Max(PositionCount, 1);

    private const float BaseLabelPixelSize = 0.0016f;

    /// <summary>Fraction of the plate the legend may occupy.</summary>
    private const float LabelFitMargin = 0.86f;

    /// <summary>Shrink the legend until it fits the plate, measuring real font
    /// metrics rather than guessing from the string's length. "PURGE" is more
    /// than twice the width of "ON", and a legend that runs off its own
    /// escutcheon is exactly as useless as no legend -- the same lesson the
    /// digital display already had to learn from "720 g".</summary>
    private void FitLabel()
    {
        if (_readout is null) return;
        _readout.PixelSize = BaseLabelPixelSize;

        var font = ThemeDB.FallbackFont;
        float natural = font.GetStringSize(_readout.Text, HorizontalAlignment.Left, -1,
                                           _readout.FontSize).X * _readout.PixelSize;
        float budget = PlateRadius * 2 * LabelFitMargin;
        if (natural > budget && natural > 0.0f) _readout.PixelSize = BaseLabelPixelSize * budget / natural;
    }

    private void ApplyPosition()
    {
        if (_knob is null) return;
        _knob.Rotation = new Vector3(0, 0, -Mathf.DegToRad(AngleFor(_position)));
        if (_readout is null) return;
        _readout.Text = LabelFor(_position);
        FitLabel();
    }

    /// <summary>Rebuild after a setting the geometry was built from has
    /// changed — the detent count and the plate marks are made once.</summary>
    public void Rebuild()
    {
        foreach (var child in GetChildren()) { RemoveChild(child); child.QueueFree(); }
        int keep = _position;
        _Ready();
        Detent = keep;
    }

    // ---------- IPart (HP-34)

    /// <summary>An Int, not a set of mutually exclusive bits: one switch is in
    /// exactly one position, and publishing three bits would invite a program
    /// that handles two of them being true.</summary>
    public void DeclareTags(PartTagBuilder tags) =>
        tags.Int("position", $"Selector {tags.Index} Position", TagKind.Input);

    public void CaptureSettings(PartSettings settings)
    {
        settings.Put("positions", PositionCount);
        settings.Put("labels", Labels);
        // Where the switch was left. A real selector does not spring back, and a
        // scene reopened mid-experiment should reopen in the mode it was in.
        settings.Put("detent", Detent);
    }

    public void ApplySettings(PartSettings settings)
    {
        if (settings.Whole("positions") is { } positions) PositionCount = positions;
        if (settings.Text("labels") is { } labels) Labels = labels;
        // Last, so the clamp sees the detent count this scene asked for rather
        // than the default three.
        if (settings.Whole("detent") is { } detent) Detent = detent;
    }

    /// <summary>The selector is an operator input and nothing else drives it, so
    /// the part is always the authority: it publishes where the knob is and
    /// never reads the tag back. That is the same one-writer rule the panel's
    /// buttons follow.</summary>
    public void StepPart(PartTick tick) => tick.Write("position", Detent);

    public void DescribeControls(IPartInspector ui)
    {
        // Both are read only while the plate and its detent marks are built, so
        // both need the rebuild alongside them.
        ui.Slider("Positions", PositionCount, 2, 6, 1,
                  value => { PositionCount = (int)value; Rebuild(); });
        ui.Text("Labels (comma)", Labels, 24, text => { Labels = text; Rebuild(); });
    }

    /// <summary>Drives the *part*, not the tag: the switch steps round a detent
    /// and publishes its position on the next tick. Writing the tag here would
    /// make the click and the machine two authorities for one value.</summary>
    public PartOperation? Operation => new("selector", "position");

    public void Operate(PartOperate op) => Advance();
}
