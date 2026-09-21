using FactoryForge.TagBus;
using Godot;

namespace FactoryForge.Parts;

/// <summary>
/// A heated plate with real thermal inertia (CP-07).
///
/// The level tank was the library's first analog process and it has a flaw as a
/// teaching plant: it is symmetric. You can fill and you can drain, at rates
/// you choose, so a lazy controller still converges. A thermal process is not
/// symmetric — you can heat as fast as the element allows, and you can only
/// cool at whatever rate the room takes the heat away. That asymmetry, plus a
/// genuine first-order lag, is what makes integral action necessary rather than
/// merely nice, and it is why pure proportional control leaves a standing
/// offset here that a student can measure.
///
///     dT/dt = (P·k_heat − (T − T_ambient)·k_loss) / thermal_mass
///
/// A failed element is the honest failure for this part: <c>heater</c> still
/// reads whatever the controller commanded, and the temperature falls anyway.
/// The output cannot tell you; only the measurement can.
/// </summary>
public partial class HeatingStation : Node3D, IPart
{
    /// <summary>Heat input at 100 % power, in °C·mass per second.
    ///
    /// Tuned together with <see cref="LossRate"/> and <see cref="ThermalMass"/>
    /// so the plant has a time constant of about twenty seconds and tops out
    /// near 320 °C at full power. That is slow enough that the lag is the
    /// obvious feature of the process and fast enough to sit through: an
    /// earlier tuning had a 55-second time constant, which is realistic and
    /// makes the first useful experiment take four minutes.</summary>
    [Export] public float HeaterPower { get; set; } = 90.0f;

    /// <summary>Loss coefficient to ambient, per second per °C. This is the
    /// term that makes pure proportional control leave a standing offset:
    /// holding a temperature needs a standing output, and P alone can only
    /// produce one from a standing error.</summary>
    [Export] public float LossRate { get; set; } = 0.30f;

    /// <summary>Thermal mass. Larger is slower and more forgiving.</summary>
    [Export] public float ThermalMass { get; set; } = 6.0f;

    [Export] public float Ambient { get; set; } = 20.0f;

    /// <summary>The band `.attemp` reports against, in °C.</summary>
    [Export] public float TargetTemp { get; set; } = 180.0f;
    [Export] public float Tolerance { get; set; } = 3.0f;

    /// <summary>Above this the plate glows visibly; the colour ramp runs from
    /// here to <see cref="GlowFullTemp"/>.</summary>
    private const float GlowStartTemp = 60.0f;
    private const float GlowFullTemp = 320.0f;

    private const float PlateY = 0.16f;

    /// <summary>Process temperature, °C. This is the measured variable.</summary>
    public float Temperature { get; private set; } = 20.0f;

    /// <summary>Heater power last commanded, 0–100 %. Kept so a faulted station
    /// can show a live command next to a falling temperature, which is the
    /// whole diagnosis.</summary>
    public float CommandedPower { get; private set; }

    public bool AtTemperature => Mathf.Abs(Temperature - TargetTemp) <= Tolerance;

    /// <summary>Element failed. The command is still accepted and still
    /// reported; no heat arrives.</summary>
    public bool IsFaulted { get; private set; }

    private StandardMaterial3D _plateMat = null!;
    private OmniLight3D _heatGlow = null!;
    private Label3D _readout = null!;
    private StandardMaterial3D? _faultLampMat;

    public override void _Ready()
    {
        Temperature = Ambient;

        var frameMat = new StandardMaterial3D
        {
            AlbedoColor = new Color(0.22f, 0.23f, 0.26f),
            Metallic = 0.45f,
            Roughness = 0.45f,
        };

        // Pedestal down to the floor, the way every free-standing part here
        // supports itself from the work plane.
        float pedestalHeight = PartLayout.FloorDrop + PlateY - 0.03f;
        AddChild(new MeshInstance3D
        {
            Name = "Pedestal",
            Mesh = new BoxMesh { Size = new Vector3(0.34f, pedestalHeight, 0.34f) },
            MaterialOverride = frameMat,
            Position = new Vector3(0, PlateY - 0.03f - pedestalHeight / 2.0f, 0),
        });
        AddChild(new MeshInstance3D
        {
            Name = "BaseFoot",
            Mesh = new BoxMesh { Size = new Vector3(0.44f, 0.03f, 0.44f) },
            MaterialOverride = frameMat,
            Position = new Vector3(0, -PartLayout.FloorDrop, 0),
        });

        // The plate itself. Its emission is the readout most people will
        // actually use: a hot plate is orange from across the scene.
        _plateMat = new StandardMaterial3D
        {
            AlbedoColor = new Color(0.14f, 0.13f, 0.13f),
            Metallic = 0.55f,
            Roughness = 0.45f,
            EmissionEnabled = true,
            Emission = new Color(1.0f, 0.28f, 0.05f),
            EmissionEnergyMultiplier = 0.0f,
        };
        AddChild(new MeshInstance3D
        {
            Name = "HeaterPlate",
            Mesh = new BoxMesh { Size = new Vector3(0.40f, 0.05f, 0.40f) },
            MaterialOverride = _plateMat,
            Position = new Vector3(0, PlateY, 0),
        });

        // Vented hood on two posts, so heat visibly has somewhere to go and the
        // part reads as a station rather than as a slab.
        foreach (int side in new[] { -1, 1 })
        {
            AddChild(new MeshInstance3D
            {
                Name = side < 0 ? "HoodPostLeft" : "HoodPostRight",
                Mesh = new BoxMesh { Size = new Vector3(0.04f, 0.42f, 0.04f) },
                MaterialOverride = frameMat,
                Position = new Vector3(side * 0.20f, PlateY + 0.23f, -0.20f),
            });
        }
        AddChild(new MeshInstance3D
        {
            Name = "Hood",
            Mesh = new BoxMesh { Size = new Vector3(0.46f, 0.05f, 0.30f) },
            MaterialOverride = frameMat,
            Position = new Vector3(0, PlateY + 0.46f, -0.10f),
        });

        _heatGlow = new OmniLight3D
        {
            Name = "HeatGlow",
            LightColor = new Color(1.0f, 0.42f, 0.12f),
            LightEnergy = 0.0f,
            OmniRange = 1.4f,
            ShadowEnabled = false,
            Position = new Vector3(0, PlateY + 0.10f, 0),
        };
        AddChild(_heatGlow);

        _readout = new Label3D
        {
            Name = "TempReadout",
            Text = "20 °C",
            FontSize = 36,
            PixelSize = 0.0020f,
            Modulate = new Color(1.0f, 0.72f, 0.35f),
            Position = new Vector3(0, PlateY + 0.40f, 0.02f),
            Billboard = BaseMaterial3D.BillboardModeEnum.Enabled,
        };
        AddChild(_readout);

        BuildFaultLamp();
        ApplyTemperature();
    }

    private void BuildFaultLamp()
    {
        _faultLampMat = new StandardMaterial3D
        {
            AlbedoColor = new Color(0.30f, 0.06f, 0.06f),
            Metallic = 0.10f,
            Roughness = 0.35f,
        };
        AddChild(new MeshInstance3D
        {
            Name = "DriveFaultLamp",
            Mesh = new SphereMesh { Radius = 0.038f, Height = 0.076f },
            MaterialOverride = _faultLampMat,
            Position = new Vector3(0.20f, PlateY + 0.52f, -0.10f),
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
    /// Integrate one tick. <paramref name="delta"/> is scaled simulation time,
    /// so the plant obeys pause and the time-scale control like everything
    /// else — and a 4× run really does heat four times as fast, which is the
    /// point of having a time scale at all on a process this slow.
    /// </summary>
    public void Step(float powerPercent, float delta)
    {
        CommandedPower = Mathf.Clamp(powerPercent, 0.0f, 100.0f);

        float applied = IsFaulted ? 0.0f : CommandedPower;
        float input = HeaterPower * applied / 100.0f;
        // Forced cooling enters the *same* loss term the room does, so a split
        // range controller is driving one plant with two actuators rather than
        // two plants that happen to share a tag. Consumed here and zeroed, so a
        // fan that stops offering it stops cooling on the next tick and not
        // whenever somebody remembers to clear a flag.
        float loss = (Temperature - Ambient) * (LossRate + _offeredCooling);
        _offeredCooling = 0.0f;

        Temperature += (input - loss) / Mathf.Max(ThermalMass, 0.01f) * delta;
        // Physically the plate cannot go below ambient with no cooling, and a
        // clamp here is cheaper than an integrator that has drifted negative
        // being interpreted as a sensor fault.
        Temperature = Mathf.Max(Temperature, Ambient);

        ApplyTemperature();
    }

    /// <summary>Extra loss coefficient offered by forced cooling this tick, in
    /// the same units as <see cref="LossRate"/>.</summary>
    private float _offeredCooling;

    /// <summary>
    /// Offer forced cooling for this tick (LP-04).
    ///
    /// Accumulated rather than applied, and consumed by <see cref="Step"/>,
    /// because parts are dispatched in placement order: a fan placed before its
    /// station would land on one side of the integration and a fan placed after
    /// it on the other, and the plant would behave differently depending on the
    /// order somebody clicked. Adding rather than assigning also means two fans
    /// on one station cool it twice, which is what two fans do.
    /// </summary>
    public void AddCooling(float extraLossRate)
    {
        if (extraLossRate <= 0.0f) return;
        // Clamped, because the consumer is <see cref="Step"/> and Step only
        // runs while the station's own `heater` tag exists. A station placed as
        // a *view* of tags something else owns is never stepped, so without a
        // ceiling a fan beside one would pile up an unbounded loss coefficient
        // and then dump the lot on the first tick that did run. Ten is already
        // thirty times the plant's own loss to ambient.
        _offeredCooling = Mathf.Min(_offeredCooling + extraLossRate, 10.0f);
    }

    public void ResetTemperature()
    {
        Temperature = Ambient;
        CommandedPower = 0.0f;
        _offeredCooling = 0.0f;
        ApplyTemperature();
    }

    private float _drawnTemp = float.NaN;

    private void ApplyTemperature()
    {
        if (_plateMat is null) return;
        // Same guard the tank uses: this runs every physics tick and touching a
        // material and a Label3D every one of them is work nobody asked for.
        if (Mathf.IsEqualApprox(Temperature, _drawnTemp)) return;
        _drawnTemp = Temperature;

        float glow = Mathf.Clamp((Temperature - GlowStartTemp) / (GlowFullTemp - GlowStartTemp), 0.0f, 1.0f);
        _plateMat.EmissionEnergyMultiplier = glow * 3.2f;
        // Deep red at the bottom of the range through orange at the top, the
        // way steel actually colours as it heats.
        _plateMat.Emission = new Color(1.0f, 0.10f + glow * 0.45f, 0.02f + glow * 0.10f);

        if (_heatGlow is not null) _heatGlow.LightEnergy = glow * 1.8f;
        if (_readout is not null) _readout.Text = $"{Temperature:0} °C";
    }

    // ---------- IPart (HP-34)

    public void DeclareTags(PartTagBuilder tags) => tags
        .Float("heater", $"Heater {tags.Index} Power (%)", TagKind.Output)
        .Float("temperature", $"Heater {tags.Index} Temperature (C)", TagKind.Input)
        .Bit("attemp", $"Heater {tags.Index} At Temperature", TagKind.Input)
        // A failed element still accepts and reports its command; only the
        // measurement gives it away (CP-07).
        .Bit("fault", $"Heater {tags.Index} Element Fault", TagKind.Input);

    public void CaptureSettings(PartSettings settings)
    {
        settings.Put("heater_power", HeaterPower);
        settings.Put("loss_rate", LossRate);
        settings.Put("thermal_mass", ThermalMass);
        settings.Put("ambient", Ambient);
        settings.Put("target_temp", TargetTemp);
        settings.Put("tolerance", Tolerance);
    }

    public void ApplySettings(PartSettings settings)
    {
        if (settings.Number("heater_power") is { } power) HeaterPower = power;
        if (settings.Number("loss_rate") is { } loss) LossRate = loss;
        if (settings.Number("thermal_mass") is { } mass) ThermalMass = mass;
        if (settings.Number("ambient") is { } ambient) Ambient = ambient;
        if (settings.Number("target_temp") is { } target) TargetTemp = target;
        if (settings.Number("tolerance") is { } band) Tolerance = band;
    }

    public void StepPart(PartTick tick)
    {
        if (!tick.Has("heater")) return;

        if (tick.TryBit("fault", out bool faulted)) SetFaulted(faulted);

        // dt is scaled simulation time, so the plant obeys pause and the time
        // scale — the same rule the tank follows, and it matters more here
        // because the time constant is a minute rather than seconds.
        Step(tick.Number("heater"), tick.Dt);
        tick.Write("temperature", (double)Temperature);
        tick.Write("attemp", AtTemperature);
    }

    public void DescribeControls(IPartInspector ui)
    {
        ui.Slider("Heater Power", HeaterPower, 5.0f, 200.0f, 1.0f, value => HeaterPower = value);
        ui.Slider("Thermal Mass", ThermalMass, 1.0f, 60.0f, 1.0f, value => ThermalMass = value);
        ui.Slider("Loss Rate (/s/degC)", LossRate, 0.02f, 2.0f, 0.02f, value => LossRate = value);
        ui.Slider("Target (degC)", TargetTemp, 20.0f, 400.0f, 1.0f, value => TargetTemp = value);
        ui.Slider("Tolerance (degC)", Tolerance, 0.5f, 30.0f, 0.5f, value => Tolerance = value);
    }

    /// <summary>A run's accumulated heat, not a machine somebody built (LP-12).
    /// Until this existed, a reset on the heat-treat scene left the plate at
    /// whatever temperature the last run reached, and the next run started from
    /// a place no experiment could reproduce.</summary>
    public void ResetPart(PartReset reset)
    {
        ResetTemperature();
        reset.Write("temperature", (double)Temperature);
        reset.Write("attemp", AtTemperature);
    }

    /// <summary>The one output is a percentage, so a click drives it fully on or
    /// fully off, exactly as a click on a tank valve does.</summary>
    public PartOperation? Operation => new("heater", "heater");

    public void Operate(PartOperate op) => op.ToggleAnalog("heater");
}
