using System.Collections.Generic;
using FactoryForge.TagBus;
using Godot;

namespace FactoryForge.Parts;

/// <summary>
/// A variable-speed dosing pump that really puts liquid into a tank.
///
/// The library's analog plants were a tank and a heater, and the tank's only
/// inflow was its own fill valve — so the one analog loop a student could build
/// was single-loop level control, and the whole family of problems that begins
/// with "the inner loop is faster than the outer one" was out of reach.
///
/// A pump changes that because it gives the tank a <em>flow</em>, which is a
/// measurable thing in its own right (<see cref="FlowMeter"/>) and a much
/// faster process than the level it feeds. Level PID setting the setpoint of a
/// flow PID that drives this pump's speed is the textbook cascade, and until
/// there was a pump there was nothing to cascade onto.
///
/// It delivers into every <see cref="LevelTank"/> in reach, the same way
/// <see cref="CoolingFan"/> cools every heating station in reach — offered and
/// consumed by the tank, never applied directly, because parts are dispatched in
/// placement order and a pump placed before its tank would otherwise land on one
/// side of the tank's integration and a pump placed after it on the other.
/// </summary>
public partial class DosingPump : Node3D, IPart
{
    /// <summary>Delivery at 100 % speed, litres per minute.</summary>
    [Export] public float RatedFlow { get; set; } = 40.0f;

    /// <summary>How far the discharge reaches, metres. A pump is plumbed to a
    /// tank, and "in reach" stands in for the pipe.</summary>
    [Export] public float Reach { get; set; } = 1.6f;

    /// <summary>How fast the flow follows the speed reference, percent per
    /// second. Much faster than a tank fills, which is exactly what makes the
    /// cascade worth teaching.</summary>
    [Export] public float RampRate { get; set; } = 120.0f;

    /// <summary>What the controller asked for, percent.</summary>
    public float CommandedSpeed { get; private set; }

    /// <summary>What the pump is actually delivering, litres per minute.</summary>
    public float Flow { get; private set; }

    /// <summary>A failed pump reports its command and delivers nothing; only
    /// the flow measurement gives it away. That is the analog failure and it is
    /// nastier to diagnose than a stopped motor, which is the point of having
    /// it.</summary>
    public bool IsFaulted { get; private set; }

    private float _percent;
    private float _rescanTimer;
    private float _impeller;
    private readonly List<LevelTank> _tanks = new();

    private Node3D _impellerNode = null!;
    private StandardMaterial3D? _faultLampMat;
    private Label3D _readout = null!;

    private const float BodyY = 0.16f;
    private const float RescanInterval = 1.0f;

    public override void _Ready()
    {
        var caseMat = new StandardMaterial3D
        {
            AlbedoColor = new Color(0.22f, 0.45f, 0.62f),
            Metallic = 0.50f,
            Roughness = 0.40f,
        };
        var trimMat = new StandardMaterial3D
        {
            AlbedoColor = new Color(0.20f, 0.22f, 0.25f),
            Metallic = 0.45f,
            Roughness = 0.45f,
        };
        var steelMat = new StandardMaterial3D
        {
            AlbedoColor = new Color(0.72f, 0.74f, 0.78f),
            Metallic = 0.75f,
            Roughness = 0.30f,
        };

        AddChild(new MeshInstance3D
        {
            Name = "Baseplate",
            Mesh = new BoxMesh { Size = new Vector3(0.30f, 0.03f, 0.18f) },
            MaterialOverride = trimMat,
            Position = new Vector3(0, -PartLayout.FloorDrop + 0.015f, 0),
        });
        AddChild(new MeshInstance3D
        {
            Name = "MotorBody",
            Mesh = new CylinderMesh { TopRadius = 0.07f, BottomRadius = 0.07f, Height = 0.18f },
            MaterialOverride = caseMat,
            Position = new Vector3(-0.07f, BodyY, 0),
            Rotation = new Vector3(0, 0, Mathf.Pi / 2.0f),
        });
        AddChild(new MeshInstance3D
        {
            Name = "PumpHead",
            Mesh = new CylinderMesh { TopRadius = 0.075f, BottomRadius = 0.075f, Height = 0.06f },
            MaterialOverride = steelMat,
            Position = new Vector3(0.08f, BodyY, 0),
            Rotation = new Vector3(0, 0, Mathf.Pi / 2.0f),
        });
        AddChild(new MeshInstance3D
        {
            Name = "DischargePipe",
            Mesh = new CylinderMesh { TopRadius = 0.020f, BottomRadius = 0.020f, Height = 0.16f },
            MaterialOverride = steelMat,
            Position = new Vector3(0.08f, BodyY + 0.10f, 0),
        });
        AddChild(new MeshInstance3D
        {
            Name = "Column",
            Mesh = new CylinderMesh
            {
                TopRadius = 0.026f, BottomRadius = 0.032f, Height = BodyY + PartLayout.FloorDrop,
            },
            MaterialOverride = trimMat,
            Position = new Vector3(-0.07f, (BodyY - PartLayout.FloorDrop) / 2.0f, 0),
        });

        // A fan on the motor's tail, turning at whatever the pump is really
        // delivering. A commanded pump that is not turning has to look
        // different from one that is, or a failed pump is invisible.
        _impellerNode = new Node3D { Name = "CoolingFanBlades", Position = new Vector3(-0.17f, BodyY, 0) };
        for (int i = 0; i < 4; i++)
        {
            _impellerNode.AddChild(new MeshInstance3D
            {
                Mesh = new BoxMesh { Size = new Vector3(0.012f, 0.09f, 0.02f) },
                MaterialOverride = steelMat,
                Rotation = new Vector3(Mathf.Pi / 2.0f * i, 0, 0),
            });
        }
        AddChild(_impellerNode);

        BuildFaultLamp(trimMat);

        _readout = new Label3D
        {
            Name = "FlowReadout",
            Text = "0.0 L/min",
            Position = new Vector3(0, BodyY + 0.30f, 0),
            Billboard = BaseMaterial3D.BillboardModeEnum.Enabled,
            FontSize = 78,
            PixelSize = 0.0015f,
            Modulate = new Color(0.65f, 0.88f, 1.0f),
        };
        AddChild(_readout);
    }

    private void BuildFaultLamp(StandardMaterial3D trimMat)
    {
        _faultLampMat = new StandardMaterial3D
        {
            AlbedoColor = new Color(0.30f, 0.06f, 0.06f),
            Metallic = 0.10f,
            Roughness = 0.35f,
        };
        var mount = new Vector3(-0.07f, BodyY + 0.09f, 0);
        const float stalk = 0.06f;

        AddChild(new MeshInstance3D
        {
            Name = "DriveFaultStalk",
            Mesh = new CylinderMesh { TopRadius = 0.007f, BottomRadius = 0.009f, Height = stalk },
            MaterialOverride = trimMat,
            Position = mount + new Vector3(0, stalk / 2, 0),
        });
        AddChild(new MeshInstance3D
        {
            Name = "DriveFaultLamp",
            Mesh = new SphereMesh { Radius = 0.034f, Height = 0.068f },
            MaterialOverride = _faultLampMat,
            Position = mount + new Vector3(0, stalk + 0.024f, 0),
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

    public void Step(bool run, float speedPercent, float delta)
    {
        CommandedSpeed = Mathf.Clamp(speedPercent, 0.0f, 100.0f);

        float target = IsFaulted || !run ? 0.0f : CommandedSpeed;
        _percent = Mathf.MoveToward(_percent, target, Mathf.Max(RampRate, 0.0f) * delta);
        Flow = Mathf.Max(RatedFlow, 0.0f) * _percent / 100.0f;

        _impeller += _percent / 100.0f * 26.0f * delta;

        _rescanTimer -= delta;
        if (_rescanTimer <= 0.0f)
        {
            _rescanTimer = RescanInterval;
            FindTanks();
        }

        if (Flow > 0.001f)
        {
            // Litres per second, because the tank knows its own capacity and
            // this does not. Offered, not applied -- see the class comment.
            float litresPerSecond = Flow / 60.0f;
            foreach (var tank in _tanks)
            {
                if (IsInstanceValid(tank)) tank.AddInflow(litresPerSecond);
            }
        }

        Apply();
    }

    private void Apply()
    {
        if (_impellerNode is not null) _impellerNode.Rotation = new Vector3(_impeller, 0, 0);

        if (_readout is null) return;
        string text = $"{Flow:0.0} L/min";
        if (_readout.Text != text) _readout.Text = text;
    }

    public void ResetPump()
    {
        SetFaulted(false);
        CommandedSpeed = 0.0f;
        _percent = 0.0f;
        Flow = 0.0f;
        Apply();
    }

    private void FindTanks()
    {
        _tanks.Clear();

        Node? root = GetTree()?.Root ?? GetParent();
        if (root is null) return;

        Vector3 here = GlobalPosition;
        foreach (var node in Walk(root))
        {
            if (node is LevelTank tank && tank.GlobalPosition.DistanceTo(here) <= Reach)
                _tanks.Add(tank);
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
        .Bit("run", $"Pump {tags.Index} Run", TagKind.Output)
        .Float("speed", $"Pump {tags.Index} Speed Ref (%)", TagKind.Output)
        // The measurement, not the command. A failed pump reports the command
        // it was given and delivers nothing; this is the tag that disagrees.
        .Float("flow", $"Pump {tags.Index} Flow (L/min)", TagKind.Input)
        .Bit("fault", $"Pump {tags.Index} Motor Fault", TagKind.Input);

    public void CaptureSettings(PartSettings settings)
    {
        settings.Put("rated_flow", RatedFlow);
        settings.Put("reach", Reach);
        settings.Put("ramp_rate", RampRate);
    }

    public void ApplySettings(PartSettings settings)
    {
        if (settings.Number("rated_flow") is { } rated) RatedFlow = rated;
        if (settings.Number("reach") is { } reach) Reach = reach;
        if (settings.Number("ramp_rate") is { } ramp) RampRate = ramp;
    }

    public void StepPart(PartTick tick)
    {
        if (tick.TryBit("fault", out bool faulted)) SetFaulted(faulted);

        Step(tick.Bit("run"), tick.Number("speed"), tick.Dt);
        tick.Write("flow", (double)Flow);
    }

    public void DescribeControls(IPartInspector ui)
    {
        ui.Slider("Rated Flow (L/min)", RatedFlow, 1.0f, 300.0f, 1.0f, value => RatedFlow = value);
        ui.Slider("Reach (m)", Reach, 0.3f, 5.0f, 0.1f, value => Reach = value);
        ui.Slider("Ramp (%/s)", RampRate, 5.0f, 400.0f, 5.0f, value => RampRate = value);
    }

    public void ResetPart(PartReset reset)
    {
        ResetPump();
        reset.Write("flow", 0.0);
    }

    public PartOperation? Operation => new("dosing pump", "run");

    /// <summary>A pump needs an enable <em>and</em> a reference, so a click has
    /// to move both or the part looks broken: the speed would go to 100 % and
    /// nothing would flow. The same reasoning as the cooling fan's.</summary>
    public void Operate(PartOperate op)
    {
        if (!op.TryBit("run", out bool running)) return;
        bool on = !running;
        op.Force("run", on);
        op.Force("speed", on ? 100.0 : 0.0);
    }
}
