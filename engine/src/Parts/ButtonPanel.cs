using System.Collections.Generic;
using FactoryForge.TagBus;
using Godot;

namespace FactoryForge.Parts;

/// <summary>Which cap on the panel a click landed on.</summary>
public enum PanelButton
{
    Start,
    Stop,
    Reset,
    EmergencyStop,
}

/// <summary>
/// Industrial control panel: three momentary pushbuttons, a latching emergency
/// stop, and two indicator lamps.
///
/// The lamps came first and the panel was output-only for a long time, which
/// meant every input the PLC could see was one the simulation computed for it.
/// You could watch a program run but never drive it — no Start, no way to inject
/// the fault the logic is supposed to survive. These buttons are the other half.
///
/// Two press behaviours, because real panels have both:
///
/// * <b>Momentary</b> (Start, Stop, Reset) — one click is one rising edge, held
///   for <see cref="PressHold"/> and then dropped, whatever the mouse does. A
///   held mouse button is not a held contact; anything else would let a slow
///   click look like a stuck one. See <see cref="StepPart"/> for why the edge
///   is held rather than one scan wide.
/// * <b>Maintained</b> (Emergency Stop) — the mushroom latches in when struck
///   and stays in until it is twisted back out. Clicking it again releases it.
///
/// The E-stop is wired <b>normally closed</b>, like the real thing: its tag is
/// <i>true while the circuit is healthy</i> and goes false when the mushroom is
/// struck. That is not a detail worth hiding — a program that runs while the
/// wire to the E-stop is cut is exactly the bug NC wiring exists to prevent.
/// </summary>
public partial class ButtonPanel : Node3D, IPart, IDialPart
{
    [Signal] public delegate void ButtonPressedEventHandler(string name);

    /// <summary>
    /// How long a click holds a momentary contact closed, in seconds -- and,
    /// after it opens, the least time it stays open before another click can
    /// close it again (IP-31).
    ///
    /// Long enough for the slowest poll a supported driver makes. Polling is
    /// the default everywhere (gotcha 1): OpenPLC's Modbus master polls every
    /// 100 ms by default (50 ms in docs/OPENPLC.md), the Node-RED example
    /// subscribes at 50 ms, and the Siemens drivers' own loops run at 50 ms.
    /// 200 ms is twice the slowest of those and still reads as one press to
    /// the hand -- a real one lasts about that long. A link that samples more
    /// slowly needs this raised, which is why it is a setting: an OPC UA
    /// <i>subscription</i> to an S7-1500 publishes at 1000 ms whatever it is
    /// asked for, and only a hold over a second survives that.
    ///
    /// Counted in physics ticks, not in simulated seconds: Godot paces ticks by
    /// the wall clock at every time scale and scales only their delta, and the
    /// poller that has to see the edge is on the wall clock too. Simulated time
    /// would shrink 200 ms to 50 ms at the toolbar's 4x.
    /// </summary>
    public float PressHold { get; set; } = DefaultPressHold;

    public const float DefaultPressHold = 0.2f;

    /// <summary><see cref="PressHold"/> in whole physics ticks, rounded up and
    /// never less than one.</summary>
    public int PressHoldTicks =>
        System.Math.Max(1, (int)System.Math.Ceiling(PressHold * Engine.PhysicsTicksPerSecond - 1e-3));

    private const float CapTravel = 0.012f;
    private const float FaceZ = 0.06f;

    private MeshInstance3D _greenLamp = null!;
    private MeshInstance3D _redLamp = null!;
    private StandardMaterial3D _greenMat = null!;
    private StandardMaterial3D _redMat = null!;

    public bool IsGreenOn { get; private set; }
    public bool IsRedOn { get; private set; }

    /// <summary>True while the mushroom is struck in. The tag reports the
    /// inverse, because the contact is normally closed.</summary>
    public bool EmergencyStopEngaged { get; private set; }

    // --- setpoint pot -----------------------------------------------------
    //
    // The one number each scene is really about -- the level to hold, the
    // height that counts as tall, the weight that counts as a reject -- used
    // to be a constant inside a Python file. A panel with Start and Stop but
    // no way to change what the line is aiming at is half an operator station
    // (OP-01).
    //
    // The tag carries **engineering units, not percent**. A percent tag would
    // need the controller to know the same range the scale plate shows, and
    // two copies of one range is one copy too many: the template would say
    // grams and the driver would decide how many. Here the template owns the
    // range, the panel prints it on the plate, and the controller reads a
    // number that already means what it says.

    /// <summary>Bottom of the scale plate, in whatever the scene measures.</summary>
    public float SetpointMin { get; set; } = 0.0f;

    /// <summary>Top of the scale plate.</summary>
    public float SetpointMax { get; set; } = 100.0f;

    /// <summary>What the plate is graduated in — shown beside the value, never
    /// interpreted. "%", "g", "mm", "pcs".</summary>
    public string SetpointUnit { get; set; } = "%";

    /// <summary>Where the pointer is now, clamped into
    /// [<see cref="SetpointMin"/>, <see cref="SetpointMax"/>].</summary>
    public float Setpoint { get; private set; } = 50.0f;

    /// <summary>Sweep of a real panel pot: 270°, not a full turn, so the
    /// pointer's angle is unambiguous about which end it is at.</summary>
    private const float DialSweepDegrees = 270.0f;

    /// <summary>Screen pixels of vertical drag for one full sweep. Roughly a
    /// window-height of travel, which is coarse enough to reach either end in
    /// one gesture and fine enough to land on a specific value.</summary>
    private const float DialPixelsPerSweep = 260.0f;

    /// <summary>What the scale plate currently reads. The one observable that
    /// proves a change to the range or the unit actually reached the
    /// instrument rather than just the field it was typed in.</summary>
    public string PlateText => _dialPlate?.Text ?? "";

    private Node3D _dial = null!;
    private Label3D _dialPlate = null!;
    private Vector3 _dialCentre;
    private float _dialRadius;

    private sealed class Cap
    {
        public PanelButton Which;
        public Vector3 Centre;          // rest position of the cap's face
        public float Radius;
        public bool Maintained;
        public MeshInstance3D Mesh = null!;

        // The momentary contact, stepped on the physics clock (StepPart).
        public ContactPhase Phase;
        public int TicksLeft;           // ticks left in High or Low after this one
        public bool Requested;          // a click the next tick has not taken yet
    }

    /// <summary>Where a momentary contact is in its press. Idle: open and free
    /// to close. High: closed, counting down the hold. Low: open again, and
    /// counting down the same time before it may close.</summary>
    private enum ContactPhase { Idle, High, Low }

    private readonly List<Cap> _caps = new();

    public override void _Ready()
    {
        var panelMat = new StandardMaterial3D
        {
            AlbedoColor = new Color(0.25f, 0.26f, 0.28f),
            Metallic = 0.45f,
            Roughness = 0.45f,
        };

        // The origin is on the work plane like every other part, so the station
        // needs a pedestal down to the floor rather than hovering at belt height.
        AddChild(new MeshInstance3D
        {
            Name = "Pedestal",
            Mesh = new BoxMesh { Size = new Vector3(0.09f, PartLayout.FloorDrop, 0.09f) },
            MaterialOverride = panelMat,
            Position = new Vector3(0, -PartLayout.FloorDrop / 2, 0),
        });
        // Base foot and housing scale together off PartLayout's panel
        // reference (see FF-25) rather than their own one-off numbers, so the
        // station stops reading as its own scale system next to the belt.
        float baseSide = PartLayout.PanelWidth * 0.75f;
        AddChild(new MeshInstance3D
        {
            Name = "BaseFoot",
            Mesh = new BoxMesh { Size = new Vector3(baseSide, 0.03f, baseSide) },
            MaterialOverride = panelMat,
            Position = new Vector3(0, -PartLayout.FloorDrop, 0),
        });

        // Main housing box, sitting on top of the pedestal
        AddChild(new MeshInstance3D
        {
            Mesh = new BoxMesh { Size = new Vector3(PartLayout.PanelWidth, PartLayout.PanelHeight, 0.12f) },
            MaterialOverride = panelMat,
            Position = new Vector3(0, PartLayout.PanelHeight / 2, 0),
        });

        // Button and lamp centres are fractions of the housing footprint
        // rather than absolute metres, so resizing the reference in
        // PartLayout moves the whole layout together instead of leaving caps
        // stranded off the edge of a resized housing.
        float lampY = PartLayout.PanelHeight * 0.767f;
        float rowY = PartLayout.PanelHeight * 0.522f;
        float estopY = PartLayout.PanelHeight * 0.233f;
        float lampX = PartLayout.PanelWidth * 0.20f;
        float capX = PartLayout.PanelWidth * 0.30f;

        // Green indicator lamp
        _greenMat = new StandardMaterial3D { AlbedoColor = new Color(0.1f, 0.4f, 0.15f) };
        _greenLamp = new MeshInstance3D
        {
            Mesh = new SphereMesh { Radius = 0.04f, Height = 0.08f },
            Position = new Vector3(-lampX, lampY, 0.07f),
            MaterialOverride = _greenMat,
        };
        AddChild(_greenLamp);

        // Red indicator lamp
        _redMat = new StandardMaterial3D { AlbedoColor = new Color(0.4f, 0.1f, 0.1f) };
        _redLamp = new MeshInstance3D
        {
            Mesh = new SphereMesh { Radius = 0.04f, Height = 0.08f },
            Position = new Vector3(lampX, lampY, 0.07f),
            MaterialOverride = _redMat,
        };
        AddChild(_redLamp);

        // Momentary row, then the mushroom below it where a palm can find it.
        // Cap and mushroom radii stay absolute: a real 22mm pushbutton is the
        // same size on any panel, a fixed human-hand-scale actuator rather
        // than something that should grow with the housing around it.
        AddCap(PanelButton.Start, new Vector3(-capX, rowY, FaceZ), 0.030f,
               new Color(0.15f, 0.70f, 0.25f), maintained: false, "Start");
        AddCap(PanelButton.Stop, new Vector3(0.0f, rowY, FaceZ), 0.030f,
               new Color(0.12f, 0.12f, 0.14f), maintained: false, "Stop");
        AddCap(PanelButton.Reset, new Vector3(capX, rowY, FaceZ), 0.030f,
               new Color(0.20f, 0.35f, 0.72f), maintained: false, "Reset");
        AddCap(PanelButton.EmergencyStop, new Vector3(0.0f, estopY, FaceZ), 0.050f,
               new Color(0.85f, 0.10f, 0.10f), maintained: true, "E-Stop");

        // The pot sits beside the mushroom, clear of its collar, on the same
        // row: both are things a hand reaches for rather than a fingertip.
        BuildDial(new Vector3(PartLayout.PanelWidth * 0.325f, estopY, FaceZ), 0.038f);
        // A scale plate in the gap between the button row and the mushroom,
        // spanning the housing rather than centred under the knob — a value
        // like "3000 g" is wider than the knob and would hang off the edge of
        // the panel anywhere else. The gap is about 8 cm tall, so the text has
        // to be small; sized any larger it lands across the mushroom, which is
        // the one control on the panel that must never be hard to read.
        BuildPlate(PartLayout.PanelHeight * 0.407f);
        ApplySetpoint(Setpoint);
    }

    /// <summary>The knob: a body facing the operator with a pointer bar across
    /// it. The pointer is a child of a node that turns about Z, so setting the
    /// setpoint is one rotation rather than trigonometry per part.</summary>
    private void BuildDial(Vector3 centre, float radius)
    {
        _dialCentre = centre;
        _dialRadius = radius;

        _dial = new Node3D { Name = "SetpointDial", Position = centre };
        AddChild(_dial);

        // A bezel behind the knob, the same trick the caps use: a control that
        // sits in something reads as a control, where one floating on flat
        // panel reads as a smudge.
        var bezel = new MeshInstance3D
        {
            Name = "DialBezel",
            Mesh = new CylinderMesh
            {
                TopRadius = radius * 1.42f,
                BottomRadius = radius * 1.42f,
                Height = 0.010f,
            },
            MaterialOverride = new StandardMaterial3D
            {
                AlbedoColor = new Color(0.55f, 0.57f, 0.60f),
                Metallic = 0.40f,
                Roughness = 0.50f,
            },
            Position = new Vector3(centre.X, centre.Y, centre.Z - 0.004f),
        };
        bezel.RotateX(Mathf.Pi / 2);
        AddChild(bezel);

        var body = new MeshInstance3D
        {
            Name = "DialBody",
            Mesh = new CylinderMesh { TopRadius = radius * 0.92f, BottomRadius = radius, Height = 0.024f },
            MaterialOverride = new StandardMaterial3D
            {
                AlbedoColor = new Color(0.20f, 0.21f, 0.24f),
                Metallic = 0.30f,
                Roughness = 0.38f,
            },
        };
        body.RotateX(-Mathf.Pi / 2);   // flat face outward, toward +Z
        _dial.AddChild(body);

        // White pointer, offset toward the rim so the eye reads an angle
        // rather than a spot.
        _dial.AddChild(new MeshInstance3D
        {
            Name = "DialPointer",
            Mesh = new BoxMesh { Size = new Vector3(0.009f, radius * 0.95f, 0.005f) },
            MaterialOverride = new StandardMaterial3D
            {
                AlbedoColor = new Color(0.97f, 0.97f, 0.94f),
                EmissionEnabled = true,
                Emission = new Color(0.85f, 0.85f, 0.80f),
                EmissionEnergyMultiplier = 1.4f,
            },
            Position = new Vector3(0, radius * 0.46f, 0.015f),
        });

        // End stops, so the sweep has visible limits and the knob does not
        // look like it could turn forever.
        var tickMat = new StandardMaterial3D { AlbedoColor = new Color(0.70f, 0.70f, 0.72f) };
        foreach (float end in new[] { -1.0f, 1.0f })
        {
            float angle = Mathf.DegToRad(DialSweepDegrees / 2.0f) * end;
            AddChild(new MeshInstance3D
            {
                Name = end < 0 ? "DialTickMin" : "DialTickMax",
                Mesh = new BoxMesh { Size = new Vector3(0.005f, 0.014f, 0.004f) },
                MaterialOverride = tickMat,
                // In front of the face, not behind it: at centre.Z - 0.004 the
                // stops sat inside the housing box and never rendered at all.
                Position = centre + new Vector3(Mathf.Sin(angle) * radius * 1.62f,
                                                Mathf.Cos(angle) * radius * 1.62f, 0.002f),
                Rotation = new Vector3(0, 0, -angle),
            });
        }
    }

    private void BuildPlate(float y)
    {
        _dialPlate = new Label3D
        {
            Name = "SetpointPlate",
            // The house size for an in-world readout (see WeighingConveyor):
            // FontSize x PixelSize is the only thing that decides how tall the
            // text really is, and every tuned readout in the project uses the
            // same PixelSize so they stay comparable.
            FontSize = 40,
            PixelSize = 0.0012f,
            Modulate = new Color(0.95f, 0.86f, 0.45f),
            OutlineSize = 0,
            Position = new Vector3(0, y, FaceZ + 0.005f),
            Billboard = BaseMaterial3D.BillboardModeEnum.Disabled,
            NoDepthTest = false,
        };
        AddChild(_dialPlate);
    }

    private void AddCap(PanelButton which, Vector3 centre, float radius, Color colour,
                        bool maintained, string label)
    {
        var collarMat = new StandardMaterial3D
        {
            AlbedoColor = maintained ? new Color(0.85f, 0.72f, 0.10f) : new Color(0.16f, 0.17f, 0.19f),
            Metallic = 0.30f,
            Roughness = 0.55f,
        };

        // Bezel: the ring the cap sits in, so a pressed cap sinks into something
        // rather than into flat panel.
        var collar = new MeshInstance3D
        {
            Name = $"{label}Collar",
            Mesh = new CylinderMesh
            {
                TopRadius = radius * 1.28f,
                BottomRadius = radius * 1.28f,
                Height = 0.012f,
            },
            MaterialOverride = collarMat,
            Position = new Vector3(centre.X, centre.Y, centre.Z - 0.002f),
        };
        collar.RotateX(Mathf.Pi / 2);
        AddChild(collar);

        var cap = new MeshInstance3D
        {
            Name = $"{label}Cap",
            Mesh = new CylinderMesh
            {
                // The mushroom flares out; the momentary caps are straight.
                TopRadius = maintained ? radius : radius * 0.94f,
                BottomRadius = radius * 0.80f,
                Height = maintained ? 0.030f : 0.018f,
            },
            MaterialOverride = new StandardMaterial3D
            {
                AlbedoColor = colour,
                Metallic = 0.05f,
                Roughness = 0.40f,
            },
            Position = centre,
        };
        cap.RotateX(-Mathf.Pi / 2);   // flat face outward, toward +Z
        AddChild(cap);

        _caps.Add(new Cap
        {
            Which = which,
            Centre = centre,
            Radius = radius,
            Maintained = maintained,
            Mesh = cap,
        });
    }

    /// <summary>
    /// Which cap, if any, the ray strikes. Tested against each cap's own sphere
    /// rather than the panel's bounding box: the box covers the housing, the
    /// pedestal and both lamps, so hit-testing it would fire a button wherever
    /// on the station you clicked.
    /// </summary>
    public PanelButton? HitTest(Vector3 worldOrigin, Vector3 worldDirection)
    {
        var toLocal = GlobalTransform.AffineInverse();
        Vector3 origin = toLocal * worldOrigin;
        Vector3 dir = (toLocal.Basis * worldDirection).Normalized();

        PanelButton? best = null;
        float nearest = float.MaxValue;

        foreach (var cap in _caps)
        {
            // A finger is blunter than a pixel; give the cap a little margin so
            // clipping the rim still counts as a press.
            if (RayHit.Sphere(origin, dir, cap.Centre, cap.Radius * 1.15f) is not { } t) continue;
            if (t >= nearest) continue;

            nearest = t;
            best = cap.Which;
        }

        return best;
    }

    /// <summary>
    /// Did the ray land on the setpoint knob? Separate from
    /// <see cref="HitTest"/> because the two gestures are different: a cap is
    /// pressed, a pot is turned, and a click that begins a drag must not also
    /// fire a button.
    /// </summary>
    public bool HitTestDial(Vector3 worldOrigin, Vector3 worldDirection)
    {
        var toLocal = GlobalTransform.AffineInverse();
        Vector3 origin = toLocal * worldOrigin;
        Vector3 dir = (toLocal.Basis * worldDirection).Normalized();
        return RayHit.Sphere(origin, dir, _dialCentre, _dialRadius * 1.20f) is not null;
    }

    /// <summary>Set the pointer to an absolute value, clamped to the plate.
    /// This is also how a forced tag turns the knob: the panel yields the
    /// number to whoever is driving it and shows what it was told (OP-02).</summary>
    public void SetSetpoint(float value)
    {
        ApplySetpoint(value);
    }

    /// <summary>Turn the pot by a drag. <paramref name="pixelsUp"/> is screen
    /// travel, positive upward, so dragging up raises the setpoint the way a
    /// slider would — a knob whose value fell when you dragged up would be
    /// technically defensible and universally hated.</summary>
    public void DragSetpoint(float pixelsUp)
    {
        float span = SetpointMax - SetpointMin;
        if (span <= 0.0f) return;
        ApplySetpoint(Setpoint + pixelsUp / DialPixelsPerSweep * span);
    }

    /// <summary>Declare what the plate is graduated in. Called when a template
    /// applies its properties, before the geometry exists — hence the null
    /// checks: the pointer catches up in <see cref="_Ready"/>.</summary>
    public void ConfigureSetpoint(float min, float max, string unit, float initial)
    {
        SetpointMin = min;
        SetpointMax = max;
        SetpointUnit = unit;
        ApplySetpoint(initial);
    }

    private void ApplySetpoint(float value)
    {
        float span = SetpointMax - SetpointMin;
        Setpoint = span > 0.0f ? Mathf.Clamp(value, SetpointMin, SetpointMax) : SetpointMin;

        if (_dial is not null)
        {
            float fraction = span > 0.0f ? (Setpoint - SetpointMin) / span : 0.0f;
            float angle = Mathf.DegToRad((fraction - 0.5f) * DialSweepDegrees);
            _dial.Rotation = new Vector3(0, 0, -angle);
        }

        if (_dialPlate is not null)
        {
            // Whole numbers where the range is coarse enough for them to be
            // the honest reading, one decimal where it is not: a plate reading
            // "0 m" for a 0.15 m threshold is a broken instrument.
            string text = span >= 20.0f
                ? Setpoint.ToString("0")
                : Setpoint.ToString("0.00");
            _dialPlate.Text = $"SP {text} {SetpointUnit}".TrimEnd();
        }
    }

    /// <summary>
    /// Register a press. A momentary cap requests one edge no matter how often
    /// it is clicked before the next tick reads it -- a click lands on the frame
    /// clock and the tags are written on the physics clock, so the request is
    /// held here rather than lost between the two. What happens to it then is
    /// <see cref="StepPart"/>'s business. The mushroom toggles its latch, which
    /// is what "twist to release" amounts to.
    /// </summary>
    public void Press(PanelButton which)
    {
        foreach (var cap in _caps)
        {
            if (cap.Which != which) continue;

            if (cap.Maintained)
            {
                EmergencyStopEngaged = !EmergencyStopEngaged;
                cap.Mesh.Position = cap.Centre - new Vector3(0, 0, EmergencyStopEngaged ? CapTravel : 0);
            }
            else
            {
                cap.Requested = true;
                // Down at once, so the click is acknowledged in the frame it
                // was made -- unless the contact is still re-opening from the
                // last press, in which case it goes down when it really closes.
                if (cap.Phase != ContactPhase.Low) ShowDepressed(cap, true);
            }

            EmitSignal(SignalName.ButtonPressed, which.ToString());
            return;
        }
    }

    /// <summary>
    /// Take the clicks no tick has acted on yet, and forget them. For a test
    /// asking "did that click request a press?" -- the tag dispatch itself reads
    /// the requests in <see cref="StepPart"/>.
    /// </summary>
    public IReadOnlyList<PanelButton> ConsumePresses()
    {
        var taken = new List<PanelButton>();
        foreach (var cap in _caps)
        {
            if (!cap.Requested) continue;
            cap.Requested = false;
            taken.Add(cap.Which);
            if (cap.Phase != ContactPhase.High) ShowDepressed(cap, false);
        }
        return taken;
    }

    /// <summary>True while this momentary button's contact is closed -- what its
    /// tag reports, and what the cap shows.</summary>
    public bool IsHeld(PanelButton which)
    {
        foreach (var cap in _caps)
        {
            if (cap.Which == which && !cap.Maintained) return cap.Phase == ContactPhase.High;
        }
        return false;
    }

    /// <summary>Drop queued presses, open every contact and pop the mushroom
    /// back out. Used by a scene reset, so a run never starts holding a stale
    /// E-stop or half a press.</summary>
    public void ResetButtons()
    {
        EmergencyStopEngaged = false;
        foreach (var cap in _caps)
        {
            cap.Requested = false;
            cap.Phase = ContactPhase.Idle;
            cap.TicksLeft = 0;
            cap.Mesh.Position = cap.Centre;
        }
    }

    private static void ShowDepressed(Cap cap, bool down) =>
        cap.Mesh.Position = cap.Centre - new Vector3(0, 0, down ? CapTravel : 0);

    public void SetGreenLamp(bool on)
    {
        IsGreenOn = on;
        _greenMat.AlbedoColor = on ? new Color(0.2f, 1.0f, 0.3f) : new Color(0.1f, 0.4f, 0.15f);
        _greenMat.EmissionEnabled = on;
        _greenMat.Emission = on ? new Color(0.2f, 1.0f, 0.3f) : Colors.Black;
        _greenMat.EmissionEnergyMultiplier = on ? 2.0f : 0.0f;
    }

    public void SetRedLamp(bool on)
    {
        IsRedOn = on;
        _redMat.AlbedoColor = on ? new Color(1.0f, 0.2f, 0.2f) : new Color(0.4f, 0.1f, 0.1f);
        _redMat.EmissionEnabled = on;
        _redMat.Emission = on ? new Color(1.0f, 0.2f, 0.2f) : Colors.Black;
        _redMat.EmissionEnergyMultiplier = on ? 2.0f : 0.0f;
    }

    // ---------- IPart (HP-34)

    /// <summary>Tag suffix for each cap. One table rather than two literal
    /// lists, so registration and dispatch cannot drift apart and leave a
    /// button that presses nothing.</summary>
    public static string TagSuffix(PanelButton which) => which switch
    {
        PanelButton.Start => "start",
        PanelButton.Stop => "stop",
        PanelButton.Reset => "reset",
        PanelButton.EmergencyStop => "estop",
        _ => which.ToString().ToLowerInvariant(),
    };

    /// <summary>The same table backwards, so a click that arrives as a region
    /// name finds the cap it belongs to.</summary>
    public static PanelButton? ButtonFor(string suffix)
    {
        foreach (PanelButton which in System.Enum.GetValues<PanelButton>())
        {
            if (TagSuffix(which) == suffix) return which;
        }
        return null;
    }

    public void DeclareTags(PartTagBuilder tags) => tags
        // The buttons are Inputs: the operator drives them and the controller
        // reads them, exactly like a sensor. `estop` is normally closed, so a
        // healthy circuit reads true and the scene starts in the state a real
        // panel powers up in.
        .Bit("start", $"Panel {tags.Index} Start (momentary)", TagKind.Input)
        .Bit("stop", $"Panel {tags.Index} Stop (momentary)", TagKind.Input)
        .Bit("reset", $"Panel {tags.Index} Reset (momentary)", TagKind.Input)
        .Bit("estop", $"Panel {tags.Index} E-Stop OK (NC)", TagKind.Input, initial: true)
        // The setpoint pot, in the scene's own engineering units -- the
        // template owns the range, not the controller (OP-01).
        .Float("setpoint", $"Panel {tags.Index} Setpoint", TagKind.Input)
        .Bit("green", $"Panel {tags.Index} Green Lamp", TagKind.Output)
        .Bit("red", $"Panel {tags.Index} Red Lamp", TagKind.Output);

    public void CaptureSettings(PartSettings settings)
    {
        // The scale plate the pot is graduated against, plus where the pointer
        // was left. Saving the live value rather than a separate "default" is
        // deliberate: a real pot does not spring back, and a scene reloaded
        // mid-tuning should reopen where you left it.
        settings.Put("setpoint_min", SetpointMin);
        settings.Put("setpoint_max", SetpointMax);
        settings.Put("setpoint_unit", SetpointUnit);
        settings.Put("setpoint", Setpoint);
        settings.Put("press_hold", PressHold);
    }

    public void ApplySettings(PartSettings settings)
    {
        if (settings.Number("setpoint_min") is { } min) SetpointMin = min;
        if (settings.Number("setpoint_max") is { } max) SetpointMax = max;
        if (settings.Text("setpoint_unit") is { } unit) SetpointUnit = unit;
        if (settings.Number("press_hold") is { } hold) PressHold = hold;
        // Last, so the clamp sees the range this template asked for rather than
        // the default 0-100 one.
        if (settings.Number("setpoint") is { } value) SetSetpoint(value);
    }

    /// <summary>
    /// One tick of the panel.
    ///
    /// Momentary buttons are the delicate part: <b>one click, one edge, held
    /// long enough to be seen.</b>
    ///
    /// The click arrives on the frame clock and the tags are written on the
    /// physics clock, so a click only <i>requests</i> a press and this is where
    /// the contact moves. It used to close for exactly one tick -- a clean edge
    /// for a program scanning inside the engine, and it guarded a real
    /// mistake: a held mouse button is not a held contact. But no controller
    /// scans inside the engine. Every one reads the tag through a transport
    /// that samples it, and a Modbus master polling every 50 ms saw 12 of 50
    /// one-tick clicks and missed the other 38 (IP-31,
    /// tools/click_poll_repro.py). The grader held its presses 150 ms, so a
    /// program could pass the grader and still ignore the Start button.
    ///
    /// So a press is now a real contact with a minimum on-time and a minimum
    /// off-time, both <see cref="PressHold"/>:
    /// <list type="bullet">
    /// <item>A click on an open contact closes it on the next tick, and it stays
    /// closed for the hold however long the mouse is down -- still one edge
    /// per click.</item>
    /// <item>A click while it is closed is the same press: the button is
    /// already down and cannot be pressed again. A double-click is one
    /// press, not a Start and a phantom second Start, which matters to every
    /// program that toggles on a button.</item>
    /// <item>A click while it is re-opening is kept, and closes the contact
    /// once it has been open for the hold. Two deliberate presses stay two
    /// edges, each with a low gap on both sides a poller can see. Dropping
    /// it would lose a press the cap was seen to take; raising it at once
    /// would leave a gap one tick wide, and a poller reads that as one long
    /// press.</item>
    /// </list>
    ///
    /// Written every tick, so the tag is always what the contact is -- and a
    /// rename carries a press in progress with it, because the state lives on
    /// the cap and not on a tag id.
    ///
    /// The E-stop is level, not edge, and inverted: the contact is normally
    /// closed, so the tag is true while the circuit is healthy.
    /// </summary>
    public void StepPart(PartTick tick)
    {
        if (tick.TryBit("green", out bool green)) SetGreenLamp(green);
        if (tick.TryBit("red", out bool red)) SetRedLamp(red);

        int hold = PressHoldTicks;
        foreach (var cap in _caps)
        {
            if (cap.Maintained) continue;
            StepContact(cap, hold);
            tick.Write(TagSuffix(cap.Which), cap.Phase == ContactPhase.High);
        }

        tick.Write("estop", !EmergencyStopEngaged);

        // The pot goes both ways (OP-02). Normally the knob is the authority and
        // the tag reports it. While the tag is *forced* -- by the Tag Inspector,
        // or by tools/try_scene.py driving the scene headless -- the force is
        // the authority and the knob turns to match, so a setpoint changed over
        // the wire is visible on the panel instead of leaving the pointer lying
        // about where the line is aimed.
        if (tick.IdFor("setpoint") is not { } setpointId) return;
        if (!tick.Tags.Contains(setpointId)) return;

        if (tick.Tags.IsForced(setpointId))
            SetSetpoint((float)System.Convert.ToDouble(tick.Tags.Visible(setpointId)));
        else
            tick.Tags.Set(setpointId, (double)Setpoint);
    }

    /// <summary>Move one momentary contact on by a tick. See
    /// <see cref="StepPart"/> for the rules.</summary>
    private static void StepContact(Cap cap, int hold)
    {
        switch (cap.Phase)
        {
            case ContactPhase.High:
                // Already down: a click now is the press in progress.
                cap.Requested = false;
                if (cap.TicksLeft > 0) { cap.TicksLeft--; return; }
                cap.Phase = ContactPhase.Low;
                cap.TicksLeft = hold - 1;
                ShowDepressed(cap, false);
                return;

            case ContactPhase.Low:
                if (cap.TicksLeft > 0) { cap.TicksLeft--; return; }
                cap.Phase = ContactPhase.Idle;
                break;   // open long enough; a waiting click may close it now
        }

        if (!cap.Requested) return;
        cap.Requested = false;
        cap.Phase = ContactPhase.High;
        cap.TicksLeft = hold - 1;
        ShowDepressed(cap, true);
    }

    public void DescribeControls(IPartInspector ui)
    {
        // The pot's scale plate. A panel dragged in from the palette used to get
        // the hardcoded 0-100 "%" default with no way to change it, so only the
        // shipped templates -- which set these in their JSON -- had a setpoint
        // that meant anything (OP-01). Every value here is read live by
        // ApplySetpoint, so none of them needs a rebuild.
        ui.Slider("Scale Min", SetpointMin, -10000.0f, 10000.0f, 1.0f,
                  value => ConfigureSetpoint(value, SetpointMax, SetpointUnit, Setpoint));
        ui.Slider("Scale Max", SetpointMax, -10000.0f, 10000.0f, 1.0f,
                  value => ConfigureSetpoint(SetpointMin, value, SetpointUnit, Setpoint));
        ui.Text("Scale Unit", SetpointUnit, 6,
                text => ConfigureSetpoint(SetpointMin, SetpointMax, text, Setpoint));
        // Last, and clamped by the range above it: a setpoint typed outside the
        // plate is not a setpoint, it is a mislabelled instrument.
        ui.Slider("Setpoint", Setpoint, -10000.0f, 10000.0f, 0.01f, value => SetSetpoint(value));
        // How long a click holds Start/Stop/Reset closed (IP-31). Raise it for
        // a link that samples slower than 200 ms.
        ui.Slider("Press Hold (s)", PressHold, 0.05f, 2.0f, 0.05f, value => PressHold = value);
    }

    public void ResetPart(PartReset reset)
    {
        // A reset must not start the next run holding a struck E-stop, and must
        // not deliver a press queued before the reset.
        ResetButtons();
        foreach (var cap in _caps)
        {
            if (!cap.Maintained) reset.Write(TagSuffix(cap.Which), false);
        }
        reset.Write("estop", true);
    }

    /// <summary>Precise: the caps and the knob are separately clickable, and the
    /// bounding box covers the housing, the pedestal and both lamps — it would
    /// turn the whole station into one big Start button.</summary>
    public PartOperation? Operation => new("panel", Precise: true);

    public string? HitTestRegion(Vector3 from, Vector3 direction)
    {
        // The knob is tested first and reported as its own region, because the
        // two gestures are different: a cap is pressed, a pot is turned, and a
        // click that begins a drag must not also fire a button.
        if (HitTestDial(from, direction)) return PartOperate.DialRegion;
        return HitTest(from, direction) is { } which ? TagSuffix(which) : null;
    }

    public void Operate(PartOperate op)
    {
        if (ButtonFor(op.Region) is { } which) Press(which);
    }

    // ---------- IDialPart

    /// <summary>A pot with min == max cannot be turned and is not worth naming
    /// in the hint bar.</summary>
    public bool DialTurnable => SetpointMax > SetpointMin;

    public string DialTagSuffix => "setpoint";

    public void TurnDial(float pixelsUp) => DragSetpoint(pixelsUp);

    /// <summary>IP-07. The panel's only outputs are its two lamps, and what a
    /// lamp does is light: both lenses have to glow. The buttons are the
    /// operator's, driven through the click path that <c>--self-test=buttons</c>
    /// and <c>--self-test=click</c> cover.</summary>
    public PartProbe? Probe => new(
        "the green and red lenses lit (emission on)",
        _ => _greenMat is { EmissionEnabled: true } && _redMat is { EmissionEnabled: true })
    {
        Drive = PartProbe.Drives(("green", true), ("red", true)),
        WithinTicks = 10,
    };
}
