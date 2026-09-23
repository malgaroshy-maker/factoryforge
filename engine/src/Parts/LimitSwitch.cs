using FactoryForge.TagBus;
using Godot;

namespace FactoryForge.Parts;

/// <summary>
/// A roller-lever limit switch with a normally-open and a normally-closed
/// contact (IP-17).
///
/// The most common input on any real machine, and the palette had none: every
/// presence sensor here was optical or inductive, which sense at a distance and
/// never bounce. A limit switch is mechanical. Something has to physically push
/// its lever, the contacts change over part-way round the lever's travel rather
/// than the instant it is touched, they change back a few degrees earlier than
/// they changed over, and on a worn or badly-mounted switch they chatter.
///
/// <b>Mounting.</b> The origin is the lane centre on the work plane, like the
/// blade stop: place it on the same grid cell as the belt it watches. The body
/// is bolted beside the lane on +Z and the lever reaches back across the lane
/// towards -Z, at <see cref="HeightAboveBelt"/> above the belt surface. A lever
/// set higher than a short carton is tall lets short cartons pass underneath
/// untouched, which is a height check made of nothing but a switch.
///
/// <b>What actuates it is geometry, not type.</b> A probe from the lever shaft
/// out to the tip of the roller measures how far into the lever's reach the
/// nearest body is — a carton on a belt, a cylinder's face plate, a gantry
/// carriage, a wall it was carelessly placed against. The lever swings back by
/// the angle that puts the roller on that surface, and the contacts follow the
/// lever angle: they change over at <see cref="TripAngle"/> and change back at
/// <see cref="TripAngle"/> - <see cref="Differential"/>. Nothing here knows what
/// a carton is.
///
/// <b>NO and NC are both contacts, not one bit and its inverse.</b> On the
/// actuating edge the NC opens and the NO makes; on release the NO opens and
/// the NC makes. The contact that is <em>making</em> can bounce (a setting, off
/// by default so no existing behaviour changes); the one that is breaking does
/// not. So during a bounce the two disagree, which is exactly what a program
/// monitoring both contacts of a safety switch has to tolerate for a moment and
/// no longer.
///
/// <b>How a PLC sees a bounce.</b> Chatter here toggles once per physics tick,
/// 16.7 ms at the default rate, because that is the finest the simulation can
/// represent. A real contact's bounce of a millisecond or two is shorter than a
/// tick and shorter than an S7 input filter's default 3.2 ms, so it would be
/// invisible to both — which is why the default is zero. What a student can
/// usefully be shown is the slower chatter of a roller skipping on a carton's
/// edge: set tens of milliseconds and a PLC scanning faster than the tick sees
/// several rising edges for one carton, and a counter written on the raw edge
/// over-counts. Whether each toggle reaches the program also depends on the
/// driver's poll period, exactly as a real one depends on the scan and the
/// input filter.
/// </summary>
public partial class LimitSwitch : Node3D, IPart
{
    /// <summary>Lever length, shaft to roller centre, metres. A rod lever on a
    /// conveyor-side switch is long enough to reach across the rail into the
    /// lane; the default puts the roller 0.10 m in from the rail's inside edge
    /// line, which a centred carton (0.24 m wide) pushes back 0.04 m.</summary>
    [Export] public float LeverLength { get; set; } = 0.20f;

    /// <summary>Roller height above the belt surface, metres. Below the short
    /// carton's 0.10 m by default, so every carton trips it.</summary>
    [Export] public float HeightAboveBelt { get; set; } = 0.05f;

    /// <summary>How long a making contact chatters, milliseconds. Zero is a
    /// clean switch and the default.</summary>
    [Export] public float BounceMs { get; set; }

    /// <summary>Lever angle at which the contacts change over, degrees. A
    /// roller lever has real pre-travel: touching the roller does nothing, and
    /// pushing it a fifth of a right angle does.</summary>
    public const float TripAngle = 20.0f;

    /// <summary>Differential travel, degrees: the contacts change back this far
    /// short of where they changed over, so a lever resting right on the trip
    /// point does not flicker.</summary>
    public const float Differential = 6.0f;

    /// <summary>The lever's mechanical overtravel stop, degrees.</summary>
    public const float MaxAngle = 75.0f;

    private const float RollerRadius = 0.02f;
    private const float RollerHeight = 0.018f;

    /// <summary>Where the lever shaft stands, across the lane from its
    /// centre: just outside the belt's side rail.</summary>
    private const float ShaftZ = PartLayout.StandardBeltWidth / 2.0f + 0.07f;

    private float LeverY => PartLayout.BeltSurface + HeightAboveBelt;

    /// <summary>Lever deflection, degrees, as of the last tick.</summary>
    public float LeverAngle { get; private set; }

    /// <summary>Has the lever gone past the trip point (and not yet back past
    /// the release point)?</summary>
    public bool IsActuated { get; private set; }

    /// <summary>The NO contact, bounce included.</summary>
    public bool NoClosed { get; private set; }

    /// <summary>The NC contact, bounce included. Closed at rest.</summary>
    public bool NcClosed { get; private set; } = true;

    private RayCast3D _probe = null!;
    private Node3D _lever = null!;
    private StandardMaterial3D? _ledMat;

    /// <summary>Which way the lever swings: +1 trails towards +X, the way a belt
    /// placed unrotated carries its load. Follows whatever last pushed it.</summary>
    private float _swing = 1.0f;

    private float _chatterLeft;
    private bool _chatterOpen;

    public override void _Ready() => BuildGeometry();

    /// <summary>Lever length and height are geometry, so the inspector's rows
    /// rebuild the switch rather than move a number nothing reads.</summary>
    public void Rebuild()
    {
        foreach (var child in GetChildren())
        {
            RemoveChild(child);
            child.QueueFree();
        }
        BuildGeometry();
    }

    private void BuildGeometry()
    {
        var bodyMat = new StandardMaterial3D
        {
            // Die-cast housing in the usual signal grey.
            AlbedoColor = new Color(0.66f, 0.67f, 0.68f),
            Metallic = 0.30f,
            Roughness = 0.50f,
        };
        var headMat = new StandardMaterial3D
        {
            AlbedoColor = new Color(0.14f, 0.15f, 0.17f),
            Metallic = 0.40f,
            Roughness = 0.45f,
        };
        var leverMat = new StandardMaterial3D
        {
            AlbedoColor = new Color(0.62f, 0.64f, 0.68f),
            Metallic = 0.35f,
            Roughness = 0.40f,
        };
        var rollerMat = new StandardMaterial3D
        {
            // A nylon roller, not a steel one: it is the part that touches the
            // product, and it has to show up against the lever.
            AlbedoColor = new Color(0.92f, 0.90f, 0.84f),
            Roughness = 0.60f,
        };

        const float bodyHeight = 0.10f;
        float bodyTop = LeverY - 0.025f;
        float bodyCentreY = bodyTop - bodyHeight / 2.0f;

        AddChild(new MeshInstance3D
        {
            Name = "SwitchBody",
            Mesh = new BoxMesh { Size = new Vector3(0.06f, bodyHeight, 0.05f) },
            MaterialOverride = bodyMat,
            Position = new Vector3(0, bodyCentreY, ShaftZ + 0.01f),
        });

        // The rotary head, with the shaft standing up out of it.
        AddChild(new MeshInstance3D
        {
            Name = "RotaryHead",
            Mesh = new CylinderMesh { TopRadius = 0.022f, BottomRadius = 0.024f, Height = 0.025f },
            MaterialOverride = headMat,
            Position = new Vector3(0, bodyTop + 0.0075f, ShaftZ),
        });

        // A bracket post from the floor, beside the conveyor rather than under
        // it, so the part stands up on its own wherever it is dropped.
        float postTop = bodyTop - bodyHeight;
        float postHeight = postTop + PartLayout.FloorDrop;
        AddChild(new MeshInstance3D
        {
            Name = "Post",
            Mesh = new BoxMesh { Size = new Vector3(0.03f, postHeight, 0.03f) },
            MaterialOverride = leverMat,
            Position = new Vector3(0, postTop - postHeight / 2.0f, ShaftZ + 0.01f),
        });
        AddChild(new MeshInstance3D
        {
            Name = "BaseFoot",
            Mesh = new BoxMesh { Size = new Vector3(0.12f, 0.02f, 0.12f) },
            MaterialOverride = headMat,
            Position = new Vector3(0, -PartLayout.FloorDrop, ShaftZ + 0.01f),
        });

        // The status LED some switches carry. It shows the NO contact, so a
        // chattering contact is visible as a flicker.
        _ledMat = new StandardMaterial3D
        {
            AlbedoColor = new Color(0.30f, 0.20f, 0.08f),
            EmissionEnabled = true,
            Emission = new Color(1.0f, 0.60f, 0.10f),
            EmissionEnergyMultiplier = 0.0f,
        };
        AddChild(new MeshInstance3D
        {
            Name = "StatusLed",
            Mesh = new SphereMesh { Radius = 0.009f, Height = 0.018f },
            MaterialOverride = _ledMat,
            Position = new Vector3(0.031f, bodyCentreY + 0.025f, ShaftZ + 0.01f),
        });

        // The lever pivots about the vertical shaft. Its arm and roller hang
        // off a pivot node, so one rotation moves both.
        _lever = new Node3D { Name = "Lever", Position = new Vector3(0, LeverY, ShaftZ) };
        _lever.AddChild(new MeshInstance3D
        {
            Name = "LeverArm",
            Mesh = new BoxMesh { Size = new Vector3(0.012f, 0.008f, LeverLength) },
            MaterialOverride = leverMat,
            Position = new Vector3(0, 0.012f, -LeverLength / 2.0f),
        });
        _lever.AddChild(new MeshInstance3D
        {
            Name = "Roller",
            Mesh = new CylinderMesh
            {
                TopRadius = RollerRadius, BottomRadius = RollerRadius, Height = RollerHeight,
            },
            MaterialOverride = rollerMat,
            Position = new Vector3(0, 0, -LeverLength),
        });
        AddChild(_lever);

        // The probe: from the shaft out to the far side of the roller at rest,
        // which is as far as anything can reach and still touch the lever.
        _probe = new RayCast3D
        {
            Name = "LeverProbe",
            Position = new Vector3(0, LeverY, ShaftZ),
            TargetPosition = new Vector3(0, 0, -(LeverLength + RollerRadius)),
            Enabled = true,
            CollideWithBodies = true,
            CollideWithAreas = false,
        };
        AddChild(_probe);

        ApplyVisuals();
    }

    /// <summary>
    /// The lever angle the nearest body in reach pushes it to, degrees.
    ///
    /// The roller rests <see cref="LeverLength"/> out from the shaft. A surface
    /// <c>h</c> from the shaft puts the roller's centre at <c>h - r</c>, so the
    /// lever swings to <c>acos((h - r) / L)</c>; a surface beyond the roller
    /// touches nothing and the lever stays at rest.
    /// </summary>
    private float MeasureDeflection()
    {
        _probe.ForceRaycastUpdate();
        if (!_probe.IsColliding()) return 0.0f;

        Vector3 hit = _probe.ToLocal(_probe.GetCollisionPoint());
        float reach = -hit.Z - RollerRadius;
        if (reach >= LeverLength) return 0.0f;

        // Swing the way the thing pushing it is travelling, so a carton going
        // either way along the lane trails the lever behind it.
        if (_probe.GetCollider() is RigidBody3D body)
        {
            float along = (GlobalTransform.Basis.Inverse() * body.LinearVelocity).X;
            if (Mathf.Abs(along) > 0.02f) _swing = Mathf.Sign(along);
        }

        float cos = Mathf.Clamp(reach / Mathf.Max(LeverLength, 0.01f), -1.0f, 1.0f);
        return Mathf.Min(Mathf.RadToDeg(Mathf.Acos(cos)), MaxAngle);
    }

    /// <summary>
    /// The contact block, driven by the lever. Public so a test can drive the
    /// contacts without staging a carton.
    /// </summary>
    /// <param name="angle">Lever deflection, degrees.</param>
    public void StepContacts(float angle, float delta)
    {
        LeverAngle = angle;

        bool actuated = IsActuated
            ? angle > TripAngle - Differential
            : angle >= TripAngle;

        if (actuated != IsActuated)
        {
            IsActuated = actuated;
            // A fresh changeover: whichever contact is making now starts its
            // bounce, closed first -- the contacts meet, then fly apart.
            _chatterLeft = BounceMs / 1000.0f;
            _chatterOpen = false;
        }

        bool makingClosed = true;
        if (_chatterLeft > 1e-4f)
        {
            makingClosed = !_chatterOpen;
            _chatterOpen = !_chatterOpen;
            _chatterLeft -= delta;
        }

        // The breaking contact opens cleanly; only the making one chatters.
        NoClosed = IsActuated && makingClosed;
        NcClosed = !IsActuated && makingClosed;
        ApplyVisuals();
    }

    private void ApplyVisuals()
    {
        if (_lever is not null)
            _lever.Rotation = new Vector3(0, -_swing * Mathf.DegToRad(LeverAngle), 0);

        if (_ledMat is null) return;
        _ledMat.AlbedoColor = NoClosed ? new Color(1.0f, 0.62f, 0.12f) : new Color(0.30f, 0.20f, 0.08f);
        _ledMat.EmissionEnergyMultiplier = NoClosed ? 3.0f : 0.0f;
    }

    // ---------- IPart (HP-34)

    public void DeclareTags(PartTagBuilder tags) => tags
        .Bit("no", $"Limit Switch {tags.Index} NO Contact", TagKind.Input)
        // Normally closed: made at rest, so the scene powers up in the state
        // the geometry shows, and a cut wire reads as "actuated".
        .Bit("nc", $"Limit Switch {tags.Index} NC Contact", TagKind.Input, initial: true);

    public void CaptureSettings(PartSettings settings)
    {
        settings.Put("lever_length", LeverLength);
        settings.Put("height", HeightAboveBelt);
        settings.Put("bounce_ms", BounceMs);
    }

    public void ApplySettings(PartSettings settings)
    {
        if (settings.Number("lever_length") is { } lever) LeverLength = lever;
        if (settings.Number("height") is { } height) HeightAboveBelt = height;
        if (settings.Number("bounce_ms") is { } bounce) BounceMs = bounce;
    }

    public void StepPart(PartTick tick)
    {
        StepContacts(MeasureDeflection(), tick.Dt);
        tick.Write("no", NoClosed);
        tick.Write("nc", NcClosed);
    }

    public void DescribeControls(IPartInspector ui)
    {
        ui.Slider("Lever (m)", LeverLength, 0.04f, 0.30f, 0.01f, value => { LeverLength = value; Rebuild(); });
        ui.Slider("Height (m)", HeightAboveBelt, 0.02f, 0.35f, 0.01f, value => { HeightAboveBelt = value; Rebuild(); });
        ui.Slider("Bounce (ms)", BounceMs, 0.0f, 250.0f, 5.0f, value => BounceMs = value);
    }

    public void ResetPart(PartReset reset)
    {
        IsActuated = false;
        LeverAngle = 0.0f;
        _chatterLeft = 0.0f;
        NoClosed = false;
        NcClosed = true;
        ApplyVisuals();
        reset.Write("no", false);
        reset.Write("nc", true);
    }

    /// <summary>IP-07. Nothing drives a limit switch but the thing it touches,
    /// so a carton is parked with its near face 0.12 m in front of the shaft --
    /// inside the default 0.20 m lever's reach, which swings it about 60
    /// degrees, well past the trip angle. A carton is 0.24 m across the lane, so
    /// its centre sits a further 0.12 m in. The contacts have to change over:
    /// NO made and NC broken.</summary>
    public PartProbe? Probe => new(
        "the lever pushed past its trip angle: `no` made, `nc` broken",
        r => r.Bit("no") && !r.Bit("nc"))
    {
        Carton = new ProbeCarton(new Vector3(0, PartLayout.BeltSurface, ShaftZ - 0.12f - 0.24f / 2.0f)),
    };
}
