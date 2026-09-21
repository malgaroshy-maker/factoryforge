using FactoryForge.TagBus;
using Godot;

namespace FactoryForge.Parts;

/// <summary>
/// A measuring wheel riding the belt, counting pulses (LP-03).
///
/// Everything in this library that happens at a moment has, until now, had to
/// be timed. "Wait two seconds after the photo-eye, then fire the pusher" works
/// on a belt that runs at exactly one speed and stops working the first time
/// anybody touches the VFD — and learning not to write that is most of the
/// point of a conveyor course. An encoder replaces the timer with a distance:
/// latch the count when the eye breaks, fire when the count has advanced by the
/// number of pulses between the eye and the pusher, and the logic survives any
/// line speed at all.
///
/// The wheel reads whichever <see cref="ConveyorBelt"/> it is standing over,
/// and turns at that belt's true surface speed. Place it away from a conveyor
/// and it counts nothing and does not move — the honest failure for a measuring
/// wheel is that it is not touching anything, and the wheel itself is the
/// indicator that says so.
///
/// Local space: the origin is the lane centre, on the work plane, so the
/// encoder drops onto the same grid cell as the belt it measures. The post
/// stands clear on the +Z side.
/// </summary>
public partial class RotaryEncoder : Node3D, IPart
{
    /// <summary>Pulses per metre of belt travel — the encoder's resolution.
    /// 100 is a wheel of about this size on a 1000 ppr encoder, and it makes
    /// a pulse worth a centimetre, which is a number a student can reason
    /// about without a calculator.</summary>
    [Export] public float PulsesPerMetre { get; set; } = 100.0f;

    /// <summary>Measuring wheel radius. It also sets the mounting height: the
    /// wheel has to touch the belt to read it.</summary>
    [Export] public float WheelRadius { get; set; } = 0.05f;

    private const float PostOffsetZ = 0.45f;

    /// <summary>Height of the post head above the work plane. Low: this is a
    /// bracket holding a wheel down on a belt, not a lamp standard, and the
    /// first version stood taller than the conveyor was wide.</summary>
    private const float PostHeadY = 0.27f;
    private const float WheelWidth = 0.035f;

    private float WheelY => PartLayout.BeltSurface + WheelRadius;

    private MeshInstance3D _wheel = null!;
    private Label3D _readout = null!;

    private ConveyorBelt? _belt;
    private float _rescanTimer;

    private double _pulses;
    private float _spin;

    /// <summary>Pulses counted since the last reset.</summary>
    public int Count { get; private set; }

    /// <summary>Pulses per second. This is belt speed expressed in the only
    /// units a counter can see, and it is what a rate alarm watches.</summary>
    public float Rate { get; private set; }

    /// <summary>Belt surface speed under the wheel, m/s. Exposed for tests:
    /// "the count is rising" and "the count is rising at the right rate" are
    /// different claims, and only the second one catches a wrong radius.</summary>
    public float SurfaceSpeed { get; private set; }

    /// <summary>Is the wheel actually on a belt? False is not an error — it is
    /// the reading, and it is why the part is placed over a conveyor and not
    /// beside one.</summary>
    public bool IsTracking => _belt is not null;

    public override void _Ready()
    {
        var frameMat = new StandardMaterial3D
        {
            AlbedoColor = new Color(0.20f, 0.22f, 0.25f),
            Metallic = 0.45f,
            Roughness = 0.50f,
        };

        float postHeight = PartLayout.FloorDrop + PostHeadY;
        AddChild(new MeshInstance3D
        {
            Name = "Post",
            Mesh = new CylinderMesh { TopRadius = 0.017f, BottomRadius = 0.024f, Height = postHeight },
            MaterialOverride = frameMat,
            Position = new Vector3(0, PostHeadY - postHeight / 2.0f, PostOffsetZ),
        });
        AddChild(new MeshInstance3D
        {
            Name = "BaseFoot",
            Mesh = new CylinderMesh { TopRadius = 0.075f, BottomRadius = 0.075f, Height = 0.025f },
            MaterialOverride = frameMat,
            Position = new Vector3(0, -PartLayout.FloorDrop, PostOffsetZ),
        });

        // The sprung arm that holds the wheel down on the belt. Modelled as one
        // bar from the post head to the hub, so the wheel reads as pressed onto
        // the surface rather than as hovering above it.
        var hub = new Vector3(0, WheelY, 0);
        var head = new Vector3(0, PostHeadY - 0.03f, PostOffsetZ);
        Vector3 mid = (hub + head) / 2.0f;
        Vector3 along = head - hub;
        float armLength = along.Length();
        // Aimed by hand rather than with LookAt: the arm is built before it is
        // added to the tree, and LookAt needs a global transform it does not
        // have yet. Rotating about +X by this angle carries the box's own +Z
        // onto (0, dy, dz), which is all the aiming this needs — the arm lies
        // in the YZ plane by construction.
        float aim = Mathf.Atan2(-along.Y, along.Z);
        AddChild(new MeshInstance3D
        {
            Name = "SpringArm",
            Mesh = new BoxMesh { Size = new Vector3(0.03f, 0.03f, armLength) },
            MaterialOverride = frameMat,
            Position = mid,
            Basis = new Basis(Vector3.Right, aim),
        });

        // Encoder body on the hub — the thing the wheel is actually turning.
        AddChild(new MeshInstance3D
        {
            Name = "EncoderBody",
            Mesh = new CylinderMesh
            {
                TopRadius = 0.028f, BottomRadius = 0.028f, Height = 0.06f,
            },
            MaterialOverride = new StandardMaterial3D
            {
                AlbedoColor = new Color(0.16f, 0.17f, 0.20f),
                Metallic = 0.50f,
                Roughness = 0.40f,
            },
            Position = hub + new Vector3(0, 0, WheelWidth / 2.0f + 0.04f),
            Basis = new Basis(Vector3.Right, Mathf.Pi / 2),
        });

        // The wheel. No collision shape: a physics body here would knock the
        // cartons it is supposed to be measuring, and a measuring wheel exerts
        // no force worth modelling. It reads the belt's commanded surface
        // speed instead, which is the same number the belt drives boxes with.
        _wheel = new MeshInstance3D
        {
            Name = "MeasuringWheel",
            Mesh = new CylinderMesh
            {
                TopRadius = WheelRadius, BottomRadius = WheelRadius, Height = WheelWidth,
            },
            MaterialOverride = new StandardMaterial3D
            {
                // Mid grey, not black: this wheel spends its life sitting on a
                // black belt, and a black wheel on a black belt is a wheel
                // nobody can see turning.
                AlbedoColor = new Color(0.34f, 0.35f, 0.37f),
                Metallic = 0.10f,
                Roughness = 0.85f,
            },
            Position = hub,
        };
        AddChild(_wheel);

        // A white spoke, because a plain black wheel turning is invisible —
        // the same reason the selector knob has a flag and the turntable has a
        // stripe.
        _wheel.AddChild(new MeshInstance3D
        {
            Name = "Spoke",
            Mesh = new BoxMesh { Size = new Vector3(0.008f, WheelWidth + 0.004f, WheelRadius * 1.8f) },
            MaterialOverride = new StandardMaterial3D
            {
                AlbedoColor = new Color(0.90f, 0.90f, 0.92f),
                Roughness = 0.60f,
            },
        });

        // A small backplate on the post with the count on it. The first version
        // floated the number in the air above the post, which read as a label
        // on the scene rather than as a display on the machine.
        AddChild(new MeshInstance3D
        {
            Name = "CountPlate",
            Mesh = new BoxMesh { Size = new Vector3(0.13f, 0.045f, 0.012f) },
            MaterialOverride = new StandardMaterial3D
            {
                AlbedoColor = new Color(0.10f, 0.11f, 0.13f),
                Roughness = 0.55f,
            },
            Position = new Vector3(0, PostHeadY + 0.035f, PostOffsetZ + 0.018f),
        });
        _readout = new Label3D
        {
            Name = "CountReadout",
            Text = "0",
            FontSize = 42,
            PixelSize = 0.0008f,
            Modulate = new Color(0.55f, 0.95f, 0.65f),
            Billboard = BaseMaterial3D.BillboardModeEnum.Disabled,
            Position = new Vector3(0, PostHeadY + 0.035f, PostOffsetZ + 0.026f),
        };
        AddChild(_readout);

        ApplySpin();
    }

    /// <summary>How often to look for a belt again while none has been found,
    /// in seconds. An encoder placed before its conveyor has to start working
    /// once the conveyor arrives, and a scan per tick for a node that is not
    /// there is the kind of cost nobody notices until there are thirty of
    /// them.</summary>
    private const float RescanInterval = 0.5f;

    /// <summary>
    /// Integrate one tick. <paramref name="reset"/> zeroes the count while it
    /// is high, exactly like a counter's reset leg — a level, not an edge, so
    /// holding it high holds the count at zero.
    /// </summary>
    public void Step(bool reset, float delta)
    {
        if (_belt is null || !IsInstanceValid(_belt))
        {
            _belt = null;
            _rescanTimer -= delta;
            if (_rescanTimer <= 0.0f)
            {
                _rescanTimer = RescanInterval;
                _belt = FindBeltUnderWheel();
            }
        }

        SurfaceSpeed = _belt is { IsRunning: true } running ? Mathf.Abs(running.Speed) : 0.0f;
        Rate = SurfaceSpeed * PulsesPerMetre;

        if (reset)
        {
            _pulses = 0.0;
            Count = 0;
        }
        else
        {
            // Accumulated as a double and floored, so a rate that is not a
            // whole number of pulses per tick does not quietly lose the
            // remainder — which at 50 Hz and 100 ppm is most of the count.
            _pulses += Rate * delta;
            Count = (int)_pulses;
        }

        _spin += SurfaceSpeed / Mathf.Max(WheelRadius, 0.001f) * delta;
        ApplySpin();

        if (_readout is not null && _readout.Text != Count.ToString())
            _readout.Text = Count.ToString();
    }

    /// <summary>Zero it, for a scene reset.</summary>
    public void ResetCount()
    {
        _pulses = 0.0;
        Count = 0;
        Rate = 0.0f;
        SurfaceSpeed = 0.0f;
        if (_readout is not null) _readout.Text = "0";
    }

    private void ApplySpin()
    {
        if (_wheel is null) return;
        // Composed as a basis, never as euler angles: Godot builds those as
        // Y*X*Z, so the roll would be applied in the mesh's own frame where the
        // cylinder axis is still +Y, and the wheel would tip over instead of
        // turning. Same trap the conveyor drums are built around.
        var lay = new Basis(Vector3.Right, Mathf.Pi / 2);
        var spin = new Basis(Vector3.Up, -_spin);
        _wheel.Basis = lay * spin;
    }

    /// <summary>
    /// The belt this wheel is standing on, or null.
    ///
    /// Found by geometry rather than by a raycast, because a raycast down from
    /// the hub hits whatever carton happens to be passing and would make the
    /// reading flicker with the product on the line. The wheel is asking which
    /// *deck* it is mounted over, and that does not change while the scene
    /// runs.
    /// </summary>
    private ConveyorBelt? FindBeltUnderWheel()
    {
        Node? root = GetTree()?.Root ?? GetParent();
        if (root is null) return null;

        Vector3 here = GlobalPosition;
        ConveyorBelt? best = null;
        float bestDrop = float.MaxValue;

        var stack = new System.Collections.Generic.Stack<Node>();
        stack.Push(root);
        while (stack.Count > 0)
        {
            Node node = stack.Pop();
            foreach (Node child in node.GetChildren()) stack.Push(child);

            if (node is not ConveyorBelt belt) continue;

            Vector3 local = belt.GlobalTransform.AffineInverse() * here;
            if (Mathf.Abs(local.X) > belt.Size.X / 2.0f) continue;
            if (Mathf.Abs(local.Z) > belt.Size.Z / 2.0f) continue;

            // Only decks at or below the wheel, and the nearest one wins — a
            // scene can legitimately stack a belt over a chute.
            float drop = local.Y;
            if (drop < -0.5f || drop > 0.5f) continue;
            if (Mathf.Abs(drop) >= bestDrop) continue;

            bestDrop = Mathf.Abs(drop);
            best = belt;
        }

        return best;
    }

    // ---------- IPart (HP-34)

    /// <summary>The count is an Int because that is what a high-speed counter
    /// hands a program, and the rate is a Float because it is a measurement --
    /// the same split as the light curtain's `blocked` and `height`.</summary>
    public void DeclareTags(PartTagBuilder tags) => tags
        .Int("count", $"Encoder {tags.Index} Count", TagKind.Input)
        .Float("rate", $"Encoder {tags.Index} Rate (pulses/s)", TagKind.Input)
        // A level, not an edge: holding the reset leg high holds the count at
        // zero, exactly like a counter's own reset.
        .Bit("reset", $"Encoder {tags.Index} Reset", TagKind.Output);

    public void CaptureSettings(PartSettings settings)
    {
        settings.Put("pulses_per_metre", PulsesPerMetre);
        settings.Put("wheel_radius", WheelRadius);
    }

    public void ApplySettings(PartSettings settings)
    {
        if (settings.Number("pulses_per_metre") is { } ppm) PulsesPerMetre = ppm;
        if (settings.Number("wheel_radius") is { } radius) WheelRadius = radius;
    }

    public void StepPart(PartTick tick)
    {
        Step(tick.Bit("reset"), tick.Dt);
        tick.Write("count", Count);
        tick.Write("rate", (double)Rate);
    }

    /// <summary>Wheel radius is build-time geometry, so it is not offered
    /// here.</summary>
    public void DescribeControls(IPartInspector ui) =>
        ui.Slider("Pulses / metre", PulsesPerMetre, 10.0f, 1000.0f, 10.0f,
                  value => PulsesPerMetre = value);

    public void ResetPart(PartReset reset)
    {
        ResetCount();
        reset.Write("count", 0);
        reset.Write("rate", 0.0);
    }
}
