using FactoryForge.TagBus;
using Godot;

namespace FactoryForge.Parts;

/// <summary>
/// A two-hand control station (LP-05).
///
/// The library's safety inputs are the mushroom and the guard door, and both of
/// them are things a program reads. This is the one that teaches what a safety
/// device actually *is*: <c>valid</c> is not <c>left AND right</c>, and a
/// program that ANDs the two bits itself passes its own test while failing the
/// real permissive.
///
/// The rule the relay enforces is simultaneity. Two buttons pressed a second
/// apart is exactly what one taped-down button looks like, so the relay refuses
/// it: both must be held, and their presses must fall within
/// <see cref="SyncWindow"/> of each other. Tape one down, press the other, and
/// nothing happens — which is the entire reason the device exists, and the
/// thing nobody believes until they have watched it refuse.
///
/// A mouse has one pointer, so a click here means "a hand has arrived on this
/// button" and holds it for <see cref="HoldTime"/> rather than for as long as a
/// mouse button is down. That is a departure, and it is the only one: the
/// simultaneity rule, the release rule and the anti-tie-down behaviour are
/// unchanged.
/// </summary>
public partial class TwoHandControl : Node3D, IPart
{
    /// <summary>How far apart two presses may be and still count as
    /// simultaneous, in seconds. 0.5 s is the figure the standards use, and it
    /// is short enough that a deliberate one-hand attempt cannot make it.
    /// </summary>
    [Export] public float SyncWindow { get; set; } = 0.5f;

    /// <summary>How long one click holds its button down, in seconds. This is
    /// the mouse concession; it has to be comfortably longer than
    /// <see cref="SyncWindow"/> or the second hand could never arrive in
    /// time.</summary>
    [Export] public float HoldTime { get; set; } = 2.0f;

    private const float ButtonRadius = 0.055f;
    private const float ButtonSpacing = 0.46f;
    private const float DeckY = 0.34f;
    private const float CapTravel = 0.014f;

    private sealed class Palm
    {
        public Vector3 Centre;
        public MeshInstance3D Mesh = null!;
        public StandardMaterial3D Material = null!;
        public float Held;          // seconds of hold left
        public float PressedAt;     // station clock when the hand arrived
    }

    private readonly Palm[] _palms = new Palm[2];
    private StandardMaterial3D _lampMat = null!;

    /// <summary>Station clock. Presses are timestamped against this rather than
    /// against wall time, so the simultaneity test obeys pause and the
    /// time-scale control like every other part.</summary>
    private float _clock;

    public bool LeftHeld => _palms[0] is { Held: > 0.0f };
    public bool RightHeld => _palms[1] is { Held: > 0.0f };

    /// <summary>The permissive. Both hands on, and they arrived together.
    /// </summary>
    public bool IsValid { get; private set; }

    public override void _Ready()
    {
        var frameMat = new StandardMaterial3D
        {
            AlbedoColor = new Color(0.20f, 0.22f, 0.25f),
            Metallic = 0.45f,
            Roughness = 0.50f,
        };

        float postHeight = PartLayout.FloorDrop + DeckY;
        AddChild(new MeshInstance3D
        {
            Name = "Post",
            Mesh = new BoxMesh { Size = new Vector3(0.09f, postHeight, 0.09f) },
            MaterialOverride = frameMat,
            Position = new Vector3(0, DeckY - postHeight / 2.0f, 0),
        });
        AddChild(new MeshInstance3D
        {
            Name = "BaseFoot",
            Mesh = new BoxMesh { Size = new Vector3(0.30f, 0.03f, 0.30f) },
            MaterialOverride = frameMat,
            Position = new Vector3(0, -PartLayout.FloorDrop, 0),
        });

        // The deck the buttons sit on. Wide enough that the two are further
        // apart than one hand spans, which is the physical half of the
        // interlock and the reason the station looks like this.
        AddChild(new MeshInstance3D
        {
            Name = "Deck",
            Mesh = new BoxMesh { Size = new Vector3(ButtonSpacing + 0.22f, 0.04f, 0.22f) },
            MaterialOverride = new StandardMaterial3D
            {
                AlbedoColor = new Color(0.26f, 0.28f, 0.32f),
                Metallic = 0.40f,
                Roughness = 0.45f,
            },
            Position = new Vector3(0, DeckY, 0),
        });

        var capMat = new StandardMaterial3D
        {
            AlbedoColor = new Color(0.95f, 0.75f, 0.10f),
            Metallic = 0.10f,
            Roughness = 0.40f,
        };

        for (int i = 0; i < 2; i++)
        {
            int side = i == 0 ? -1 : 1;
            var centre = new Vector3(side * ButtonSpacing / 2.0f, DeckY + 0.05f, 0);

            // The shroud. Not decoration: a palm button that can be nudged by a
            // forearm is a palm button that defeats the interlock, so a real one
            // is recessed inside a collar.
            AddChild(new MeshInstance3D
            {
                Name = i == 0 ? "ShroudLeft" : "ShroudRight",
                Mesh = new CylinderMesh
                {
                    TopRadius = ButtonRadius + 0.022f,
                    BottomRadius = ButtonRadius + 0.026f,
                    Height = 0.075f,
                    CapTop = false,
                },
                MaterialOverride = frameMat,
                Position = centre - new Vector3(0, 0.015f, 0),
            });

            var material = (StandardMaterial3D)capMat.Duplicate();
            var mesh = new MeshInstance3D
            {
                Name = i == 0 ? "PalmLeft" : "PalmRight",
                Mesh = new CylinderMesh
                {
                    TopRadius = ButtonRadius, BottomRadius = ButtonRadius, Height = 0.03f,
                },
                MaterialOverride = material,
                Position = centre,
            };
            AddChild(mesh);

            _palms[i] = new Palm { Centre = centre, Mesh = mesh, Material = material };
        }

        // The permissive lamp, between the two buttons where both hands can see
        // it. Without it the refusal is silent, and a silent refusal reads as a
        // broken part rather than as the interlock doing its job.
        _lampMat = new StandardMaterial3D
        {
            AlbedoColor = new Color(0.08f, 0.24f, 0.10f),
            EmissionEnabled = true,
            Emission = new Color(0.25f, 1.0f, 0.35f),
            EmissionEnergyMultiplier = 0.0f,
        };
        AddChild(new MeshInstance3D
        {
            Name = "PermissiveLamp",
            Mesh = new SphereMesh { Radius = 0.035f, Height = 0.07f },
            MaterialOverride = _lampMat,
            Position = new Vector3(0, DeckY + 0.07f, 0),
        });

        AddChild(new Label3D
        {
            Name = "Plate",
            Text = "TWO HAND",
            FontSize = 34,
            PixelSize = 0.0014f,
            Modulate = new Color(0.80f, 0.82f, 0.86f),
            Billboard = BaseMaterial3D.BillboardModeEnum.Disabled,
            Position = new Vector3(0, DeckY + 0.023f, 0.085f),
            Basis = new Basis(Vector3.Right, -Mathf.Pi / 2),
        });
    }

    /// <summary>
    /// Which palm button, if any, the ray strikes — <c>"left"</c> or
    /// <c>"right"</c>.
    ///
    /// Tested against each cap's own sphere rather than the station's bounding
    /// box, for the reason the control panel's own hit test spells out: the box
    /// covers the deck, the post and the lamp, so hit-testing it would press
    /// whichever button happens to be first no matter where you clicked. The
    /// ray goes into local space, so this still works once the station is
    /// rotated.
    /// </summary>
    public string? HitTest(Vector3 worldOrigin, Vector3 worldDirection)
    {
        var toLocal = GlobalTransform.AffineInverse();
        Vector3 origin = toLocal * worldOrigin;
        Vector3 dir = (toLocal.Basis * worldDirection).Normalized();

        string? best = null;
        float nearest = float.MaxValue;

        for (int i = 0; i < _palms.Length; i++)
        {
            if (_palms[i] is not { } palm) continue;
            if (RayHit.Sphere(origin, dir, palm.Centre, ButtonRadius * 1.25f) is not { } t) continue;
            if (t >= nearest) continue;

            nearest = t;
            best = i == 0 ? "left" : "right";
        }

        return best;
    }

    /// <summary>Put a hand on a button. Re-pressing one already held renews the
    /// hold but keeps the original timestamp — otherwise clicking the same
    /// button twice would look like a fresh, simultaneous press and the
    /// tie-down would pass.</summary>
    public void Press(string which)
    {
        int index = which == "left" ? 0 : which == "right" ? 1 : -1;
        if (index < 0 || _palms[index] is not { } palm) return;

        bool wasHeld = palm.Held > 0.0f;
        palm.Held = HoldTime;
        if (!wasHeld) palm.PressedAt = _clock;

        palm.Mesh.Position = palm.Centre - new Vector3(0, CapTravel, 0);
    }

    /// <summary>Take both hands off, for a scene reset.</summary>
    public void ResetStation()
    {
        foreach (var palm in _palms)
        {
            if (palm is null) continue;
            palm.Held = 0.0f;
            palm.Mesh.Position = palm.Centre;
        }
        IsValid = false;
        ApplyLamp();
    }

    /// <summary>Run down the holds and re-evaluate the permissive. On the
    /// physics clock, like everything else that a program can read.</summary>
    public void Step(float delta)
    {
        _clock += delta;

        foreach (var palm in _palms)
        {
            if (palm is null || palm.Held <= 0.0f) continue;
            palm.Held -= delta;
            if (palm.Held <= 0.0f)
            {
                palm.Held = 0.0f;
                palm.Mesh.Position = palm.Centre;
            }
        }

        bool wasValid = IsValid;
        IsValid = LeftHeld && RightHeld
                  && Mathf.Abs(_palms[0].PressedAt - _palms[1].PressedAt) <= SyncWindow;

        if (IsValid != wasValid) ApplyLamp();
    }

    private void ApplyLamp()
    {
        if (_lampMat is null) return;
        _lampMat.EmissionEnergyMultiplier = IsValid ? 2.8f : 0.0f;
        _lampMat.AlbedoColor = IsValid ? new Color(0.35f, 1.0f, 0.45f) : new Color(0.08f, 0.24f, 0.10f);
    }

    // ---------- IPart (HP-34)

    /// <summary>All three are Inputs: the operator drives the buttons and the
    /// *relay* decides the permissive. `valid` is an input to the controller for
    /// the same reason a guard switch is -- the program reads the safety
    /// device's verdict, it does not compute it.</summary>
    public void DeclareTags(PartTagBuilder tags) => tags
        .Bit("left", $"Two-Hand {tags.Index} Left Held", TagKind.Input)
        .Bit("right", $"Two-Hand {tags.Index} Right Held", TagKind.Input)
        .Bit("valid", $"Two-Hand {tags.Index} Permissive", TagKind.Input);

    public void CaptureSettings(PartSettings settings)
    {
        settings.Put("sync_window", SyncWindow);
        settings.Put("hold_time", HoldTime);
    }

    public void ApplySettings(PartSettings settings)
    {
        if (settings.Number("sync_window") is { } window) SyncWindow = window;
        if (settings.Number("hold_time") is { } hold) HoldTime = hold;
    }

    public void StepPart(PartTick tick)
    {
        Step(tick.Dt);
        tick.Write("left", LeftHeld);
        tick.Write("right", RightHeld);
        tick.Write("valid", IsValid);
    }

    public void DescribeControls(IPartInspector ui)
    {
        ui.Slider("Sync Window (s)", SyncWindow, 0.05f, 2.0f, 0.05f, value => SyncWindow = value);
        ui.Slider("Hold Time (s)", HoldTime, 0.5f, 6.0f, 0.1f, value => HoldTime = value);
    }

    public void ResetPart(PartReset reset)
    {
        ResetStation();
        reset.Write("left", false);
        reset.Write("right", false);
        reset.Write("valid", false);
    }

    /// <summary>Precise: two palm buttons far enough apart that one hand cannot
    /// span them. Hit-testing the bounding box would put both of them under
    /// every click, which is precisely the defeat the part exists to
    /// refuse.</summary>
    public PartOperation? Operation => new("two-hand station", Precise: true);

    public string? HitTestRegion(Vector3 from, Vector3 direction) => HitTest(from, direction);

    /// <summary>Drives the part, not the tag: the station decides whether the
    /// two hands arrived together, and publishes the permissive on the next
    /// tick.</summary>
    public void Operate(PartOperate op) => Press(op.Region);
}
