using FactoryForge.TagBus;
using Godot;

namespace FactoryForge.Parts;

/// <summary>
/// An on/off process valve: a quarter-turn valve on a spring-return pneumatic
/// actuator, piloted by one solenoid, with a limit-switch box reporting
/// <em>opened</em> and <em>closed</em> (IP-17).
///
/// What this part teaches is <b>discrepancy monitoring</b>: the program commands
/// one thing and reads back another, and the two are allowed to disagree for as
/// long as the valve takes to travel and not a moment longer. Every real plant
/// runs this check on every valve it owns — command open, start a timer, and if
/// <c>opened</c> has not arrived when it runs out, the valve has failed.
///
/// <b>Travel is real.</b> The valve takes <see cref="TravelTime"/> to go from
/// one end to the other, and for all of it <em>both</em> feedbacks are false:
/// <c>!closed</c> does not mean open, it means "not closed yet". A program that
/// infers one feedback from the other passes every test until the valve stops
/// half way.
///
/// <b>Spring return.</b> One solenoid, energised to open. Take the command away
/// and the spring closes the valve — the fail position of an ordinary
/// normally-closed process valve, and the reason a single bit is the whole
/// command.
///
/// <b>Stuck.</b> The <c>fault</c> input (the same injectable fault every drive
/// here has, raised by the toolbar's fault tool or a force) seizes the stem
/// where it stands. The feedback then never follows the command: stuck closed
/// it keeps reporting <c>closed</c> while commanded open; stuck mid-stroke it
/// reports neither, forever. That is the failure the discrepancy timer exists
/// to catch, and it can only be caught from the feedback.
/// </summary>
public partial class SolenoidValve : Node3D, IPart
{
    /// <summary>End-to-end stroke time, seconds. Quarter-turn actuators on
    /// small process valves take one to three seconds.</summary>
    [Export] public float TravelTime { get; set; } = 2.0f;

    /// <summary>How close to an end the stem has to be for that limit switch
    /// to make, as a fraction of the stroke (about two degrees of a quarter
    /// turn).</summary>
    public const float SwitchBand = 0.02f;

    /// <summary>Stem position, 0 = closed, 1 = open.</summary>
    public float Opening { get; private set; }

    public bool IsOpened => Opening >= 1.0f - SwitchBand;
    public bool IsClosed => Opening <= SwitchBand;

    /// <summary>Is the solenoid energised?</summary>
    public bool Commanded { get; private set; }

    /// <summary>Seized: the stem holds wherever it was.</summary>
    public bool IsFaulted { get; private set; }

    private const float PipeY = 0.12f;
    private const float PipeRadius = 0.035f;
    private const float PipeLength = 0.80f;
    private const float ActuatorY = PipeY + 0.17f;

    private Node3D _indicator = null!;
    private StandardMaterial3D _coilLedMat = null!;
    private StandardMaterial3D _openedLampMat = null!;
    private StandardMaterial3D _closedLampMat = null!;
    private StandardMaterial3D? _faultLampMat;

    public override void _Ready()
    {
        var pipeMat = new StandardMaterial3D
        {
            AlbedoColor = new Color(0.62f, 0.64f, 0.68f),
            Metallic = 0.35f,
            Roughness = 0.50f,
        };
        var bodyMat = new StandardMaterial3D
        {
            // Cast body in the dark red a process valve is usually painted.
            AlbedoColor = new Color(0.50f, 0.12f, 0.10f),
            Metallic = 0.30f,
            Roughness = 0.50f,
        };
        var actuatorMat = new StandardMaterial3D
        {
            AlbedoColor = new Color(0.20f, 0.36f, 0.62f),
            Metallic = 0.30f,
            Roughness = 0.45f,
        };
        var trimMat = new StandardMaterial3D
        {
            AlbedoColor = new Color(0.18f, 0.20f, 0.22f),
            Metallic = 0.45f,
            Roughness = 0.50f,
        };

        // The pipe run through the valve, along X.
        AddChild(new MeshInstance3D
        {
            Name = "Pipe",
            Mesh = new CylinderMesh { TopRadius = PipeRadius, BottomRadius = PipeRadius, Height = PipeLength },
            MaterialOverride = pipeMat,
            Position = new Vector3(0, PipeY, 0),
            Rotation = new Vector3(0, 0, Mathf.Pi / 2.0f),
        });
        foreach (float x in new[] { -0.085f, 0.085f })
        {
            AddChild(new MeshInstance3D
            {
                Name = "Flange",
                Mesh = new CylinderMesh { TopRadius = 0.065f, BottomRadius = 0.065f, Height = 0.02f },
                MaterialOverride = pipeMat,
                Position = new Vector3(x, PipeY, 0),
                Rotation = new Vector3(0, 0, Mathf.Pi / 2.0f),
            });
        }
        AddChild(new MeshInstance3D
        {
            Name = "ValveBody",
            Mesh = new SphereMesh { Radius = 0.075f, Height = 0.15f },
            MaterialOverride = bodyMat,
            Position = new Vector3(0, PipeY, 0),
        });

        // Pipe supports down to the floor at each end.
        foreach (float x in new[] { -0.32f, 0.32f })
        {
            float height = PipeY - PipeRadius + PartLayout.FloorDrop;
            AddChild(new MeshInstance3D
            {
                Name = "PipeSupport",
                Mesh = new BoxMesh { Size = new Vector3(0.04f, height, 0.08f) },
                MaterialOverride = trimMat,
                Position = new Vector3(x, PipeY - PipeRadius - height / 2.0f, 0),
            });
        }

        // Yoke, actuator and limit-switch box stacked on the stem.
        AddChild(new MeshInstance3D
        {
            Name = "Yoke",
            Mesh = new CylinderMesh { TopRadius = 0.018f, BottomRadius = 0.018f, Height = 0.08f },
            MaterialOverride = trimMat,
            Position = new Vector3(0, PipeY + 0.10f, 0),
        });
        AddChild(new MeshInstance3D
        {
            Name = "Actuator",
            Mesh = new BoxMesh { Size = new Vector3(0.24f, 0.09f, 0.10f) },
            MaterialOverride = actuatorMat,
            Position = new Vector3(0, ActuatorY, 0),
        });
        foreach (float x in new[] { -0.125f, 0.125f })
        {
            AddChild(new MeshInstance3D
            {
                Name = "ActuatorEndCap",
                Mesh = new BoxMesh { Size = new Vector3(0.012f, 0.10f, 0.11f) },
                MaterialOverride = trimMat,
                Position = new Vector3(x, ActuatorY, 0),
            });
        }

        const float boxY = ActuatorY + 0.075f;
        AddChild(new MeshInstance3D
        {
            Name = "SwitchBox",
            Mesh = new BoxMesh { Size = new Vector3(0.10f, 0.06f, 0.09f) },
            MaterialOverride = new StandardMaterial3D
            {
                AlbedoColor = new Color(0.86f, 0.82f, 0.70f),
                Roughness = 0.55f,
            },
            Position = new Vector3(0, boxY, 0),
        });

        // The beacon-style position indicator on top of the switch box: a
        // pointer that turns with the stem, across the pipe when shut and along
        // it when open, exactly as on a real one.
        _indicator = new Node3D { Name = "PositionIndicator", Position = new Vector3(0, boxY + 0.04f, 0) };
        _indicator.AddChild(new MeshInstance3D
        {
            Name = "IndicatorDome",
            Mesh = new CylinderMesh { TopRadius = 0.034f, BottomRadius = 0.038f, Height = 0.02f },
            MaterialOverride = trimMat,
        });
        _indicator.AddChild(new MeshInstance3D
        {
            Name = "IndicatorPointer",
            // Longer than the dome is wide, so it overhangs both sides and its
            // angle reads from any side of the valve.
            Mesh = new BoxMesh { Size = new Vector3(0.014f, 0.012f, 0.12f) },
            MaterialOverride = new StandardMaterial3D
            {
                AlbedoColor = new Color(0.98f, 0.80f, 0.10f),
                EmissionEnabled = true,
                Emission = new Color(0.98f, 0.80f, 0.10f),
                EmissionEnergyMultiplier = 0.3f,
            },
            Position = new Vector3(0, 0.014f, 0),
        });
        AddChild(_indicator);

        // The two feedback lamps on the front of the switch box.
        _openedLampMat = Lamp();
        _closedLampMat = Lamp();
        AddLamp("OpenedLamp", _openedLampMat, new Vector3(-0.025f, boxY, 0.047f));
        AddLamp("ClosedLamp", _closedLampMat, new Vector3(0.025f, boxY, 0.047f));

        // The pilot solenoid on the side of the actuator, with a coil LED: the
        // first thing to look at when a valve does not move is whether it was
        // ever told to.
        AddChild(new MeshInstance3D
        {
            Name = "PilotValve",
            Mesh = new BoxMesh { Size = new Vector3(0.06f, 0.05f, 0.04f) },
            MaterialOverride = trimMat,
            Position = new Vector3(0.06f, ActuatorY, 0.07f),
        });
        AddChild(new MeshInstance3D
        {
            Name = "SolenoidCoil",
            Mesh = new CylinderMesh { TopRadius = 0.018f, BottomRadius = 0.018f, Height = 0.05f },
            MaterialOverride = new StandardMaterial3D
            {
                AlbedoColor = new Color(0.12f, 0.12f, 0.13f),
                Roughness = 0.6f,
            },
            Position = new Vector3(0.06f, ActuatorY, 0.105f),
            Rotation = new Vector3(Mathf.Pi / 2.0f, 0, 0),
        });
        _coilLedMat = new StandardMaterial3D
        {
            AlbedoColor = new Color(0.30f, 0.20f, 0.06f),
            EmissionEnabled = true,
            Emission = new Color(1.0f, 0.65f, 0.10f),
            EmissionEnergyMultiplier = 0.0f,
        };
        AddChild(new MeshInstance3D
        {
            Name = "CoilLed",
            Mesh = new SphereMesh { Radius = 0.009f, Height = 0.018f },
            MaterialOverride = _coilLedMat,
            Position = new Vector3(0.06f, ActuatorY + 0.03f, 0.09f),
        });

        BuildFaultLamp(trimMat);
        ApplyVisuals();
    }

    private static StandardMaterial3D Lamp() => new()
    {
        AlbedoColor = new Color(0.06f, 0.24f, 0.10f),
        Metallic = 0.10f,
        Roughness = 0.35f,
    };

    private void AddLamp(string name, StandardMaterial3D mat, Vector3 position) =>
        AddChild(new MeshInstance3D
        {
            Name = name,
            Mesh = new SphereMesh { Radius = 0.011f, Height = 0.022f },
            MaterialOverride = mat,
            Position = position,
        });

    private void BuildFaultLamp(StandardMaterial3D trimMat)
    {
        _faultLampMat = new StandardMaterial3D
        {
            AlbedoColor = new Color(0.30f, 0.06f, 0.06f),
            Metallic = 0.10f,
            Roughness = 0.35f,
        };
        var mount = new Vector3(-0.10f, ActuatorY + 0.045f, 0);
        const float stalk = 0.10f;
        AddChild(new MeshInstance3D
        {
            Name = "DriveFaultStalk",
            Mesh = new CylinderMesh { TopRadius = 0.007f, BottomRadius = 0.009f, Height = stalk },
            MaterialOverride = trimMat,
            Position = mount + new Vector3(0, stalk / 2.0f, 0),
        });
        AddChild(new MeshInstance3D
        {
            Name = "DriveFaultLamp",
            Mesh = new SphereMesh { Radius = 0.03f, Height = 0.06f },
            MaterialOverride = _faultLampMat,
            Position = mount + new Vector3(0, stalk + 0.02f, 0),
        });
    }

    public void SetFaulted(bool faulted)
    {
        if (faulted == IsFaulted && _faultLampMat is not null) return;
        IsFaulted = faulted;

        if (_faultLampMat is null) return;
        _faultLampMat.AlbedoColor = faulted ? new Color(1.0f, 0.15f, 0.12f) : new Color(0.30f, 0.06f, 0.06f);
        _faultLampMat.EmissionEnabled = faulted;
        _faultLampMat.Emission = faulted ? new Color(1.0f, 0.15f, 0.12f) : Colors.Black;
        _faultLampMat.EmissionEnergyMultiplier = faulted ? 2.4f : 0.0f;
    }

    /// <summary>
    /// One tick of the actuator.
    /// </summary>
    /// <param name="open">Is the solenoid energised?</param>
    public void Step(bool open, float delta)
    {
        Commanded = open;

        // The fault wins over the command, in one place: a seized stem goes
        // nowhere, whatever the solenoid is doing.
        if (!IsFaulted)
        {
            float rate = 1.0f / Mathf.Max(TravelTime, 0.05f);
            Opening = Mathf.MoveToward(Opening, open ? 1.0f : 0.0f, rate * delta);
        }
        ApplyVisuals();
    }

    private void ApplyVisuals()
    {
        if (_indicator is null) return;
        _indicator.Rotation = new Vector3(0, Opening * Mathf.Pi / 2.0f, 0);
        SetLamp(_openedLampMat, IsOpened, new Color(0.25f, 1.0f, 0.35f), new Color(0.06f, 0.24f, 0.10f));
        SetLamp(_closedLampMat, IsClosed, new Color(0.25f, 1.0f, 0.35f), new Color(0.06f, 0.24f, 0.10f));
        SetLamp(_coilLedMat, Commanded, new Color(1.0f, 0.65f, 0.10f), new Color(0.30f, 0.20f, 0.06f));
    }

    private static void SetLamp(StandardMaterial3D? mat, bool on, Color lit, Color dark)
    {
        if (mat is null) return;
        mat.AlbedoColor = on ? lit : dark;
        mat.EmissionEnabled = on;
        mat.Emission = on ? lit : Colors.Black;
        mat.EmissionEnergyMultiplier = on ? 2.2f : 0.0f;
    }

    // ---------- IPart (HP-34)

    public void DeclareTags(PartTagBuilder tags) => tags
        .Bit("open", $"Valve {tags.Index} Solenoid (Open)", TagKind.Output)
        .Bit("opened", $"Valve {tags.Index} Opened Feedback", TagKind.Input)
        // Parked shut, so the scene starts in the state the geometry shows.
        .Bit("closed", $"Valve {tags.Index} Closed Feedback", TagKind.Input, initial: true)
        .Bit("fault", $"Valve {tags.Index} Stuck", TagKind.Input);

    public void CaptureSettings(PartSettings settings) => settings.Put("travel_time", TravelTime);

    public void ApplySettings(PartSettings settings)
    {
        if (settings.Number("travel_time") is { } travel) TravelTime = travel;
    }

    public void StepPart(PartTick tick)
    {
        if (tick.TryBit("fault", out bool faulted)) SetFaulted(faulted);
        Step(tick.Bit("open"), tick.Dt);
        tick.Write("opened", IsOpened);
        tick.Write("closed", IsClosed);
    }

    public void DescribeControls(IPartInspector ui) =>
        ui.Slider("Travel Time (s)", TravelTime, 0.1f, 10.0f, 0.1f, value => TravelTime = value);

    public void ResetPart(PartReset reset)
    {
        SetFaulted(false);
        Opening = 0.0f;
        Commanded = false;
        ApplyVisuals();
        reset.Write("opened", false);
        reset.Write("closed", true);
    }

    public PartOperation? Operation => new("valve", "open");

    public void Operate(PartOperate op) => op.ToggleBit("open");
}
