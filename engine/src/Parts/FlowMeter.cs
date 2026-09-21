using System.Collections.Generic;
using FactoryForge.TagBus;
using Godot;

namespace FactoryForge.Parts;

/// <summary>
/// An in-line flow meter: a rate, and a totaliser you can zero.
///
/// The two measurements are genuinely different instruments and a program uses
/// them for different things. The rate is a process variable — it is what an
/// inner flow loop controls, and it moves in a second where a level moves in a
/// minute, which is the whole reason a cascade is worth building. The total is a
/// counter: batch this many litres and stop, which is a completely different
/// piece of logic and one the library could not express at all.
///
/// The totaliser's reset is a <em>level</em>, not an edge, exactly like the
/// measuring encoder's: holding the reset leg high holds the total at zero, the
/// way a counter's own reset input behaves. A program that pulses it and expects
/// the total to stay cleared has misread the contact, and that is a mistake
/// worth making somewhere it costs nothing.
///
/// It measures every <see cref="DosingPump"/> within reach, which stands in for
/// being plumbed into the same pipe.
/// </summary>
public partial class FlowMeter : Node3D, IPart
{
    /// <summary>How far up the pipe the meter sees, metres.</summary>
    [Export] public float Reach { get; set; } = 1.2f;

    /// <summary>Instrument damping, seconds. A real flow transmitter is damped
    /// because raw flow is noisy, and a PID tuned against an undamped signal
    /// chases noise. Zero is allowed and is instructive.</summary>
    [Export] public float Damping { get; set; } = 0.25f;

    /// <summary>Measured flow, litres per minute, after damping.</summary>
    public float Rate { get; private set; }

    /// <summary>Litres since the last reset.</summary>
    public float Total { get; private set; }

    private float _rescanTimer;
    private readonly List<DosingPump> _pumps = new();
    private Label3D _readout = null!;

    private const float BodyY = 0.20f;
    private const float RescanInterval = 1.0f;

    public override void _Ready()
    {
        var steelMat = new StandardMaterial3D
        {
            AlbedoColor = new Color(0.74f, 0.76f, 0.80f),
            Metallic = 0.75f,
            Roughness = 0.28f,
        };
        var headMat = new StandardMaterial3D
        {
            AlbedoColor = new Color(0.16f, 0.35f, 0.50f),
            Metallic = 0.45f,
            Roughness = 0.42f,
        };
        var trimMat = new StandardMaterial3D
        {
            AlbedoColor = new Color(0.20f, 0.22f, 0.25f),
            Metallic = 0.45f,
            Roughness = 0.45f,
        };

        // A spool piece with flanges and a transmitter head on top, which is
        // what one looks like and what makes it read as "in the pipe" rather
        // than as another box beside the line.
        AddChild(new MeshInstance3D
        {
            Name = "Spool",
            Mesh = new CylinderMesh { TopRadius = 0.045f, BottomRadius = 0.045f, Height = 0.26f },
            MaterialOverride = steelMat,
            Position = new Vector3(0, BodyY, 0),
            Rotation = new Vector3(Mathf.Pi / 2.0f, 0, 0),
        });
        foreach (float z in new[] { -0.13f, 0.13f })
        {
            AddChild(new MeshInstance3D
            {
                Name = $"Flange{(z < 0 ? "A" : "B")}",
                Mesh = new CylinderMesh { TopRadius = 0.07f, BottomRadius = 0.07f, Height = 0.02f },
                MaterialOverride = steelMat,
                Position = new Vector3(0, BodyY, z),
                Rotation = new Vector3(Mathf.Pi / 2.0f, 0, 0),
            });
        }
        AddChild(new MeshInstance3D
        {
            Name = "TransmitterHead",
            Mesh = new BoxMesh { Size = new Vector3(0.12f, 0.10f, 0.09f) },
            MaterialOverride = headMat,
            Position = new Vector3(0, BodyY + 0.11f, 0),
        });
        AddChild(new MeshInstance3D
        {
            Name = "Stand",
            Mesh = new CylinderMesh
            {
                TopRadius = 0.024f, BottomRadius = 0.030f, Height = BodyY + PartLayout.FloorDrop,
            },
            MaterialOverride = trimMat,
            Position = new Vector3(0, (BodyY - PartLayout.FloorDrop) / 2.0f, 0),
        });

        _readout = new Label3D
        {
            Name = "FlowReadout",
            Text = "0.0 L/min\n0 L",
            Position = new Vector3(0, BodyY + 0.30f, 0),
            Billboard = BaseMaterial3D.BillboardModeEnum.Enabled,
            FontSize = 72,
            PixelSize = 0.0015f,
            Modulate = new Color(0.65f, 0.88f, 1.0f),
        };
        AddChild(_readout);
    }

    public void Step(bool reset, float delta)
    {
        _rescanTimer -= delta;
        if (_rescanTimer <= 0.0f)
        {
            _rescanTimer = RescanInterval;
            FindPumps();
        }

        float raw = 0.0f;
        foreach (var pump in _pumps)
        {
            if (IsInstanceValid(pump)) raw += pump.Flow;
        }

        // First-order damping, with the time constant floored. A zero constant
        // is a legitimate setting -- an undamped transmitter -- and it must
        // mean "follow instantly", not "divide by nothing": the tag table now
        // rejects a non-finite float outright, and the throw would surface
        // inside the tick where it is hardest to read (HP-23).
        float tau = Mathf.Max(Damping, 0.0f);
        Rate = tau <= 0.0001f
            ? raw
            : Mathf.Lerp(Rate, raw, Mathf.Clamp(delta / tau, 0.0f, 1.0f));

        // A level, not an edge: holding the reset leg high holds the total at
        // zero, exactly like a counter's own reset input.
        if (reset) Total = 0.0f;
        else Total += Rate / 60.0f * delta;

        Apply();
    }

    private void Apply()
    {
        if (_readout is null) return;
        string text = $"{Rate:0.0} L/min\n{Total:0} L";
        if (_readout.Text != text) _readout.Text = text;
    }

    public void ResetTotal()
    {
        Rate = 0.0f;
        Total = 0.0f;
        Apply();
    }

    private void FindPumps()
    {
        _pumps.Clear();

        Node? root = GetTree()?.Root ?? GetParent();
        if (root is null) return;

        Vector3 here = GlobalPosition;
        foreach (var node in Walk(root))
        {
            if (node is DosingPump pump && pump.GlobalPosition.DistanceTo(here) <= Reach)
                _pumps.Add(pump);
        }
    }

    private static IEnumerable<Node> Walk(Node from)
    {
        foreach (var child in from.GetChildren())
        {
            yield return child;
            foreach (var grand in Walk(child)) yield return grand;
        }
    }

    // ---------- IPart (HP-34)

    public void DeclareTags(PartTagBuilder tags) => tags
        // A Float because it is a measurement, and an Int total because that is
        // what a batch counter hands a program -- the same split the measuring
        // encoder's `rate` and `count` make.
        .Float("rate", $"Flow Meter {tags.Index} Rate (L/min)", TagKind.Input)
        .Int("total", $"Flow Meter {tags.Index} Total (L)", TagKind.Input)
        .Bit("reset", $"Flow Meter {tags.Index} Totaliser Reset", TagKind.Output);

    public void CaptureSettings(PartSettings settings)
    {
        settings.Put("reach", Reach);
        settings.Put("damping", Damping);
    }

    public void ApplySettings(PartSettings settings)
    {
        if (settings.Number("reach") is { } reach) Reach = reach;
        if (settings.Number("damping") is { } damping) Damping = damping;
    }

    public void StepPart(PartTick tick)
    {
        Step(tick.Bit("reset"), tick.Dt);
        tick.Write("rate", (double)Rate);
        tick.Write("total", (int)Total);
    }

    public void DescribeControls(IPartInspector ui)
    {
        ui.Slider("Reach (m)", Reach, 0.2f, 5.0f, 0.1f, value => Reach = value);
        ui.Slider("Damping (s)", Damping, 0.0f, 5.0f, 0.05f, value => Damping = value);
    }

    public void ResetPart(PartReset reset)
    {
        ResetTotal();
        reset.Write("rate", 0.0);
        reset.Write("total", 0);
    }
}
