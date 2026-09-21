using FactoryForge.TagBus;
using Godot;

namespace FactoryForge.Parts;

/// <summary>
/// A ducted fan that takes heat out of a nearby heating station (LP-04).
///
/// The heating station is a good teaching plant and it has one gap: it can only
/// heat. Overshoot can only be waited out, so the interesting half of process
/// control — deciding *which* actuator to use, and never both at once — cannot
/// be taught with it. A fan gives the same first-order equation a second
/// actuator pulling the other way, which is split-range control, and it is the
/// first place a student meets a deadband that exists for a reason rather than
/// as a tuning nicety.
///
/// The coupling is deliberately physical rather than symbolic: the fan finds
/// the <see cref="HeatingStation"/>s within <see cref="Reach"/> and adds to
/// their loss term, so cooling enters the *same* differential equation the
/// element drives. One plant, two actuators. Blowing at nothing costs the same
/// as blowing at something and achieves nothing, which is the honest behaviour
/// and the reason the part has a reach at all.
/// </summary>
public partial class CoolingFan : Node3D, IPart
{
    /// <summary>How far the airflow carries, in metres. Stations beyond this
    /// are not cooled at all — a fan pointed at the wrong machine is a real
    /// commissioning mistake and it should look like one.</summary>
    [Export] public float Reach { get; set; } = 1.2f;

    /// <summary>Extra loss coefficient at 100 % airflow, in the same units as
    /// <see cref="HeatingStation.LossRate"/>. Tuned so full cooling roughly
    /// triples the station's natural loss: enough to pull an overshoot down in
    /// a time worth watching, not so much that the plant stops being slow.
    /// </summary>
    [Export] public float CoolingRate { get; set; } = 0.60f;

    /// <summary>Airflow ramp, percent per second. A fan has inertia, so the
    /// reference and the reading disagree while it is changing — the same
    /// lesson the VFD conveyor teaches, on a part that is easy to see.</summary>
    [Export] public float SpinUpRate { get; set; } = 45.0f;

    private const float DuctRadius = 0.17f;
    private const float DuctDepth = 0.14f;

    /// <summary>Fan axis height above the part origin. Level with a heating
    /// station's plate, so the air goes where it looks like it goes.</summary>
    private const float AxisY = 0.20f;

    private Node3D _blades = null!;
    private StandardMaterial3D? _faultLampMat;
    private Label3D _readout = null!;

    private float _blade;
    private float _drawnFlow = float.NaN;

    /// <summary>Airflow reference last commanded, 0–100 %.</summary>
    public float CommandedSpeed { get; private set; }

    /// <summary>Airflow actually being delivered, 0–100 %. Lags the reference
    /// through the ramp and falls to zero when the motor fails, whatever the
    /// reference still reads.</summary>
    public float Airflow { get; private set; }

    /// <summary>Motor failed. The reference is still accepted and still
    /// reported; no air arrives, the blade coasts down, and the only honest
    /// reading is the airflow — and, further downstream, the temperature that
    /// stops coming back down.</summary>
    public bool IsFaulted { get; private set; }

    public override void _Ready()
    {
        var frameMat = new StandardMaterial3D
        {
            AlbedoColor = new Color(0.20f, 0.22f, 0.25f),
            Metallic = 0.45f,
            Roughness = 0.50f,
        };

        float postHeight = PartLayout.FloorDrop + AxisY;
        AddChild(new MeshInstance3D
        {
            Name = "Post",
            Mesh = new BoxMesh { Size = new Vector3(0.07f, postHeight, 0.07f) },
            MaterialOverride = frameMat,
            Position = new Vector3(0, AxisY - postHeight / 2.0f, 0),
        });
        AddChild(new MeshInstance3D
        {
            Name = "BaseFoot",
            Mesh = new BoxMesh { Size = new Vector3(0.28f, 0.03f, 0.28f) },
            MaterialOverride = frameMat,
            Position = new Vector3(0, -PartLayout.FloorDrop, 0),
        });

        // The duct. Open both ends, so it reads as a shroud around a fan rather
        // than as a drum: the cylinder is drawn with its faces culled away.
        AddChild(new MeshInstance3D
        {
            Name = "Duct",
            Mesh = new CylinderMesh
            {
                TopRadius = DuctRadius,
                BottomRadius = DuctRadius,
                Height = DuctDepth,
                CapTop = false,
                CapBottom = false,
            },
            MaterialOverride = new StandardMaterial3D
            {
                AlbedoColor = new Color(0.32f, 0.34f, 0.38f),
                Metallic = 0.50f,
                Roughness = 0.40f,
                CullMode = BaseMaterial3D.CullModeEnum.Disabled,
            },
            // Laid on its side so it blows down -Z, the direction the part
            // faces when it is dropped from the palette unrotated.
            Position = new Vector3(0, AxisY, 0),
            Basis = new Basis(Vector3.Right, Mathf.Pi / 2),
        });

        // Hub and blades. Their own node, so the spin is a rotation about the
        // fan axis and nothing else moves with it.
        _blades = new Node3D { Name = "FanBlades", Position = new Vector3(0, AxisY, 0) };
        AddChild(_blades);

        var bladeMat = new StandardMaterial3D
        {
            AlbedoColor = new Color(0.60f, 0.63f, 0.68f),
            Metallic = 0.65f,
            Roughness = 0.35f,
        };
        _blades.AddChild(new MeshInstance3D
        {
            Name = "Hub",
            Mesh = new CylinderMesh { TopRadius = 0.045f, BottomRadius = 0.045f, Height = 0.07f },
            MaterialOverride = new StandardMaterial3D
            {
                AlbedoColor = new Color(0.42f, 0.44f, 0.48f),
                Metallic = 0.55f,
                Roughness = 0.40f,
            },
            Basis = new Basis(Vector3.Right, Mathf.Pi / 2),
        });

        const int bladeCount = 5;
        for (int i = 0; i < bladeCount; i++)
        {
            float bearing = Mathf.Tau * i / bladeCount;
            var blade = new MeshInstance3D
            {
                Name = $"Blade{i}",
                // Long in Y, which is the *radial* direction before the swing:
                // the blade reaches out from the hub toward the duct wall. Wide
                // in X (tangential), thin in Z (along the axis). Getting those
                // three the wrong way round makes five flat plates that stick
                // out along the axis instead of a fan.
                Mesh = new BoxMesh { Size = new Vector3(0.075f, DuctRadius * 0.82f, 0.012f) },
                MaterialOverride = bladeMat,
            };
            // Pitched about its own radial axis so the blade bites the air
            // instead of being a flat paddle, then swung round the fan axis
            // (+Z, once the duct is laid over).
            var swing = new Basis(Vector3.Back, bearing);
            var pitch = new Basis(Vector3.Up, Mathf.DegToRad(30.0f));
            blade.Basis = swing * pitch;
            blade.Position = swing * new Vector3(0, DuctRadius * 0.52f, 0);
            _blades.AddChild(blade);
        }

        // Finger guard across the outlet. Purely cosmetic, and the single
        // cheapest thing that makes an axial fan read as industrial.
        for (int i = 0; i < 6; i++)
        {
            float bearing = Mathf.Pi * i / 6.0f;
            var bar = new MeshInstance3D
            {
                Name = $"GuardBar{i}",
                Mesh = new BoxMesh { Size = new Vector3(DuctRadius * 2.0f, 0.008f, 0.008f) },
                MaterialOverride = frameMat,
                Position = new Vector3(0, AxisY, -DuctDepth / 2.0f - 0.01f),
            };
            bar.Basis = new Basis(Vector3.Back, bearing);
            AddChild(bar);
        }

        // A small plate on top of the duct with the airflow on it, rather than
        // a number floating in the air beside the machine.
        float plateY = AxisY + DuctRadius + 0.045f;
        AddChild(new MeshInstance3D
        {
            Name = "FlowPlate",
            Mesh = new BoxMesh { Size = new Vector3(0.17f, 0.055f, 0.015f) },
            MaterialOverride = new StandardMaterial3D
            {
                AlbedoColor = new Color(0.10f, 0.11f, 0.13f),
                Roughness = 0.55f,
            },
            Position = new Vector3(0, plateY, -DuctDepth / 2.0f - 0.012f),
        });
        _readout = new Label3D
        {
            Name = "FlowReadout",
            Text = "0 %",
            FontSize = 44,
            PixelSize = 0.0009f,
            Modulate = new Color(0.55f, 0.85f, 0.98f),
            Billboard = BaseMaterial3D.BillboardModeEnum.Disabled,
            Position = new Vector3(0, plateY, -DuctDepth / 2.0f - 0.021f),
            RotationDegrees = new Vector3(0, 180, 0),
        };
        AddChild(_readout);

        BuildFaultLamp();
    }

    private void BuildFaultLamp()
    {
        _faultLampMat = new StandardMaterial3D
        {
            AlbedoColor = new Color(0.30f, 0.06f, 0.06f),
            Metallic = 0.10f,
            Roughness = 0.35f,
        };
        // On a stalk off the duct's shoulder, like every other faultable part,
        // rather than floating unattached beside it.
        var mount = new Vector3(DuctRadius * 0.72f, AxisY + DuctRadius * 0.72f, 0);
        const float stalk = 0.06f;
        AddChild(new MeshInstance3D
        {
            Name = "DriveFaultStalk",
            Mesh = new CylinderMesh { TopRadius = 0.008f, BottomRadius = 0.010f, Height = stalk },
            MaterialOverride = new StandardMaterial3D
            {
                AlbedoColor = new Color(0.22f, 0.23f, 0.25f),
                Metallic = 0.40f,
                Roughness = 0.50f,
            },
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

    /// <summary>How often the fan looks for stations again, in seconds. They
    /// do not move while the scene runs, but one can be placed after the fan
    /// is, and a scan per tick per fan is work nobody asked for.</summary>
    private const float RescanInterval = 1.0f;

    private readonly System.Collections.Generic.List<HeatingStation> _stations = new();
    private float _rescanTimer;

    /// <summary>
    /// Integrate one tick: ramp the airflow toward the reference, spin the
    /// blade at whatever the fan is really delivering, and hand the cooling to
    /// every station in reach.
    ///
    /// The cooling is *offered*, not applied — <see cref="HeatingStation"/>
    /// accumulates it and consumes it in its own <c>Step</c>. Parts are
    /// dispatched in placement order, so a fan placed before its station would
    /// otherwise apply cooling to a temperature that had not been integrated
    /// yet on some ticks and had on others, and the plant's behaviour would
    /// depend on the order somebody happened to click.
    /// </summary>
    public void Step(bool run, float speedPercent, float delta)
    {
        CommandedSpeed = Mathf.Clamp(speedPercent, 0.0f, 100.0f);

        float target = IsFaulted || !run ? 0.0f : CommandedSpeed;
        Airflow = Mathf.MoveToward(Airflow, target, SpinUpRate * delta);

        _blade += Airflow / 100.0f * 22.0f * delta;

        _rescanTimer -= delta;
        if (_rescanTimer <= 0.0f)
        {
            _rescanTimer = RescanInterval;
            FindStations();
        }

        if (Airflow > 0.01f)
        {
            float extraLoss = CoolingRate * Airflow / 100.0f;
            foreach (var station in _stations)
            {
                if (IsInstanceValid(station)) station.AddCooling(extraLoss);
            }
        }

        Apply();
    }

    public void ResetFan()
    {
        CommandedSpeed = 0.0f;
        Airflow = 0.0f;
        Apply();
    }

    private void Apply()
    {
        if (_blades is not null) _blades.Rotation = new Vector3(0, 0, _blade);

        if (_readout is null) return;
        if (Mathf.IsEqualApprox(Airflow, _drawnFlow)) return;
        _drawnFlow = Airflow;
        _readout.Text = $"{Airflow:0} %";
    }

    private void FindStations()
    {
        _stations.Clear();

        Node? root = GetTree()?.Root ?? GetParent();
        if (root is null) return;

        Vector3 here = GlobalPosition;
        var stack = new System.Collections.Generic.Stack<Node>();
        stack.Push(root);
        while (stack.Count > 0)
        {
            Node node = stack.Pop();
            foreach (Node child in node.GetChildren()) stack.Push(child);

            if (node is HeatingStation station
                && station.GlobalPosition.DistanceTo(here) <= Reach)
            {
                _stations.Add(station);
            }
        }
    }

    // ---------- IPart (HP-34)

    public void DeclareTags(PartTagBuilder tags) => tags
        .Bit("run", $"Fan {tags.Index} Run", TagKind.Output)
        .Float("speed", $"Fan {tags.Index} Speed Ref (%)", TagKind.Output)
        .Float("airflow", $"Fan {tags.Index} Airflow (%)", TagKind.Input)
        .Bit("fault", $"Fan {tags.Index} Motor Fault", TagKind.Input);

    public void CaptureSettings(PartSettings settings)
    {
        settings.Put("reach", Reach);
        settings.Put("cooling_rate", CoolingRate);
        settings.Put("spin_up_rate", SpinUpRate);
    }

    public void ApplySettings(PartSettings settings)
    {
        if (settings.Number("reach") is { } reach) Reach = reach;
        if (settings.Number("cooling_rate") is { } rate) CoolingRate = rate;
        if (settings.Number("spin_up_rate") is { } spinUp) SpinUpRate = spinUp;
    }

    public void StepPart(PartTick tick)
    {
        if (tick.TryBit("fault", out bool faulted)) SetFaulted(faulted);

        Step(tick.Bit("run"), tick.Number("speed"), tick.Dt);
        tick.Write("airflow", (double)Airflow);
    }

    public void DescribeControls(IPartInspector ui)
    {
        ui.Slider("Reach (m)", Reach, 0.3f, 4.0f, 0.1f, value => Reach = value);
        ui.Slider("Cooling (/s/degC)", CoolingRate, 0.05f, 3.0f, 0.05f,
                  value => CoolingRate = value);
        ui.Slider("Spin-up (%/s)", SpinUpRate, 5.0f, 200.0f, 5.0f, value => SpinUpRate = value);
    }

    public void ResetPart(PartReset reset)
    {
        ResetFan();
        reset.Write("airflow", 0.0);
    }

    public PartOperation? Operation => new("fan", "run");

    /// <summary>A fan needs an enable *and* a reference, so a click has to move
    /// both or the part looks broken: the speed would go to 100 % and nothing
    /// would turn. Driven off `run`, since that is the one that decides.</summary>
    public void Operate(PartOperate op)
    {
        if (!op.TryBit("run", out bool running)) return;
        bool on = !running;
        op.Force("run", on);
        op.Force("speed", on ? 100.0 : 0.0);
    }
}
