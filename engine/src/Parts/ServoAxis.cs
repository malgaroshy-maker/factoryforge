using FactoryForge.TagBus;
using Godot;

namespace FactoryForge.Parts;

/// <summary>
/// A servo-driven linear axis with a PLCopen-shaped interface: power, status,
/// error and reset, an absolute move, and position feedback (IP-17).
///
/// Every other axis in the library is a setpoint that is always obeyed. A real
/// servo drive is a machine with a state: it has to be <b>enabled</b> before it
/// does anything, it says when it is <b>ready</b>, it <b>stops itself</b> when
/// something is wrong, and it stays stopped until the program acknowledges the
/// error. That handshake is most of what a motion program is, and it is what
/// <c>MC_Power</c>, <c>MC_Reset</c> and <c>MC_MoveAbsolute</c> exist for.
///
/// <b>The interface</b>, from the controller's side:
/// <list type="bullet">
/// <item><c>enable</c> (out) — <c>MC_Power.Enable</c>. Level, not edge.</item>
/// <item><c>ready</c> (in) — powered and error-free, <c>MC_Power.Status</c>.
///   Rises <see cref="EnableDelay"/> after <c>enable</c> (brake release and
///   current-loop start), drops at once when <c>enable</c> drops or an error
///   latches.</item>
/// <item><c>target</c>, <c>velocity</c> (out, mm and mm/s) — the move. The
///   drive follows <c>target</c> whenever it is ready, re-planning on the fly
///   if it changes mid-move (PLCopen's <em>aborting</em> buffer mode).
///   <c>velocity</c> is limited to the axis maximum, as a real technology
///   object limits it.</item>
/// <item><c>position</c> (in, mm) and <c>inposition</c> (in).</item>
/// <item><c>fault</c> (in) — the injectable drive fault every drive in the
///   library has: nothing in the simulation computes it; the fault tool, a
///   force or a test raises it.</item>
/// <item><c>error</c> (in) — the drive's <em>latched</em> error,
///   <c>MC_Power.Error</c> / the axis error bit. This is the one to read:
///   <c>fault</c> says the cause is present, <c>error</c> says the drive has
///   stopped and is waiting for an acknowledgement.</item>
/// <item><c>ack</c> (out) — <c>MC_Reset.Execute</c>.</item>
/// </list>
///
/// <b>The error rule.</b> An error latches on a drive fault, on a target
/// outside the travel (the software limits), or on a move asked for at a
/// velocity of zero or less. It drops <c>ready</c> and <c>inposition</c> and
/// quick-stops the carriage at four times the configured acceleration, where
/// it holds. It clears on a <b>rising edge of <c>ack</c></b> and only if its
/// cause has gone: acknowledging while the fault is still present, or while
/// the target is still out of range, changes nothing. Cycling <c>enable</c>
/// does <em>not</em> clear it. After a clean acknowledge with <c>enable</c>
/// still on, the drive is ready again and moves to whatever <c>target</c>
/// says — so a program that does not want the interrupted move to resume sets
/// <c>target</c> to <c>position</c> before acknowledging. (On a real S7-1500 a
/// rejected command is reported by the motion block rather than by the axis;
/// here there is no motion block, so the axis carries it.)
///
/// <b>Motion</b> is trapezoidal: acceleration-limited to
/// <see cref="Acceleration"/>, velocity-limited to the lesser of
/// <c>velocity</c> and <see cref="MaxVelocity"/>, and decelerating so as to
/// arrive exactly. That is the profile an <c>MC_MoveAbsolute</c> produces. A
/// first-order lag was the alternative and was rejected: it never quite
/// arrives, so "in position" would depend on the window alone, and it starts
/// every move at its fastest, which no servo does.
///
/// <b><c>inposition</c> is stale for one round trip.</b> It is the drive's own
/// judgement — ready, stopped, and within <see cref="InPositionWindow"/> of the
/// target <em>the drive currently holds</em>. A target written this scan
/// reaches the drive on the next simulation tick, so for the scans in between
/// <c>inposition</c> still describes the previous move and reads true at the
/// place you are trying to leave. This is the same staleness as the gantry's,
/// and it is real: a fieldbus drive's status word lags its control word the
/// same way. After writing a new target, either wait for <c>inposition</c> to
/// fall before waiting for it to rise, or check <c>position</c> against the
/// destination the step wants — which is what a sequence should do anyway.
/// </summary>
public partial class ServoAxis : Node3D, IPart
{
    /// <summary>Travel, millimetres: position 0 at the motor end, at the part's
    /// origin, to this at the far end. Also the software limit.</summary>
    [Export] public float StrokeMm { get; set; } = 1000.0f;

    /// <summary>The axis' velocity limit, mm/s.</summary>
    [Export] public float MaxVelocity { get; set; } = 500.0f;

    /// <summary>Acceleration and deceleration, mm/s².</summary>
    [Export] public float Acceleration { get; set; } = 2000.0f;

    /// <summary>How close to the target counts as arrived, mm.</summary>
    [Export] public float InPositionWindow { get; set; } = 0.5f;

    /// <summary>Enable to ready: brake release and the current loop coming
    /// up, seconds.</summary>
    public const float EnableDelay = 0.10f;

    /// <summary>Quick-stop deceleration as a multiple of
    /// <see cref="Acceleration"/>.</summary>
    public const float QuickStopFactor = 4.0f;

    /// <summary>Actual position, mm.</summary>
    public float ActualPosition { get; private set; }

    /// <summary>Actual velocity, mm/s, signed.</summary>
    public float Velocity { get; private set; }

    /// <summary>The target the drive is holding, mm.</summary>
    public float Target { get; private set; }

    public bool IsReady { get; private set; }
    public bool HasError { get; private set; }

    /// <summary>Why the error latched, for the readout and the log.</summary>
    public string ErrorText { get; private set; } = "";

    public bool InPosition =>
        IsReady && Mathf.Abs(Velocity) < 1e-3f && Mathf.Abs(Target - ActualPosition) <= InPositionWindow;

    private float _enableTimer;
    private bool _lastAck;

    private const float RailY = 0.05f;
    private const float RailHeight = 0.06f;
    private const float CarriageThickness = 0.03f;

    private AnimatableBody3D _carriage = null!;
    private StandardMaterial3D _statusMat = null!;
    private Label3D _readout = null!;

    public override void _Ready() => BuildGeometry();

    /// <summary>The stroke is geometry, so its inspector row rebuilds the
    /// rail rather than move a number the rail was only built from.</summary>
    public void Rebuild()
    {
        foreach (var child in GetChildren())
        {
            RemoveChild(child);
            child.QueueFree();
        }
        BuildGeometry();
    }

    private float StrokeM => Mathf.Max(StrokeMm, 1.0f) / 1000.0f;

    private void BuildGeometry()
    {
        var profileMat = new StandardMaterial3D
        {
            // Anodised aluminium extrusion.
            AlbedoColor = new Color(0.74f, 0.76f, 0.78f),
            Metallic = 0.40f,
            Roughness = 0.40f,
        };
        var trimMat = new StandardMaterial3D
        {
            AlbedoColor = new Color(0.16f, 0.17f, 0.19f),
            Metallic = 0.45f,
            Roughness = 0.45f,
        };

        float railLength = StrokeM + 0.20f;
        float railCentre = StrokeM / 2.0f;

        AddChild(new MeshInstance3D
        {
            Name = "Profile",
            Mesh = new BoxMesh { Size = new Vector3(railLength, RailHeight, 0.10f) },
            MaterialOverride = profileMat,
            Position = new Vector3(railCentre, RailY, 0),
        });
        // The slot the carriage runs in, as a dark strip along the top.
        AddChild(new MeshInstance3D
        {
            Name = "BeltCover",
            Mesh = new BoxMesh { Size = new Vector3(railLength - 0.04f, 0.004f, 0.03f) },
            MaterialOverride = trimMat,
            Position = new Vector3(railCentre, RailY + RailHeight / 2.0f + 0.002f, 0),
        });
        foreach (float x in new[] { -0.10f, StrokeM + 0.10f })
        {
            AddChild(new MeshInstance3D
            {
                Name = "EndCap",
                Mesh = new BoxMesh { Size = new Vector3(0.03f, RailHeight + 0.02f, 0.11f) },
                MaterialOverride = trimMat,
                Position = new Vector3(x, RailY, 0),
            });
            float legHeight = RailY - RailHeight / 2.0f + PartLayout.FloorDrop;
            AddChild(new MeshInstance3D
            {
                Name = "Leg",
                Mesh = new BoxMesh { Size = new Vector3(0.04f, legHeight, 0.08f) },
                MaterialOverride = trimMat,
                Position = new Vector3(x, RailY - RailHeight / 2.0f - legHeight / 2.0f, 0),
            });
        }

        // The servo motor on the zero end, with a status ring on its back:
        // green ready, amber enabled and coming up, red error, dark off.
        AddChild(new MeshInstance3D
        {
            Name = "ServoMotor",
            Mesh = new BoxMesh { Size = new Vector3(0.14f, 0.08f, 0.08f) },
            MaterialOverride = trimMat,
            Position = new Vector3(-0.185f, RailY, 0),
        });
        _statusMat = new StandardMaterial3D
        {
            AlbedoColor = new Color(0.10f, 0.10f, 0.10f),
            Roughness = 0.3f,
        };
        AddChild(new MeshInstance3D
        {
            Name = "StatusRing",
            Mesh = new CylinderMesh { TopRadius = 0.03f, BottomRadius = 0.03f, Height = 0.01f },
            MaterialOverride = _statusMat,
            Position = new Vector3(-0.26f, RailY, 0),
            Rotation = new Vector3(0, 0, Mathf.Pi / 2.0f),
        });
        AddChild(new MeshInstance3D
        {
            Name = "Cable",
            Mesh = new CylinderMesh { TopRadius = 0.008f, BottomRadius = 0.008f, Height = 0.08f },
            MaterialOverride = trimMat,
            Position = new Vector3(-0.185f, RailY + 0.08f, 0),
        });

        // The carriage: a real body, so it pushes what it meets and a limit
        // switch placed at the end of travel sees it arrive.
        var plate = new Vector3(0.14f, CarriageThickness, 0.12f);
        _carriage = new AnimatableBody3D { Name = "Carriage", SyncToPhysics = true };
        _carriage.AddChild(new MeshInstance3D
        {
            Name = "CarriagePlate",
            Mesh = new BoxMesh { Size = plate },
            MaterialOverride = new StandardMaterial3D
            {
                AlbedoColor = new Color(0.95f, 0.45f, 0.10f),
                Roughness = 0.50f,
            },
        });
        _carriage.AddChild(new CollisionShape3D { Shape = new BoxShape3D { Size = plate } });
        AddChild(_carriage);

        _readout = new Label3D
        {
            Name = "DriveReadout",
            Text = "OFF",
            Position = new Vector3(-0.185f, RailY + 0.20f, 0),
            Billboard = BaseMaterial3D.BillboardModeEnum.Enabled,
            FontSize = 48,
            PixelSize = 0.0010f,
            Modulate = new Color(0.85f, 0.90f, 1.0f),
        };
        AddChild(_readout);

        ApplyVisuals();
    }

    private void LatchError(string why)
    {
        if (HasError) return;
        HasError = true;
        ErrorText = why;
        GD.Print($"{Name}: axis error -- {why}");
    }

    /// <summary>
    /// One tick of the drive.
    /// </summary>
    public void Step(bool enable, bool ack, bool fault, float target, float velocity, float delta)
    {
        // Causes first, then the acknowledgement: an ack that arrives while
        // the cause is still there is an ack of nothing.
        if (fault) LatchError("drive fault");

        bool ackEdge = ack && !_lastAck;
        _lastAck = ack;
        if (ackEdge && HasError && !fault)
        {
            HasError = false;
            ErrorText = "";
        }

        _enableTimer = enable ? _enableTimer + delta : 0.0f;
        bool powered = enable && _enableTimer >= EnableDelay;

        // A command the drive cannot carry out is refused, not guessed at.
        // Only a drive that is actually commanding the axis judges one.
        if (powered && !HasError)
        {
            bool needsMove = Mathf.Abs(target - ActualPosition) > InPositionWindow;
            if (target < 0.0f || target > StrokeMm) LatchError($"target {target:0.#} mm is outside 0..{StrokeMm:0} mm");
            else if (needsMove && velocity <= 0.0f) LatchError("move requested at a velocity of zero or less");
        }

        IsReady = powered && !HasError;

        if (IsReady)
        {
            Target = target;
            Track(Mathf.Min(velocity, Mathf.Max(MaxVelocity, 1.0f)), delta);
        }
        else
        {
            // Disabled or in error: quick stop, then hold on the brake.
            float decel = Mathf.Max(Acceleration, 1.0f) * QuickStopFactor;
            Velocity = Mathf.MoveToward(Velocity, 0.0f, decel * delta);
            ActualPosition += Velocity * delta;
        }

        // The hard stops at either end, which the software limit is there to
        // keep the carriage off.
        if (ActualPosition < 0.0f || ActualPosition > StrokeMm)
        {
            ActualPosition = Mathf.Clamp(ActualPosition, 0.0f, StrokeMm);
            Velocity = 0.0f;
        }

        ApplyVisuals();
    }

    /// <summary>The trajectory generator: the speed that still lets the axis
    /// stop at the target, capped at the commanded speed, approached at the
    /// acceleration limit.</summary>
    private void Track(float speedCap, float delta)
    {
        float accel = Mathf.Max(Acceleration, 1.0f);
        float error = Target - ActualPosition;

        if (Mathf.Abs(error) < 1e-4f && Mathf.Abs(Velocity) <= accel * delta)
        {
            ActualPosition = Target;
            Velocity = 0.0f;
            return;
        }

        // The fastest speed from which the axis can still stop at the target
        // when deceleration is applied in whole ticks: v²/2a + v·dt/2 = |e|.
        // The continuous sqrt(2a|e|) overshoots, because each tick moves the
        // carriage at the speed it had before that tick's deceleration.
        float slew = accel * delta;
        float stoppable = (Mathf.Sqrt(slew * slew + 8.0f * accel * Mathf.Abs(error)) - slew) / 2.0f;
        float desired = Mathf.Sign(error) * Mathf.Min(Mathf.Max(speedCap, 0.0f), stoppable);
        Velocity = Mathf.MoveToward(Velocity, desired, accel * delta);

        float step = Velocity * delta;
        // Arriving: the last step would reach or cross the target, at a speed
        // the axis can shed in a tick or two. Land on it.
        if (Mathf.Sign(step) == Mathf.Sign(error) && Mathf.Abs(step) >= Mathf.Abs(error)
            && Mathf.Abs(Velocity) <= 2.0f * accel * delta)
        {
            ActualPosition = Target;
            Velocity = 0.0f;
            return;
        }
        ActualPosition += step;
    }

    private void ApplyVisuals()
    {
        if (_carriage is null) return;
        _carriage.Position = new Vector3(ActualPosition / 1000.0f, RailY + RailHeight / 2.0f + CarriageThickness / 2.0f, 0);

        Color ring = HasError ? new Color(1.0f, 0.15f, 0.12f)
            : IsReady ? new Color(0.25f, 1.0f, 0.35f)
            : _enableTimer > 0.0f ? new Color(1.0f, 0.70f, 0.10f)
            : new Color(0.10f, 0.10f, 0.10f);
        bool lit = HasError || IsReady || _enableTimer > 0.0f;
        _statusMat.AlbedoColor = ring;
        _statusMat.EmissionEnabled = lit;
        _statusMat.Emission = lit ? ring : Colors.Black;
        _statusMat.EmissionEnergyMultiplier = lit ? 2.4f : 0.0f;

        string text = HasError ? $"ERROR {ActualPosition:0.0} mm"
            : IsReady ? $"{ActualPosition:0.0} mm"
            : $"OFF {ActualPosition:0.0} mm";
        if (_readout.Text != text) _readout.Text = text;
    }

    // ---------- IPart (HP-34)

    public void DeclareTags(PartTagBuilder tags) => tags
        .Bit("enable", $"Axis {tags.Index} Enable", TagKind.Output)
        .Bit("ack", $"Axis {tags.Index} Error Acknowledge", TagKind.Output)
        .Float("target", $"Axis {tags.Index} Target Position (mm)", TagKind.Output)
        .Float("velocity", $"Axis {tags.Index} Velocity (mm/s)", TagKind.Output)
        .Bit("ready", $"Axis {tags.Index} Ready", TagKind.Input)
        .Bit("error", $"Axis {tags.Index} Error (latched)", TagKind.Input)
        .Float("position", $"Axis {tags.Index} Actual Position (mm)", TagKind.Input)
        .Bit("inposition", $"Axis {tags.Index} In Position", TagKind.Input)
        .Bit("fault", $"Axis {tags.Index} Drive Fault", TagKind.Input);

    public void CaptureSettings(PartSettings settings)
    {
        settings.Put("stroke_mm", StrokeMm);
        settings.Put("max_velocity", MaxVelocity);
        settings.Put("acceleration", Acceleration);
        settings.Put("window", InPositionWindow);
    }

    public void ApplySettings(PartSettings settings)
    {
        if (settings.Number("stroke_mm") is { } stroke) StrokeMm = stroke;
        if (settings.Number("max_velocity") is { } vmax) MaxVelocity = vmax;
        if (settings.Number("acceleration") is { } accel) Acceleration = accel;
        if (settings.Number("window") is { } window) InPositionWindow = window;
    }

    public void StepPart(PartTick tick)
    {
        Step(tick.Bit("enable"), tick.Bit("ack"), tick.Bit("fault"),
             tick.Number("target"), tick.Number("velocity"), tick.Dt);

        tick.Write("ready", IsReady);
        tick.Write("error", HasError);
        tick.Write("position", (double)ActualPosition);
        tick.Write("inposition", InPosition);
    }

    public void DescribeControls(IPartInspector ui)
    {
        ui.Slider("Stroke (mm)", StrokeMm, 100.0f, 3000.0f, 50.0f, value => { StrokeMm = value; Rebuild(); });
        ui.Slider("Max Speed (mm/s)", MaxVelocity, 10.0f, 3000.0f, 10.0f, value => MaxVelocity = value);
        ui.Slider("Accel (mm/s2)", Acceleration, 100.0f, 20000.0f, 100.0f, value => Acceleration = value);
        ui.Slider("Window (mm)", InPositionWindow, 0.05f, 10.0f, 0.05f, value => InPositionWindow = value);
    }

    public void ResetPart(PartReset reset)
    {
        ActualPosition = 0.0f;
        Velocity = 0.0f;
        Target = 0.0f;
        HasError = false;
        ErrorText = "";
        IsReady = false;
        _enableTimer = 0.0f;
        _lastAck = false;
        ApplyVisuals();
        reset.Write("ready", false);
        reset.Write("error", false);
        reset.Write("position", 0.0);
        reset.Write("inposition", false);
    }

    /// <summary>IP-07. Enabled and sent to 100 mm, the drive has to come
    /// ready, move, and report the carriage there: `ready`, `inposition`, and
    /// `position` within half a millimetre of the target.</summary>
    public PartProbe? Probe => new(
        "`ready`, `inposition`, and `position` at the commanded 100 mm",
        r => r.Bit("ready") && r.Bit("inposition") && System.Math.Abs(r.Number("position") - 100.0) < 0.5)
    {
        Drive = PartProbe.Drives(("enable", true), ("target", 100.0), ("velocity", 200.0)),
        WithinTicks = 120,
    };
}
