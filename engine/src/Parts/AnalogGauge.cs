using FactoryForge.TagBus;
using Godot;

namespace FactoryForge.Parts;

/// <summary>
/// A needle instrument on a post (CP-05).
///
/// The library could show an integer count on a seven-segment panel and had
/// nowhere at all to show a float. A tank level, a drive speed, a temperature —
/// every analog value in a scene lived only in the tag list, on the other side
/// of the screen from the machine it described. This is the part that puts a
/// number back in the world it came from.
///
/// It is an instrument, so it teaches nothing on its own. What it does is make
/// the analog parts legible without alt-tabbing, and give a scene somewhere to
/// put the one measurement it is about.
/// </summary>
public partial class AnalogGauge : Node3D, IPart
{
    [Export] public float ScaleMin { get; set; }
    [Export] public float ScaleMax { get; set; } = 100.0f;

    /// <summary>Reading above which the dial's red band starts. Set it outside
    /// the scale to hide the band.</summary>
    [Export] public float AlarmAt { get; set; } = 85.0f;

    [Export] public string Unit { get; set; } = "%";

    /// <summary>Needle sweep, degrees. 240 is the industrial convention: the
    /// gap at the bottom is where the pivot and the maker's name go.</summary>
    private const float SweepDegrees = 240.0f;

    private const float DialRadius = 0.17f;
    private const float DialY = 0.52f;

    private float _value;

    /// <summary>The displayed reading, in the gauge's own units.</summary>
    public float Value
    {
        get => _value;
        set { _value = value; ApplyValue(); }
    }

    /// <summary>True while the reading is in the red band. Not published as a
    /// tag — the gauge is a display, and an alarm contact belongs to whatever
    /// is being measured, not to the thing looking at it.</summary>
    public bool InAlarm => _value >= AlarmAt;

    /// <summary>Where the needle is actually pointing, in degrees. Exposed so a
    /// test can check the needle moved and not merely that the digits under it
    /// changed — a gauge whose face is a lie would pass every tag-level
    /// assertion there is.</summary>
    public float RotationOfNeedleDegrees =>
        _needlePivot is null ? 0.0f : Mathf.RadToDeg(_needlePivot.Rotation.Z);

    private Node3D _needlePivot = null!;
    private Label3D _readout = null!;
    private Label3D _unitLabel = null!;
    private MeshInstance3D _alarmBand = null!;

    public override void _Ready()
    {
        var caseMat = new StandardMaterial3D
        {
            AlbedoColor = new Color(0.20f, 0.22f, 0.25f),
            Metallic = 0.50f,
            Roughness = 0.40f,
        };
        var faceMat = new StandardMaterial3D
        {
            AlbedoColor = new Color(0.93f, 0.93f, 0.90f),
            Roughness = 0.75f,
        };

        float postHeight = PartLayout.FloorDrop + DialY - DialRadius * 0.4f;
        AddChild(new MeshInstance3D
        {
            Name = "Post",
            Mesh = new BoxMesh { Size = new Vector3(0.05f, postHeight, 0.05f) },
            MaterialOverride = caseMat,
            Position = new Vector3(0, DialY - DialRadius * 0.4f - postHeight / 2.0f, 0),
        });
        AddChild(new MeshInstance3D
        {
            Name = "BaseFoot",
            Mesh = new BoxMesh { Size = new Vector3(0.18f, 0.025f, 0.18f) },
            MaterialOverride = caseMat,
            Position = new Vector3(0, -PartLayout.FloorDrop, 0),
        });

        // Case, then face, then everything on the face — all built facing +Z so
        // the gauge reads correctly when dropped with the default rotation and
        // turns with R like every other part.
        var bezel = new MeshInstance3D
        {
            Name = "Bezel",
            Mesh = new CylinderMesh { TopRadius = DialRadius, BottomRadius = DialRadius, Height = 0.06f },
            MaterialOverride = caseMat,
            Position = new Vector3(0, DialY, 0),
        };
        bezel.RotateX(Mathf.Pi / 2);
        AddChild(bezel);

        var face = new MeshInstance3D
        {
            Name = "Face",
            Mesh = new CylinderMesh
            {
                TopRadius = DialRadius * 0.90f,
                BottomRadius = DialRadius * 0.90f,
                Height = 0.006f,
            },
            MaterialOverride = faceMat,
            Position = new Vector3(0, DialY, 0.032f),
        };
        face.RotateX(Mathf.Pi / 2);
        AddChild(face);

        BuildTicks();
        BuildAlarmBand();

        _needlePivot = new Node3D { Name = "NeedlePivot", Position = new Vector3(0, DialY, 0.038f) };
        AddChild(_needlePivot);

        var needleMat = new StandardMaterial3D
        {
            AlbedoColor = new Color(0.85f, 0.10f, 0.08f),
            Roughness = 0.4f,
        };
        // Drawn along +Y from the pivot and offset by half its length, so the
        // pivot really is the pivot and the needle turns about the hub instead
        // of about its own middle.
        _needlePivot.AddChild(new MeshInstance3D
        {
            Name = "Needle",
            Mesh = new BoxMesh { Size = new Vector3(0.012f, DialRadius * 0.78f, 0.004f) },
            MaterialOverride = needleMat,
            Position = new Vector3(0, DialRadius * 0.39f, 0),
        });
        AddChild(new MeshInstance3D
        {
            Name = "Hub",
            Mesh = new SphereMesh { Radius = 0.018f, Height = 0.036f },
            MaterialOverride = caseMat,
            Position = new Vector3(0, DialY, 0.040f),
        });

        _readout = new Label3D
        {
            Name = "DigitalReadout",
            Text = "0.0",
            FontSize = 34,
            PixelSize = 0.0017f,
            Modulate = new Color(0.10f, 0.10f, 0.12f),
            Position = new Vector3(0, DialY - DialRadius * 0.50f, 0.042f),
        };
        AddChild(_readout);

        _unitLabel = new Label3D
        {
            Name = "UnitLabel",
            Text = Unit,
            FontSize = 24,
            PixelSize = 0.0017f,
            Modulate = new Color(0.35f, 0.36f, 0.40f),
            Position = new Vector3(0, DialY + DialRadius * 0.40f, 0.042f),
        };
        AddChild(_unitLabel);

        ApplyValue();
    }

    /// <summary>Eleven graduations across the sweep. Enough to read a value off
    /// the dial rather than only off the digits under it, which is the entire
    /// reason an analog instrument is worth having next to a digital one.</summary>
    private void BuildTicks()
    {
        var tickMat = new StandardMaterial3D { AlbedoColor = new Color(0.12f, 0.12f, 0.14f) };
        const int ticks = 11;

        for (int i = 0; i < ticks; i++)
        {
            float fraction = i / (float)(ticks - 1);
            float angle = Mathf.DegToRad(-SweepDegrees / 2.0f + SweepDegrees * fraction);
            bool major = i % 5 == 0;

            float length = major ? 0.030f : 0.018f;
            float radius = DialRadius * 0.90f - length / 2.0f - 0.008f;

            var tick = new MeshInstance3D
            {
                Name = $"Tick{i}",
                Mesh = new BoxMesh { Size = new Vector3(major ? 0.008f : 0.005f, length, 0.003f) },
                MaterialOverride = tickMat,
                Position = new Vector3(Mathf.Sin(angle) * radius, DialY + Mathf.Cos(angle) * radius, 0.036f),
            };
            tick.RotateZ(-angle);
            AddChild(tick);
        }
    }

    /// <summary>The red band, drawn as one wedge-ish arc of small segments from
    /// <see cref="AlarmAt"/> to full scale. Rebuilt whenever the scale changes,
    /// because a band left where the old scale put it is worse than none.</summary>
    private void BuildAlarmBand()
    {
        _alarmBand = new MeshInstance3D { Name = "AlarmBand" };
        AddChild(_alarmBand);
        RebuildAlarmBand();
    }

    private void RebuildAlarmBand()
    {
        if (_alarmBand is null) return;

        foreach (var child in _alarmBand.GetChildren()) child.QueueFree();

        float span = ScaleMax - ScaleMin;
        if (span <= 0.0f) return;

        float start = Mathf.Clamp((AlarmAt - ScaleMin) / span, 0.0f, 1.0f);
        if (start >= 1.0f) return;

        var bandMat = new StandardMaterial3D
        {
            AlbedoColor = new Color(0.85f, 0.15f, 0.12f),
            Roughness = 0.6f,
        };

        const int segments = 12;
        for (int i = 0; i < segments; i++)
        {
            float fraction = Mathf.Lerp(start, 1.0f, (i + 0.5f) / segments);
            float angle = Mathf.DegToRad(-SweepDegrees / 2.0f + SweepDegrees * fraction);
            float radius = DialRadius * 0.90f - 0.006f;

            var seg = new MeshInstance3D
            {
                Mesh = new BoxMesh { Size = new Vector3(0.014f, 0.008f, 0.003f) },
                MaterialOverride = bandMat,
                Position = new Vector3(Mathf.Sin(angle) * radius, DialY + Mathf.Cos(angle) * radius, 0.037f),
            };
            seg.RotateZ(-angle);
            _alarmBand.AddChild(seg);
        }
    }

    /// <summary>Change the plate the needle is graduated against. Rebuilds the
    /// red band, because the band's position is a function of the scale and
    /// leaving it put would silently mislabel the instrument.</summary>
    public void ConfigureScale(float min, float max, float alarmAt, string unit)
    {
        ScaleMin = min;
        ScaleMax = max;
        AlarmAt = alarmAt;
        Unit = unit;
        if (_unitLabel is not null) _unitLabel.Text = unit;
        RebuildAlarmBand();
        ApplyValue();
    }

    private void ApplyValue()
    {
        if (_needlePivot is null) return;

        float span = ScaleMax - ScaleMin;
        float fraction = span > 0.0f ? Mathf.Clamp((_value - ScaleMin) / span, 0.0f, 1.0f) : 0.0f;
        float angle = Mathf.DegToRad(-SweepDegrees / 2.0f + SweepDegrees * fraction);

        // Negative about Z: the ticks above were placed with sin/cos measured
        // clockwise from straight up, so the needle has to turn the same way or
        // it points at the mirror of the value.
        _needlePivot.Rotation = new Vector3(0, 0, -angle);

        if (_readout is not null)
            _readout.Text = Mathf.Abs(_value) >= 100.0f ? _value.ToString("0") : _value.ToString("0.0");
    }

    // ---------- IPart (HP-34)

    public void DeclareTags(PartTagBuilder tags) =>
        tags.Float("value", $"Gauge {tags.Index} Value", TagKind.Output);

    public void CaptureSettings(PartSettings settings)
    {
        settings.Put("scale_min", ScaleMin);
        settings.Put("scale_max", ScaleMax);
        settings.Put("alarm_at", AlarmAt);
        settings.Put("unit", Unit);
    }

    public void ApplySettings(PartSettings settings)
    {
        if (settings.Number("scale_min") is { } min) ScaleMin = min;
        if (settings.Number("scale_max") is { } max) ScaleMax = max;
        if (settings.Number("alarm_at") is { } alarm) AlarmAt = alarm;
        if (settings.Text("unit") is { } unit) Unit = unit;
    }

    public void StepPart(PartTick tick)
    {
        if (tick.Has("value")) Value = tick.Number("value");
    }

    public void DescribeControls(IPartInspector ui)
    {
        // Every one of these goes through ConfigureScale, which rebuilds the red
        // band: a band left where the old scale put it would be a mislabelled
        // instrument, which is worse than no band at all.
        ui.Slider("Scale Min", ScaleMin, -10000.0f, 10000.0f, 1.0f,
                  value => ConfigureScale(value, ScaleMax, AlarmAt, Unit));
        ui.Slider("Scale Max", ScaleMax, -10000.0f, 10000.0f, 1.0f,
                  value => ConfigureScale(ScaleMin, value, AlarmAt, Unit));
        ui.Slider("Alarm At", AlarmAt, -10000.0f, 10000.0f, 1.0f,
                  value => ConfigureScale(ScaleMin, ScaleMax, value, Unit));
        ui.Text("Unit", Unit, 8, text => ConfigureScale(ScaleMin, ScaleMax, AlarmAt, text));
    }
}
