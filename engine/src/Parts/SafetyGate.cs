using FactoryForge.TagBus;
using Godot;

namespace FactoryForge.Parts;

/// <summary>
/// An interlocked guard door (CP-08).
///
/// The library had one safety input — the panel's mushroom — and it is the one
/// every student already expects. A guard interlock is the one they meet next
/// and the one that actually shapes a program: the line may not run with the
/// gate open, the gate may not open while the line is dangerous, and those two
/// rules are enforced by different things. The solenoid lock is what makes it a
/// two-way contract rather than a second E-stop: the controller decides whether
/// the door *can* be opened, and until it releases the lock, pulling on the
/// handle does nothing.
///
/// <c>closed</c> is wired the way a real guard switch is — <b>true while the
/// guard is shut</b>, so a broken wire reads as an open guard and stops the
/// line, exactly like the normally-closed E-stop next to it.
/// </summary>
public partial class SafetyGate : Node3D, IPart
{
    /// <summary>How far the door slides when it opens, in metres.</summary>
    [Export] public float TravelDistance { get; set; } = 0.72f;

    [Export] public float SlideSpeed { get; set; } = 1.1f;

    private const float DoorWidth = 0.76f;
    private const float DoorHeight = 1.10f;
    private const float FrameThickness = 0.06f;

    /// <summary>Base of the frame relative to the part origin: the guard stands
    /// on the floor, like every other free-standing part.</summary>
    private float BaseY => -PartLayout.FloorDrop;

    private Node3D _door = null!;
    private StandardMaterial3D _lockMat = null!;
    private StandardMaterial3D _doorMat = null!;

    private float _opening;      // 0 = shut, 1 = fully open
    private bool _wantOpen;

    /// <summary>The guard switch. True while the door is shut, because that is
    /// how a guard switch is wired: an open circuit must read as "not
    /// safe".</summary>
    public bool IsClosed => _opening <= 0.02f;

    /// <summary>Solenoid energised. While locked, the door cannot be opened by
    /// hand — which is the whole reason this is not just a second E-stop.</summary>
    public bool IsLocked { get; private set; }

    public float Opening => _opening;

    public override void _Ready()
    {
        var frameMat = new StandardMaterial3D
        {
            AlbedoColor = new Color(0.95f, 0.75f, 0.10f),
            Metallic = 0.25f,
            Roughness = 0.45f,
        };

        // Uprights and a header, so the door has something to be a door in.
        foreach (int side in new[] { -1, 1 })
        {
            AddChild(new MeshInstance3D
            {
                Name = side < 0 ? "PostLeft" : "PostRight",
                Mesh = new BoxMesh { Size = new Vector3(FrameThickness, DoorHeight + 0.12f, FrameThickness) },
                MaterialOverride = frameMat,
                Position = new Vector3(side * (DoorWidth / 2 + FrameThickness / 2),
                                       BaseY + (DoorHeight + 0.12f) / 2, 0),
            });
        }
        AddChild(new MeshInstance3D
        {
            Name = "Header",
            Mesh = new BoxMesh
            {
                Size = new Vector3(DoorWidth + FrameThickness * 2 + TravelDistance, FrameThickness, FrameThickness),
            },
            MaterialOverride = frameMat,
            // Offset toward the side the door slides to, so the open door has a
            // rail to hang from rather than floating in space.
            Position = new Vector3(TravelDistance / 2, BaseY + DoorHeight + 0.12f, 0),
        });

        // The door: a tinted polycarbonate panel in a frame, the way a real
        // machine guard is, so you can see the line through it.
        _door = new Node3D { Name = "GateDoor" };
        AddChild(_door);

        _doorMat = new StandardMaterial3D
        {
            AlbedoColor = new Color(0.45f, 0.62f, 0.70f, 0.35f),
            Transparency = BaseMaterial3D.TransparencyEnum.Alpha,
            Roughness = 0.15f,
            CullMode = BaseMaterial3D.CullModeEnum.Disabled,
        };
        _door.AddChild(new MeshInstance3D
        {
            Name = "Pane",
            Mesh = new BoxMesh { Size = new Vector3(DoorWidth, DoorHeight, 0.02f) },
            MaterialOverride = _doorMat,
            Position = new Vector3(0, BaseY + DoorHeight / 2, 0),
        });
        _door.AddChild(new MeshInstance3D
        {
            Name = "Handle",
            Mesh = new BoxMesh { Size = new Vector3(0.04f, 0.22f, 0.04f) },
            MaterialOverride = frameMat,
            Position = new Vector3(DoorWidth / 2 - 0.09f, BaseY + DoorHeight / 2, 0.05f),
        });

        // Solenoid lock body on the frame, with a lamp that says whether it is
        // energised. A guard whose lock state is invisible is a guard you have
        // to guess about, which is the opposite of what it is for.
        _lockMat = new StandardMaterial3D
        {
            AlbedoColor = new Color(0.30f, 0.06f, 0.06f),
            EmissionEnabled = true,
            Emission = new Color(1.0f, 0.25f, 0.15f),
            EmissionEnergyMultiplier = 0.0f,
        };
        AddChild(new MeshInstance3D
        {
            Name = "LockBody",
            Mesh = new BoxMesh { Size = new Vector3(0.09f, 0.16f, 0.09f) },
            MaterialOverride = new StandardMaterial3D
            {
                AlbedoColor = new Color(0.22f, 0.23f, 0.26f),
                Metallic = 0.45f,
                Roughness = 0.45f,
            },
            Position = new Vector3(-(DoorWidth / 2 + FrameThickness / 2), BaseY + DoorHeight * 0.62f, 0.07f),
        });
        AddChild(new MeshInstance3D
        {
            Name = "LockLamp",
            Mesh = new SphereMesh { Radius = 0.026f, Height = 0.052f },
            MaterialOverride = _lockMat,
            Position = new Vector3(-(DoorWidth / 2 + FrameThickness / 2), BaseY + DoorHeight * 0.62f + 0.11f, 0.07f),
        });

        ApplyOpening();
    }

    /// <summary>Energise or release the solenoid. A locked *shut* guard will
    /// not open; a lock commanded while the guard is already open cannot shut
    /// it, which is why the lock only bites at <c>IsClosed</c>.</summary>
    public void SetLocked(bool locked)
    {
        IsLocked = locked;
        if (locked && IsClosed) _wantOpen = false;

        if (_lockMat is null) return;
        _lockMat.EmissionEnergyMultiplier = locked ? 2.6f : 0.0f;
        _lockMat.AlbedoColor = locked ? new Color(1.0f, 0.30f, 0.18f) : new Color(0.30f, 0.06f, 0.06f);
    }

    /// <summary>Pull the handle. Refused while the solenoid holds the guard
    /// shut — the refusal is the point, and it is silent on purpose: a real
    /// locked gate simply does not move.</summary>
    public void Toggle()
    {
        if (IsLocked && IsClosed) return;
        _wantOpen = !_wantOpen;
    }

    public void Step(float delta)
    {
        float target = _wantOpen ? 1.0f : 0.0f;
        _opening = Mathf.MoveToward(_opening, target, SlideSpeed / Mathf.Max(TravelDistance, 0.01f) * delta);
        ApplyOpening();
    }

    /// <summary>Slam it shut without ceremony, for a scene reset.</summary>
    public void ResetGate()
    {
        _wantOpen = false;
        _opening = 0.0f;
        ApplyOpening();
    }

    private void ApplyOpening()
    {
        if (_door is null) return;
        _door.Position = new Vector3(TravelDistance * _opening, 0, 0);

        // The pane reddens as it opens, so an open guard is obvious from the
        // far side of the scene rather than only from an angle where you can
        // see the gap.
        if (_doorMat is not null)
        {
            _doorMat.AlbedoColor = new Color(
                Mathf.Lerp(0.45f, 0.85f, _opening),
                Mathf.Lerp(0.62f, 0.30f, _opening),
                Mathf.Lerp(0.70f, 0.25f, _opening),
                0.35f);
        }
    }

    // ---------- IPart (HP-34)

    public void DeclareTags(PartTagBuilder tags) => tags
        // Normally closed, like the E-stop beside it: true while the guard is
        // shut, so a broken circuit reads as "not safe".
        .Bit("closed", $"Guard {tags.Index} Closed (NC)", TagKind.Input, initial: true)
        .Bit("lock", $"Guard {tags.Index} Solenoid Lock", TagKind.Output)
        .Bit("locked", $"Guard {tags.Index} Locked Shut", TagKind.Input);

    public void CaptureSettings(PartSettings settings)
    {
        settings.Put("travel", TravelDistance);
        settings.Put("slide_speed", SlideSpeed);
    }

    public void ApplySettings(PartSettings settings)
    {
        if (settings.Number("travel") is { } travel) TravelDistance = travel;
        if (settings.Number("slide_speed") is { } slide) SlideSpeed = slide;
    }

    public void StepPart(PartTick tick)
    {
        if (tick.TryBit("lock", out bool locked)) SetLocked(locked);

        Step(tick.Dt);
        tick.Write("closed", IsClosed);
        tick.Write("locked", IsLocked && IsClosed);
    }

    public void DescribeControls(IPartInspector ui)
    {
        ui.Slider("Travel (m)", TravelDistance, 0.2f, 1.5f, 0.05f,
                  value => TravelDistance = value);
        ui.Slider("Slide Speed (m/s)", SlideSpeed, 0.2f, 3.0f, 0.1f, value => SlideSpeed = value);
    }

    public void ResetPart(PartReset reset)
    {
        ResetGate();
        reset.Write("closed", true);
    }

    /// <summary>The door slides, or refuses while its solenoid holds it — so a
    /// click drives the part rather than flipping the contact.</summary>
    public PartOperation? Operation => new("guard door", "closed");

    public void Operate(PartOperate op) => Toggle();
}
