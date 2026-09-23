using FactoryForge.TagBus;
using Godot;

namespace FactoryForge.Parts;

/// <summary>
/// A star-delta starter: main, star and delta contactors with their auxiliary
/// contacts, a motor-protective circuit breaker, a thermal overload, and the
/// motor itself (IP-17).
///
/// The classic exam circuit. The motor starts with its windings in star, which
/// puts 1/√3 of line voltage across each and cuts starting current and torque
/// to a third of direct-on-line; once it is near speed the star contactor drops
/// out and the delta contactor puts full voltage across the windings. <b>The
/// changeover timing is the PLC's job</b> — there is no timer in here.
///
/// <b>Star and delta together is a short circuit.</b> With both contactors
/// closed the delta contactor feeds the three phases into the winding ends and
/// the star contactor shorts those same three points together. That is a
/// three-phase short across the supply whether or not the main contactor is in,
/// and the breaker's magnetic trip opens it instantly. The breaker then stays
/// open — <c>breaker</c> reads false — until somebody resets it by hand, which
/// in Run mode is a click on the starter. A program is not given a reset
/// output on purpose: an automatic re-close onto a fault is how switchgear gets
/// destroyed.
///
/// <b>It is contacts that short, not coils, and contacts are slow.</b> A
/// contactor takes <see cref="PullInTime"/> to close after its coil is
/// energised, and <see cref="DropOutTime"/> — release plus arcing — before its
/// contacts stop conducting after the coil is dropped. Opening is the slower of
/// the two, so a program that drops star and energises delta <em>in the same
/// scan</em> closes delta while star is still arcing, and trips the breaker.
/// Every manufacturer's star-delta timing relay puts a dead time of about 50 ms
/// between the two for exactly this reason. Wait for <c>staraux</c> to fall
/// before energising delta, or hold a dead time, and the changeover is clean.
///
/// <b>The motor.</b> A cage induction motor on a simplified torque-speed curve
/// (about 2x rated torque at standstill, 2.6x pull-out near 90 % speed, rated
/// torque at 4 % slip) and a load that is part constant and part fan-like.
/// Star divides torque and line current by three at any speed. So a lightly
/// loaded motor runs up in star to most of its speed and the changeover to
/// delta costs a modest current step; a heavily loaded one stalls part way in
/// star — its star pull-out torque is under rated — and changing over there
/// draws nearly the full direct-on-line current, which is the thing the
/// starter existed to avoid. Speed is published as a percentage of synchronous
/// speed; the line current is an analog input like the direct-on-line
/// starter's, and can be raw counts (IP-16).
///
/// <b>The overload</b> sits in the winding circuit after the main contactor, as
/// it is normally wired, so it is set at 0.58 x full-load amps (the winding
/// share of line current in delta) and trips on the same inverse-time curve as
/// <see cref="MotorStarter"/>. In star the winding carries the full line
/// current, so a motor left stalled in star trips it — eventually. Its contact
/// is in series with all three coils, so a trip drops every contactor.
/// </summary>
public partial class StarDeltaStarter : Node3D, IPart
{
    /// <summary>The motor's full-load line current, amps. The plate rating.
    /// Star-delta starting is for motors big enough for the inrush to matter.</summary>
    [Export] public float FullLoadAmps { get; set; } = 20.0f;

    /// <summary>Mechanical load as a percentage of the motor's rated torque. A
    /// property of the machine being driven, so a setting and not a tag.</summary>
    [Export] public float LoadPercent { get; set; } = 60.0f;

    /// <summary>Mechanical starting time, seconds: how long rated torque would
    /// take to accelerate the motor and its load to synchronous speed. The
    /// inertia of the machine, stated in the one unit that says how long a
    /// run-up takes.</summary>
    [Export] public float InertiaTime { get; set; } = 2.0f;

    /// <summary>Coil energised to main contacts closed, seconds. A small AC
    /// contactor closes in 10–40 ms.</summary>
    public const float PullInTime = 0.030f;

    /// <summary>Coil dropped to main contacts no longer conducting, seconds:
    /// opening delay plus arcing. Longer than <see cref="PullInTime"/>, which is
    /// the whole reason a changeover needs a dead time.</summary>
    public const float DropOutTime = 0.045f;

    /// <summary>Overload setting as a multiple of winding full-load current
    /// (itself 1/√3 of line full-load current).</summary>
    private const float OverloadSetting = 1.15f;

    /// <summary>Overload trip class, seconds at six times the setting.</summary>
    private const float OverloadClass = 10.0f;

    // ---- the motor's per-unit model
    private const float LockedRotorCurrent = 6.0f;   // x FLA, direct-on-line at standstill
    private const float MagnetisingCurrent = 0.35f;   // x FLA, at no load
    private const float CurrentKneeSlip = 0.24f;      // rotor current ~ slip below this
    private const float RatedSlip = 0.04f;
    private const float StartingTorque = 2.0f;        // x rated, direct-on-line at standstill
    private const float PullOutTorque = 2.6f;
    private const float PullOutSpeed = 0.85f;
    private const float FrictionTorque = 0.02f;
    private static readonly float Sqrt3 = Mathf.Sqrt(3.0f);

    /// <summary>
    /// One contactor, as the interval of simulation time its main contacts
    /// conduct.
    ///
    /// Kept in continuous time rather than as "closed this tick", because what
    /// trips the breaker is two intervals overlapping, and at the time-scale
    /// control's fast settings one physics tick is longer than the whole 15 ms
    /// overlap a same-scan changeover produces. Counted in ticks, the short
    /// would appear at normal speed and vanish at four times speed; counted in
    /// time, it is the same short at any speed. The coil is sampled once per
    /// tick, which is when the controller's command reaches the panel.
    /// </summary>
    private sealed class Contactor
    {
        public bool Coil;
        private double _from = double.PositiveInfinity;
        private double _until = double.PositiveInfinity;

        public double ConductsFrom => _from;
        public double ConductsUntil => _until;

        public bool ConductingAt(double t) => t >= _from && t < _until;

        public void Command(bool coil, double now)
        {
            if (coil == Coil) return;
            Coil = coil;
            if (coil)
            {
                // Re-energised while still dropping out: the armature never
                // left, so the contacts simply stay closed.
                if (ConductingAt(now)) _until = double.PositiveInfinity;
                else { _from = now + PullInTime; _until = double.PositiveInfinity; }
            }
            else if (_from <= now)
            {
                _until = now + DropOutTime;
            }
            else
            {
                // Dropped before it had pulled in: it never closes.
                _from = double.PositiveInfinity;
                _until = double.PositiveInfinity;
            }
        }

        public void Reset()
        {
            Coil = false;
            _from = double.PositiveInfinity;
            _until = double.PositiveInfinity;
        }
    }

    /// <summary>Did two contactors conduct at the same instant at any point in
    /// (<paramref name="t0"/>, <paramref name="t1"/>]?</summary>
    private static bool Overlapped(Contactor a, Contactor b, double t0, double t1)
    {
        double start = System.Math.Max(System.Math.Max(a.ConductsFrom, b.ConductsFrom), t0);
        double end = System.Math.Min(System.Math.Min(a.ConductsUntil, b.ConductsUntil), t1 + 1e-9);
        return start < end;
    }

    /// <summary>Simulation seconds since placement or reset: the time base the
    /// contactor intervals are written in.</summary>
    private double _clock;

    private readonly Contactor _main = new();
    private readonly Contactor _star = new();
    private readonly Contactor _delta = new();
    private readonly ThermalOverload _overload = new();

    public bool MainClosed => _main.ConductingAt(_clock);
    public bool StarClosed => _star.ConductingAt(_clock);
    public bool DeltaClosed => _delta.ConductingAt(_clock);

    /// <summary>Has the breaker's magnetic trip opened? Latched until a hand
    /// reset.</summary>
    public bool IsBreakerTripped { get; private set; }

    public bool IsOverloadTripped => _overload.IsTripped;

    /// <summary>Rotor speed as a fraction of synchronous, 0..1.</summary>
    public float Speed { get; private set; }

    /// <summary>Line current, amps.</summary>
    public float Current { get; private set; }

    /// <summary>How the line current reaches the PLC (IP-16). A 0–100 A
    /// transducer by default: the default 20 A motor runs at about a fifth of
    /// scale, its 39 A star inrush fits, and a changeover made too early —
    /// near the full 117 A direct-on-line inrush — overranges the card.</summary>
    public AnalogSignal CurrentSignal { get; } = new("current", 0.0f, 100.0f, -100.0f, 1000.0f, 1.0f);

    // ---- visuals
    private StandardMaterial3D _mainLampMat = null!;
    private StandardMaterial3D _starLampMat = null!;
    private StandardMaterial3D _deltaLampMat = null!;
    private StandardMaterial3D _breakerLampMat = null!;
    private StandardMaterial3D _overloadLampMat = null!;
    private Node3D _breakerHandle = null!;
    private Node3D _shaft = null!;
    private Label3D _readout = null!;

    private const float BoxWidth = 0.46f;
    private const float BoxHeight = 0.40f;
    private const float BoxDepth = 0.16f;
    private const float PanelY = 0.32f;
    private const float MotorX = 0.50f;
    private const float MotorAxisY = 0.02f;

    public override void _Ready()
    {
        var enclosureMat = new StandardMaterial3D
        {
            AlbedoColor = new Color(0.62f, 0.60f, 0.56f),
            Metallic = 0.35f,
            Roughness = 0.55f,
        };
        var trimMat = new StandardMaterial3D
        {
            AlbedoColor = new Color(0.16f, 0.17f, 0.19f),
            Metallic = 0.45f,
            Roughness = 0.45f,
        };
        var motorMat = new StandardMaterial3D
        {
            AlbedoColor = new Color(0.18f, 0.36f, 0.52f),
            Metallic = 0.35f,
            Roughness = 0.50f,
        };

        // The enclosure on its stand, like the direct-on-line starter's, only
        // wide enough for three contactors and a breaker.
        AddChild(new MeshInstance3D
        {
            Name = "Enclosure",
            Mesh = new BoxMesh { Size = new Vector3(BoxWidth, BoxHeight, BoxDepth) },
            MaterialOverride = enclosureMat,
            Position = new Vector3(0, PanelY, 0),
        });
        AddChild(new MeshInstance3D
        {
            Name = "Stand",
            Mesh = new CylinderMesh
            {
                TopRadius = 0.024f, BottomRadius = 0.030f,
                Height = PanelY - BoxHeight / 2.0f + PartLayout.FloorDrop,
            },
            MaterialOverride = trimMat,
            Position = new Vector3(0, (PanelY - BoxHeight / 2.0f - PartLayout.FloorDrop) / 2.0f, 0),
        });

        const float front = BoxDepth / 2.0f;
        const float rowY = PanelY - 0.05f;

        // The breaker, top left, with a rotary handle that turns to TRIP.
        AddChild(new MeshInstance3D
        {
            Name = "Breaker",
            Mesh = new BoxMesh { Size = new Vector3(0.08f, 0.10f, 0.05f) },
            MaterialOverride = trimMat,
            Position = new Vector3(-0.16f, PanelY + 0.10f, front + 0.025f),
        });
        _breakerHandle = new Node3D { Name = "BreakerHandle", Position = new Vector3(-0.16f, PanelY + 0.10f, front + 0.055f) };
        _breakerHandle.AddChild(new MeshInstance3D
        {
            Mesh = new BoxMesh { Size = new Vector3(0.016f, 0.06f, 0.014f) },
            MaterialOverride = new StandardMaterial3D { AlbedoColor = new Color(0.10f, 0.10f, 0.11f), Roughness = 0.5f },
        });
        AddChild(_breakerHandle);
        _breakerLampMat = Lamp(new Color(0.28f, 0.06f, 0.06f));
        AddChild(LampMesh("BreakerTrippedLamp", _breakerLampMat, new Vector3(-0.10f, PanelY + 0.13f, front + 0.01f)));
        _overloadLampMat = Lamp(new Color(0.28f, 0.06f, 0.06f));
        AddChild(LampMesh("OverloadLamp", _overloadLampMat, new Vector3(-0.10f, PanelY + 0.07f, front + 0.01f)));

        // Three contactors in a row, each with a lamp that lights while its
        // main contacts are closed, and its name above it.
        _mainLampMat = Lamp(new Color(0.06f, 0.24f, 0.10f));
        _starLampMat = Lamp(new Color(0.06f, 0.24f, 0.10f));
        _deltaLampMat = Lamp(new Color(0.06f, 0.24f, 0.10f));
        AddContactor("Main", -0.13f, rowY, _mainLampMat, trimMat);
        AddContactor("Star", 0.0f, rowY, _starLampMat, trimMat);
        AddContactor("Delta", 0.13f, rowY, _deltaLampMat, trimMat);

        // The motor beside the panel: a finned frame on feet, a fan cowl on the
        // back, and a shaft with a striped coupling so its speed can be seen.
        AddChild(new MeshInstance3D
        {
            Name = "MotorFrame",
            Mesh = new CylinderMesh { TopRadius = 0.10f, BottomRadius = 0.10f, Height = 0.26f },
            MaterialOverride = motorMat,
            Position = new Vector3(MotorX, MotorAxisY, 0),
            Rotation = new Vector3(0, 0, Mathf.Pi / 2.0f),
        });
        for (int i = 0; i < 5; i++)
        {
            AddChild(new MeshInstance3D
            {
                Name = "CoolingFin",
                Mesh = new CylinderMesh { TopRadius = 0.108f, BottomRadius = 0.108f, Height = 0.008f },
                MaterialOverride = motorMat,
                Position = new Vector3(MotorX - 0.10f + i * 0.05f, MotorAxisY, 0),
                Rotation = new Vector3(0, 0, Mathf.Pi / 2.0f),
            });
        }
        AddChild(new MeshInstance3D
        {
            Name = "FanCowl",
            Mesh = new CylinderMesh { TopRadius = 0.095f, BottomRadius = 0.105f, Height = 0.07f },
            MaterialOverride = trimMat,
            Position = new Vector3(MotorX - 0.165f, MotorAxisY, 0),
            Rotation = new Vector3(0, 0, Mathf.Pi / 2.0f),
        });
        AddChild(new MeshInstance3D
        {
            Name = "TerminalBox",
            Mesh = new BoxMesh { Size = new Vector3(0.09f, 0.05f, 0.09f) },
            MaterialOverride = motorMat,
            Position = new Vector3(MotorX, MotorAxisY + 0.12f, 0),
        });
        float footHeight = MotorAxisY - 0.10f + PartLayout.FloorDrop;
        AddChild(new MeshInstance3D
        {
            Name = "MotorPlinth",
            Mesh = new BoxMesh { Size = new Vector3(0.30f, footHeight, 0.20f) },
            MaterialOverride = trimMat,
            Position = new Vector3(MotorX, MotorAxisY - 0.10f - footHeight / 2.0f, 0),
        });

        _shaft = new Node3D { Name = "Shaft", Position = new Vector3(MotorX + 0.13f, MotorAxisY, 0) };
        _shaft.AddChild(new MeshInstance3D
        {
            Mesh = new CylinderMesh { TopRadius = 0.018f, BottomRadius = 0.018f, Height = 0.08f },
            MaterialOverride = new StandardMaterial3D { AlbedoColor = new Color(0.70f, 0.72f, 0.75f), Metallic = 0.5f, Roughness = 0.3f },
            Position = new Vector3(0.04f, 0, 0),
            Rotation = new Vector3(0, 0, Mathf.Pi / 2.0f),
        });
        _shaft.AddChild(new MeshInstance3D
        {
            Name = "Coupling",
            Mesh = new CylinderMesh { TopRadius = 0.05f, BottomRadius = 0.05f, Height = 0.03f },
            MaterialOverride = trimMat,
            Position = new Vector3(0.07f, 0, 0),
            Rotation = new Vector3(0, 0, Mathf.Pi / 2.0f),
        });
        _shaft.AddChild(new MeshInstance3D
        {
            Name = "CouplingStripe",
            Mesh = new BoxMesh { Size = new Vector3(0.032f, 0.09f, 0.014f) },
            MaterialOverride = new StandardMaterial3D { AlbedoColor = new Color(0.98f, 0.80f, 0.10f), Roughness = 0.4f },
            Position = new Vector3(0.07f, 0, 0),
        });
        AddChild(_shaft);

        // The ammeter, over the panel.
        _readout = new Label3D
        {
            Name = "AmmeterReadout",
            Text = "0.0 A",
            Position = new Vector3(0, PanelY + BoxHeight / 2.0f + 0.06f, 0),
            Billboard = BaseMaterial3D.BillboardModeEnum.Enabled,
            FontSize = 64,
            PixelSize = 0.0012f,
            Modulate = new Color(0.85f, 0.90f, 1.0f),
        };
        AddChild(_readout);

        ApplyVisuals();
    }

    private void AddContactor(string name, float x, float y, StandardMaterial3D lamp, StandardMaterial3D trim)
    {
        const float front = BoxDepth / 2.0f;
        AddChild(new MeshInstance3D
        {
            Name = name + "Contactor",
            Mesh = new BoxMesh { Size = new Vector3(0.09f, 0.12f, 0.05f) },
            MaterialOverride = trim,
            Position = new Vector3(x, y, front + 0.025f),
        });
        AddChild(LampMesh(name + "Lamp", lamp, new Vector3(x, y + 0.03f, front + 0.052f)));
        AddChild(new Label3D
        {
            Name = name + "Legend",
            Text = name.ToUpperInvariant(),
            // On the contactor's own face below its lamp, small enough that the
            // three names stay three names.
            Position = new Vector3(x, y - 0.03f, front + 0.052f),
            FontSize = 32,
            PixelSize = 0.0007f,
            Modulate = new Color(0.95f, 0.95f, 0.95f),
            OutlineSize = 6,
        });
    }

    private static StandardMaterial3D Lamp(Color dark) => new()
    {
        AlbedoColor = dark,
        Metallic = 0.10f,
        Roughness = 0.35f,
    };

    private static MeshInstance3D LampMesh(string name, StandardMaterial3D mat, Vector3 position) => new()
    {
        Name = name,
        Mesh = new SphereMesh { Radius = 0.016f, Height = 0.032f },
        MaterialOverride = mat,
        Position = position,
    };

    // ---------- the model

    /// <summary>Direct-on-line (delta) torque at a speed, per unit of rated:
    /// rising from starting torque to pull-out, then the linear run near
    /// synchronous speed that passes through rated torque at rated slip.</summary>
    private static float DeltaTorque(float speed)
    {
        float lowSide = StartingTorque + (PullOutTorque - StartingTorque) * speed / PullOutSpeed;
        float highSide = (1.0f - speed) / RatedSlip;
        return Mathf.Max(Mathf.Min(lowSide, highSide), 0.0f);
    }

    /// <summary>Direct-on-line (delta) line current at a slip, per unit of
    /// full-load: magnetising current plus a rotor current that rises with slip
    /// and saturates at locked-rotor current.</summary>
    private static float DeltaCurrent(float slip)
    {
        float rotor = LockedRotorCurrent * slip / Mathf.Sqrt(slip * slip + CurrentKneeSlip * CurrentKneeSlip);
        return Mathf.Sqrt(MagnetisingCurrent * MagnetisingCurrent + rotor * rotor);
    }

    private float LoadTorque(float speed)
    {
        float relative = speed / (1.0f - RatedSlip);
        return Mathf.Max(LoadPercent, 0.0f) / 100.0f * (0.2f + 0.8f * relative * relative);
    }

    /// <summary>
    /// One tick of the starter and the motor.
    /// </summary>
    public void Step(bool mainCoil, bool starCoil, bool deltaCoil, float delta)
    {
        // The overload's contact is in series with every coil, so a tripped
        // overload drops all three whatever the program asks.
        bool coilsFed = !_overload.IsTripped;
        double t0 = _clock;
        _main.Command(mainCoil && coilsFed, t0);
        _star.Command(starCoil && coilsFed, t0);
        _delta.Command(deltaCoil && coilsFed, t0);
        _clock += delta;

        // The short. Star shorts the winding ends together; delta feeds the
        // supply into those same ends. Main is not involved at all -- and it is
        // the contacts that short, so what counts is whether the two
        // conduction intervals met anywhere in this tick, however briefly.
        if (!IsBreakerTripped && Overlapped(_star, _delta, t0, _clock))
        {
            IsBreakerTripped = true;
            GD.Print($"{Name}: star and delta contactors closed together -- "
                     + "short circuit, the breaker has tripped");
        }

        bool supplied = !IsBreakerTripped && MainClosed;
        bool star = supplied && StarClosed && !DeltaClosed;
        bool deltaRun = supplied && DeltaClosed && !StarClosed;

        float slip = 1.0f - Speed;
        float torque = 0.0f;
        float lineCurrent = 0.0f;
        if (deltaRun)
        {
            torque = DeltaTorque(Speed);
            lineCurrent = DeltaCurrent(slip);
        }
        else if (star)
        {
            // A third of the voltage-squared: a third of the torque, and a third
            // of the delta line current at the same speed.
            torque = DeltaTorque(Speed) / 3.0f;
            lineCurrent = DeltaCurrent(slip) / 3.0f;
        }

        float net = torque - LoadTorque(Speed) - FrictionTorque;
        Speed = Mathf.Clamp(Speed + net / Mathf.Max(InertiaTime, 0.05f) * delta, 0.0f, 1.0f);
        Current = lineCurrent * FullLoadAmps;

        // In delta the winding carries 1/√3 of the line current; in star it
        // carries all of it. The overload is in the winding circuit.
        float windingAmps = star ? Current : Current / Sqrt3;
        float tripAmps = FullLoadAmps / Sqrt3 * OverloadSetting;
        if (_overload.Step(windingAmps, tripAmps, OverloadClass, delta))
        {
            GD.Print($"{Name}: thermal overload tripped at {windingAmps:0.0} A in the winding");
        }

        ApplyVisuals();
    }

    /// <summary>The breaker's handle and the overload's reset button, as a
    /// person at the panel would do both. Closing a breaker onto a short that
    /// is still there trips it again on the next tick, which is correct.</summary>
    public void ResetBreaker()
    {
        IsBreakerTripped = false;
        _overload.Reset();
        ApplyVisuals();
    }

    private float _shaftAngle;

    public override void _Process(double delta)
    {
        if (_shaft is null) return;
        // Drawn slowed down: a real 4-pole motor turns at 24 rev/s, which at 60
        // frames a second strobes into looking stopped. 3 rev/s at synchronous
        // speed reads as "running, and this fast".
        _shaftAngle = Mathf.Wrap(_shaftAngle + Speed * 3.0f * Mathf.Tau * (float)delta, 0.0f, Mathf.Tau);
        _shaft.Rotation = new Vector3(_shaftAngle, 0, 0);
    }

    private void ApplyVisuals()
    {
        if (_readout is null) return;
        var green = new Color(0.25f, 1.0f, 0.35f);
        var greenDark = new Color(0.06f, 0.24f, 0.10f);
        var red = new Color(1.0f, 0.20f, 0.15f);
        var redDark = new Color(0.28f, 0.06f, 0.06f);
        SetLamp(_mainLampMat, MainClosed, green, greenDark);
        SetLamp(_starLampMat, StarClosed, green, greenDark);
        SetLamp(_deltaLampMat, DeltaClosed, green, greenDark);
        SetLamp(_breakerLampMat, IsBreakerTripped, red, redDark);
        SetLamp(_overloadLampMat, _overload.IsTripped, red, redDark);
        _breakerHandle.Rotation = new Vector3(0, 0, IsBreakerTripped ? -Mathf.Pi / 4.0f : 0.0f);

        string text = IsBreakerTripped ? "BREAKER TRIPPED"
            : _overload.IsTripped ? "OVERLOAD"
            : $"{Current:0.0} A  {Speed * 100.0f:0}%";
        if (_readout.Text != text) _readout.Text = text;
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
        .Bit("main", $"Star-Delta {tags.Index} Main Contactor Coil", TagKind.Output)
        .Bit("star", $"Star-Delta {tags.Index} Star Contactor Coil", TagKind.Output)
        .Bit("delta", $"Star-Delta {tags.Index} Delta Contactor Coil", TagKind.Output)
        .Bit("mainaux", $"Star-Delta {tags.Index} Main Aux Contact", TagKind.Input)
        .Bit("staraux", $"Star-Delta {tags.Index} Star Aux Contact", TagKind.Input)
        .Bit("deltaaux", $"Star-Delta {tags.Index} Delta Aux Contact", TagKind.Input)
        // Both normally closed, like the direct-on-line starter's overload:
        // true while healthy, so a broken circuit reads as "not available".
        .Bit("breaker", $"Star-Delta {tags.Index} Breaker OK (NC)", TagKind.Input, initial: true)
        .Bit("overload", $"Star-Delta {tags.Index} Overload OK (NC)", TagKind.Input, initial: true)
        .Float("speed", $"Star-Delta {tags.Index} Motor Speed (%)", TagKind.Input)
        .Analog(CurrentSignal, $"Star-Delta {tags.Index} Line Current", "A");

    public void CaptureSettings(PartSettings settings)
    {
        settings.Put("fla", FullLoadAmps);
        settings.Put("load_percent", LoadPercent);
        settings.Put("inertia", InertiaTime);
        CurrentSignal.Capture(settings);
    }

    public void ApplySettings(PartSettings settings)
    {
        if (settings.Number("fla") is { } fla) FullLoadAmps = fla;
        if (settings.Number("load_percent") is { } load) LoadPercent = load;
        if (settings.Number("inertia") is { } inertia) InertiaTime = inertia;
        CurrentSignal.Apply(settings);
    }

    public void StepPart(PartTick tick)
    {
        Step(tick.Bit("main"), tick.Bit("star"), tick.Bit("delta"), tick.Dt);

        // The auxiliary contacts ride on the same carrier as the main ones.
        tick.Write("mainaux", MainClosed);
        tick.Write("staraux", StarClosed);
        tick.Write("deltaaux", DeltaClosed);
        tick.Write("breaker", !IsBreakerTripped);
        tick.Write("overload", !_overload.IsTripped);
        tick.Write("speed", (double)(Speed * 100.0f));
        CurrentSignal.Write(tick, Current);
    }

    public void DescribeControls(IPartInspector ui)
    {
        ui.Slider("Full Load (A)", FullLoadAmps, 1.0f, 200.0f, 1.0f, value => FullLoadAmps = value);
        ui.Slider("Motor Load (%)", LoadPercent, 0.0f, 200.0f, 5.0f, value => LoadPercent = value);
        ui.Slider("Inertia (s)", InertiaTime, 0.2f, 20.0f, 0.1f, value => InertiaTime = value);
        CurrentSignal.Describe(ui);
    }

    public void ResetPart(PartReset reset)
    {
        _main.Reset();
        _star.Reset();
        _delta.Reset();
        _clock = 0.0;
        Speed = 0.0f;
        Current = 0.0f;
        ResetBreaker();
        reset.Write("mainaux", false);
        reset.Write("staraux", false);
        reset.Write("deltaaux", false);
        reset.Write("breaker", true);
        reset.Write("overload", true);
        reset.Write("speed", 0.0);
        CurrentSignal.Reset(reset, 0.0);
    }

    public PartOperation? Operation => new("star-delta starter", "breaker");

    /// <summary>A click is a person at the panel closing the breaker and
    /// pressing the overload's reset, not a toggle of a contact. Forcing
    /// <c>breaker</c> true by hand would paper over a trip the starter still
    /// believes in.</summary>
    public void Operate(PartOperate op) => ResetBreaker();

    /// <summary>IP-07. The star start, which is what a program does first:
    /// main and star in, delta left out -- driving all three would be the
    /// short circuit this part exists to punish. Both aux contacts have to make
    /// and the motor has to turn.</summary>
    public PartProbe? Probe => new(
        "`mainaux` and `staraux` made and `speed` above 1 %",
        r => r.Bit("mainaux") && r.Bit("staraux") && r.Number("speed") > r.AtStart("speed") + 1.0)
    {
        Drive = PartProbe.Drives(("main", true), ("star", true)),
        WithinTicks = 120,
    };
}
