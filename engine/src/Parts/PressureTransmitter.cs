using FactoryForge.TagBus;
using Godot;

namespace FactoryForge.Parts;

/// <summary>
/// A pressure transmitter on a compressed-air receiver, with the receiver's
/// supply valve and a steady consumption (IP-17).
///
/// <b>Raw analog only.</b> Every other measuring part here offers engineering
/// units as the default and raw counts as an option (IP-16). This one does not
/// offer engineering units at all, because the lesson it exists for is the one
/// engineering units skip: a pressure transmitter is a two-wire 4–20 mA loop
/// device, the PLC's analog card turns that current into an integer, and "bar"
/// exists only after the program has scaled the integer (<c>NORM_X</c> then
/// <c>SCALE_X</c> on an S7). Publishing bar would hand the student the answer.
/// So the <c>signal</c> setting is <c>ma_4_20</c> (the default, with an
/// injectable wire break) or <c>s7_raw</c> (a 0–20 mA / 0–10 V channel, where a
/// broken wire reads as a perfectly plausible zero). A scene file that asks for
/// <c>engineering</c> gets <c>ma_4_20</c>. The receiver carries a local Bourdon
/// gauge reading in bar, because a real one does, and the transmitter's own
/// display shows the loop current — so the person at the machine and the
/// program reading the card can be compared.
///
/// <b>The receiver.</b> A first-order pneumatic capacity. The supply valve,
/// when the program opens it, admits air from the compressor main at
/// <see cref="SupplyPressure"/> through a fixed restriction, and a non-return
/// valve stops it flowing back; the plant downstream consumes air in proportion
/// to the pressure it is fed at, scaled by <see cref="ConsumptionPercent"/>:
///
/// <code>  dP/dt = Ks·(Ps − P)·[supply open] − Kc·(consumption/100)·P  </code>
///
/// With the defaults the receiver fills towards 6.4 bar with the valve open and
/// bleeds away over several seconds with it shut, so a two-point (hysteresis)
/// pressure controller — load at 5.5 bar, unload at 6.0 — has a real process
/// to act on, and raising the consumption visibly shortens its cycle.
/// </summary>
public partial class PressureTransmitter : Node3D, IPart
{
    /// <summary>Compressor main pressure, bar(g): what the receiver fills
    /// towards with no consumption.</summary>
    [Export] public float SupplyPressure { get; set; } = 8.0f;

    /// <summary>How hard the plant downstream draws on the receiver, percent. A
    /// property of the process, not a signal.</summary>
    [Export] public float ConsumptionPercent { get; set; } = 25.0f;

    /// <summary>Fill conductance through the supply valve, per second.</summary>
    private const float SupplyRate = 0.5f;

    /// <summary>Consumption conductance at 100 %, per second.</summary>
    private const float ConsumptionRate = 0.5f;

    /// <summary>Receiver pressure, bar(g). The measured variable.</summary>
    public float Pressure { get; private set; }

    /// <summary>Is the supply valve open?</summary>
    public bool SupplyOpen { get; private set; }

    /// <summary>The transmitter's output. Spanned 0–10 bar, 4–20 mA, by
    /// default; never engineering units (see the class summary).</summary>
    public AnalogSignal PressureSignal { get; } =
        new("pressure", 0.0f, 10.0f, -1.0f, 40.0f, 0.5f) { Mode = AnalogSignalMode.Ma4To20 };

    /// <summary>The two signal types this transmitter can be wired as, in
    /// dropdown order.</summary>
    private static readonly AnalogSignalMode[] RawModes = { AnalogSignalMode.Ma4To20, AnalogSignalMode.S7Raw };

    /// <summary>The loop current the transmitter is driving, mA, as its own
    /// display shows it. Zero with the wire cut.</summary>
    public float LoopCurrent
    {
        get
        {
            if (PressureSignal.WireBroken) return 0.0f;
            float span = PressureSignal.RangeMax - PressureSignal.RangeMin;
            float fraction = span > 1e-6f ? (Pressure - PressureSignal.RangeMin) / span : 0.0f;
            // The same limits the card applies: a transmitter saturates a
            // little past either end of its range rather than driving forever.
            return PressureSignal.Mode == AnalogSignalMode.Ma4To20
                ? Mathf.Clamp(4.0f + 16.0f * fraction, 3.8f, 20.5f)
                : Mathf.Clamp(20.0f * fraction, 0.0f, 23.5f);
        }
    }

    private const float VesselRadius = 0.14f;
    private const float VesselHeight = 0.36f;
    private const float VesselBaseY = -0.18f;
    private float VesselTopY => VesselBaseY + VesselHeight;

    private Label3D _gaugeReadout = null!;
    private Label3D _transmitterDisplay = null!;
    private StandardMaterial3D _supplyLedMat = null!;

    public override void _Ready()
    {
        var vesselMat = new StandardMaterial3D
        {
            AlbedoColor = new Color(0.30f, 0.44f, 0.58f),
            Metallic = 0.30f,
            Roughness = 0.50f,
        };
        var pipeMat = new StandardMaterial3D
        {
            AlbedoColor = new Color(0.62f, 0.64f, 0.68f),
            Metallic = 0.35f,
            Roughness = 0.50f,
        };
        var trimMat = new StandardMaterial3D
        {
            AlbedoColor = new Color(0.18f, 0.20f, 0.22f),
            Metallic = 0.45f,
            Roughness = 0.50f,
        };

        // The receiver: an upright cylinder with dished ends, on three legs.
        float midY = VesselBaseY + VesselHeight / 2.0f;
        AddChild(new MeshInstance3D
        {
            Name = "ReceiverShell",
            Mesh = new CylinderMesh { TopRadius = VesselRadius, BottomRadius = VesselRadius, Height = VesselHeight },
            MaterialOverride = vesselMat,
            Position = new Vector3(0, midY, 0),
        });
        foreach (float y in new[] { VesselBaseY, VesselTopY })
        {
            AddChild(new MeshInstance3D
            {
                Name = "DishedEnd",
                Mesh = new SphereMesh { Radius = VesselRadius, Height = VesselRadius * 0.9f },
                MaterialOverride = vesselMat,
                Position = new Vector3(0, y, 0),
            });
        }
        float legTop = VesselBaseY - 0.02f;
        float legHeight = legTop + PartLayout.FloorDrop;
        for (int i = 0; i < 3; i++)
        {
            float angle = Mathf.Tau * i / 3.0f + 0.5f;
            AddChild(new MeshInstance3D
            {
                Name = "Leg",
                Mesh = new BoxMesh { Size = new Vector3(0.03f, legHeight, 0.03f) },
                MaterialOverride = trimMat,
                Position = new Vector3(Mathf.Cos(angle) * 0.10f, legTop - legHeight / 2.0f, Mathf.Sin(angle) * 0.10f),
            });
        }

        // Inlet from the compressor main, on the left, through the supply
        // solenoid valve; outlet to the plant on the right.
        const float pipeY = 0.02f;
        AddPipe("InletPipe", -VesselRadius - 0.13f, pipeY, 0.26f, pipeMat);
        AddPipe("OutletPipe", VesselRadius + 0.10f, pipeY, 0.20f, pipeMat);

        AddChild(new MeshInstance3D
        {
            Name = "SupplyValve",
            Mesh = new BoxMesh { Size = new Vector3(0.07f, 0.06f, 0.06f) },
            MaterialOverride = trimMat,
            Position = new Vector3(-VesselRadius - 0.13f, pipeY, 0),
        });
        AddChild(new MeshInstance3D
        {
            Name = "SupplyCoil",
            Mesh = new CylinderMesh { TopRadius = 0.022f, BottomRadius = 0.022f, Height = 0.06f },
            MaterialOverride = new StandardMaterial3D { AlbedoColor = new Color(0.12f, 0.12f, 0.13f), Roughness = 0.6f },
            Position = new Vector3(-VesselRadius - 0.13f, pipeY + 0.06f, 0),
        });
        _supplyLedMat = new StandardMaterial3D
        {
            AlbedoColor = new Color(0.30f, 0.20f, 0.06f),
            EmissionEnabled = true,
            Emission = new Color(1.0f, 0.65f, 0.10f),
            EmissionEnergyMultiplier = 0.0f,
        };
        AddChild(new MeshInstance3D
        {
            Name = "SupplyLed",
            Mesh = new SphereMesh { Radius = 0.01f, Height = 0.02f },
            MaterialOverride = _supplyLedMat,
            Position = new Vector3(-VesselRadius - 0.13f, pipeY + 0.10f, 0),
        });

        // The local gauge on the shell, facing out of the front.
        AddChild(new MeshInstance3D
        {
            Name = "GaugeFace",
            Mesh = new CylinderMesh { TopRadius = 0.05f, BottomRadius = 0.05f, Height = 0.02f },
            MaterialOverride = new StandardMaterial3D { AlbedoColor = new Color(0.94f, 0.94f, 0.92f), Roughness = 0.4f },
            Position = new Vector3(0, midY + 0.06f, VesselRadius + 0.02f),
            Rotation = new Vector3(Mathf.Pi / 2.0f, 0, 0),
        });
        _gaugeReadout = new Label3D
        {
            Name = "GaugeReadout",
            Text = "0.0 bar",
            Position = new Vector3(0, midY + 0.06f, VesselRadius + 0.035f),
            // Sized to sit inside the 0.10 m dial, not across it.
            FontSize = 32,
            PixelSize = 0.0006f,
            Modulate = new Color(0.10f, 0.10f, 0.12f),
        };
        AddChild(_gaugeReadout);

        // The transmitter on the top head: a stem, the process connection and
        // the round housing with its display.
        AddChild(new MeshInstance3D
        {
            Name = "TransmitterStem",
            Mesh = new CylinderMesh { TopRadius = 0.012f, BottomRadius = 0.012f, Height = 0.10f },
            MaterialOverride = pipeMat,
            Position = new Vector3(0, VesselTopY + 0.08f, 0),
        });
        AddChild(new MeshInstance3D
        {
            Name = "TransmitterHousing",
            Mesh = new CylinderMesh { TopRadius = 0.045f, BottomRadius = 0.045f, Height = 0.07f },
            MaterialOverride = new StandardMaterial3D
            {
                AlbedoColor = new Color(0.20f, 0.42f, 0.70f),
                Metallic = 0.25f,
                Roughness = 0.45f,
            },
            Position = new Vector3(0, VesselTopY + 0.16f, 0),
            Rotation = new Vector3(Mathf.Pi / 2.0f, 0, 0),
        });
        _transmitterDisplay = new Label3D
        {
            Name = "TransmitterDisplay",
            Text = "4.00 mA",
            Position = new Vector3(0, VesselTopY + 0.16f, 0.04f),
            FontSize = 32,
            PixelSize = 0.0005f,
            Modulate = new Color(0.60f, 1.0f, 0.70f),
            OutlineSize = 4,
        };
        AddChild(_transmitterDisplay);

        ApplyVisuals();
    }

    private void AddPipe(string name, float x, float y, float length, StandardMaterial3D mat) =>
        AddChild(new MeshInstance3D
        {
            Name = name,
            Mesh = new CylinderMesh { TopRadius = 0.018f, BottomRadius = 0.018f, Height = length },
            MaterialOverride = mat,
            Position = new Vector3(x, y, 0),
            Rotation = new Vector3(0, 0, Mathf.Pi / 2.0f),
        });

    /// <summary>
    /// One tick of the receiver.
    /// </summary>
    /// <param name="supply">Is the supply valve open?</param>
    public void Step(bool supply, float delta)
    {
        SupplyOpen = supply;

        // Inflow only while the main is above the receiver: the non-return
        // valve stops the receiver emptying back into a lower main.
        float inflow = supply ? SupplyRate * Mathf.Max(SupplyPressure - Pressure, 0.0f) : 0.0f;
        float outflow = ConsumptionRate * Mathf.Max(ConsumptionPercent, 0.0f) / 100.0f * Pressure;
        Pressure = Mathf.Max(Pressure + (inflow - outflow) * delta, 0.0f);
    }

    public override void _Process(double delta) => ApplyVisuals();

    private void ApplyVisuals()
    {
        if (_gaugeReadout is null) return;
        string gauge = $"{Pressure:0.0} bar";
        if (_gaugeReadout.Text != gauge) _gaugeReadout.Text = gauge;

        // A loop-powered transmitter with its loop cut has no power, so its
        // display goes blank rather than showing zero.
        string display = PressureSignal.WireBroken ? "" : $"{LoopCurrent:0.00} mA";
        if (_transmitterDisplay.Text != display) _transmitterDisplay.Text = display;

        _supplyLedMat.AlbedoColor = SupplyOpen ? new Color(1.0f, 0.65f, 0.10f) : new Color(0.30f, 0.20f, 0.06f);
        _supplyLedMat.EmissionEnergyMultiplier = SupplyOpen ? 3.0f : 0.0f;
    }

    // ---------- IPart (HP-34)

    public void DeclareTags(PartTagBuilder tags) => tags
        .Bit("supply", $"Receiver {tags.Index} Supply Valve", TagKind.Output)
        .Analog(PressureSignal, $"Receiver {tags.Index} Pressure", "bar");

    public void CaptureSettings(PartSettings settings)
    {
        settings.Put("supply_pressure", SupplyPressure);
        settings.Put("consumption", ConsumptionPercent);
        PressureSignal.Capture(settings);
    }

    public void ApplySettings(PartSettings settings)
    {
        if (settings.Number("supply_pressure") is { } supply) SupplyPressure = supply;
        if (settings.Number("consumption") is { } consumption) ConsumptionPercent = consumption;
        PressureSignal.Apply(settings);
        // Raw only: a file asking for engineering units gets the 4-20 mA loop
        // this device actually is, never bar.
        if (!PressureSignal.IsRaw) PressureSignal.Mode = AnalogSignalMode.Ma4To20;
    }

    public void StepPart(PartTick tick)
    {
        Step(tick.Bit("supply"), tick.Dt);
        PressureSignal.Write(tick, Pressure);
    }

    /// <summary>The signal and range rows are this part's own rather than
    /// <see cref="AnalogSignal.Describe"/>, which offers engineering units
    /// first -- the one choice this transmitter exists not to have.</summary>
    public void DescribeControls(IPartInspector ui)
    {
        ui.Slider("Supply (bar)", SupplyPressure, 1.0f, 12.0f, 0.1f, value => SupplyPressure = value);
        ui.Slider("Consumption (%)", ConsumptionPercent, 0.0f, 100.0f, 1.0f, value => ConsumptionPercent = value);

        var labels = new string[RawModes.Length];
        int selected = 0;
        for (int i = 0; i < RawModes.Length; i++)
        {
            labels[i] = AnalogSignal.LabelFor(RawModes[i]);
            if (RawModes[i] == PressureSignal.Mode) selected = i;
        }
        ui.Choice("Signal", labels, selected, index => PressureSignal.Mode = RawModes[index]);
        // Gauge pressure: -1 bar is a perfect vacuum, so no range starts lower.
        ui.Slider("Range Min", PressureSignal.RangeMin, -1.0f, 40.0f, 0.5f, value => PressureSignal.RangeMin = value);
        ui.Slider("Range Max", PressureSignal.RangeMax, -1.0f, 40.0f, 0.5f, value => PressureSignal.RangeMax = value);
    }

    public void ResetPart(PartReset reset)
    {
        Pressure = 0.0f;
        SupplyOpen = false;
        PressureSignal.Reset(reset, 0.0);
    }

    /// <summary>IP-07. The supply valve open has to charge the receiver, and
    /// the transmitter has to report it rising -- in whatever units its signal
    /// mode publishes, counts by default.</summary>
    public PartProbe? Probe => new(
        "`pressure` rising above where it rested",
        r => r.Number("pressure") > r.AtStart("pressure"))
    {
        Drive = PartProbe.Drives(("supply", true)),
    };
}
