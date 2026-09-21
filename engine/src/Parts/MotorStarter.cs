using FactoryForge.TagBus;
using Godot;

namespace FactoryForge.Parts;

/// <summary>
/// A direct-on-line motor starter: contactor, thermal overload, auxiliary
/// contact and an ammeter.
///
/// This is the most common electrical device in a factory and the library had
/// nothing like it. Every drive here is commanded by a bit the PLC writes
/// straight at the machine, so a student could never build the first circuit
/// anybody is taught — start button, stop button, contactor, seal-in through the
/// auxiliary contact, overload in series with the coil. There was no coil to
/// energise, no auxiliary contact to seal through, and nothing that could trip
/// and stay tripped.
///
/// <b>The starter commands.</b> Its <c>load_tag</c> setting names an output
/// somewhere else in the scene — a conveyor's <c>rotate</c>, a fan's
/// <c>run</c> — and while the contactor is in, that tag is <em>held</em> true;
/// while it is out, it is held false. The PLC drives <c>coil</c>, and the
/// contactor drives the motor, exactly as the wiring does. That is deliberately
/// the opposite of <see cref="SafetyRelay"/>, which only ever <em>permits</em>:
/// a safety contact in series with a coil cannot start anything, and a starter
/// is the thing that does.
///
/// <b>The overload is the point.</b> A thermal overload does not trip on
/// current, it trips on current *and time* — an inverse-time curve, so a motor
/// may draw six times its rating for a second of starting inrush and nothing
/// happens, while a ten percent sustained overload trips eventually. Wire the
/// contactor without the overload contact and the circuit works perfectly until
/// the day it does not, which is the lesson.
/// </summary>
public partial class MotorStarter : Node3D, IPart
{
    /// <summary>The motor's full-load current, amps. The plate rating.</summary>
    [Export] public float FullLoadAmps { get; set; } = 6.0f;

    /// <summary>Mechanical load on the motor as a fraction of its rating, in
    /// percent. A property of the machine being driven, not a signal — which is
    /// why it is a setting and not a tag. Put it above 100 and the overload
    /// trips, after the time its curve allows.</summary>
    [Export] public float LoadPercent { get; set; } = 80.0f;

    /// <summary>Overload trip setting, as a percentage of
    /// <see cref="FullLoadAmps"/>. Real overloads are adjustable and are
    /// adjusted wrong constantly.</summary>
    [Export] public float TripPercent { get; set; } = 115.0f;

    /// <summary>
    /// The trip class: seconds to trip at <em>six times</em> the trip setting,
    /// which is roughly where a motor's starting inrush sits. Ten is a Class 10
    /// overload, which is what most conveyor drives get.
    ///
    /// Stated at six times rather than at two because that is the number the
    /// class is defined by, and because it is the one that has to be big enough
    /// for the machine to start at all. A curve calibrated at two times looks
    /// reasonable and trips on every inrush.
    /// </summary>
    [Export] public float TripTime { get; set; } = 10.0f;

    /// <summary>How long the contactor takes to pull in, seconds. Small, but
    /// not zero: the auxiliary contact is what a seal-in circuit latches
    /// through, and a contact that closed on the same scan as the coil command
    /// would let a student build a seal-in that only appears to work.</summary>
    [Export] public float PullInTime { get; set; } = 0.06f;

    /// <summary>The output tag this starter powers. Empty means it powers
    /// nothing and is an indicator only.</summary>
    [Export] public string LoadTag { get; set; } = "";

    /// <summary>Is the contactor in?</summary>
    public bool IsClosed { get; private set; }

    /// <summary>Has the thermal element opened? Latched: an overload stays
    /// tripped until somebody presses reset, which is the whole difference
    /// between a protective device and a fuse that heals.</summary>
    public bool IsTripped { get; private set; }

    /// <summary>Motor current, amps. Zero with the contactor out.</summary>
    public float Current { get; private set; }

    /// <summary>How far through its trip curve the thermal element is, 0..1.
    /// Exposed because "it is about to trip" is invisible otherwise, and
    /// because a test that waits for a trip should be able to see it
    /// coming.</summary>
    public float ThermalState { get; private set; }

    private float _coilTimer;
    private float _startTimer;

    private const float InrushFactor = 6.0f;
    private const float InrushDecay = 0.45f;

    private StandardMaterial3D _closedLampMat = null!;
    private StandardMaterial3D _trippedLampMat = null!;
    private Label3D _readout = null!;

    // The tag table and id this starter last drove, so a deleted starter
    // releases the motor it was holding instead of leaving it latched by a
    // force nothing owns any more. _ExitTree is the only moment a part hears
    // about its own removal.
    private TagTable? _tags;
    private string _heldTagId = "";

    private const float BoxWidth = 0.26f;
    private const float BoxHeight = 0.34f;
    private const float BoxDepth = 0.16f;
    private const float PanelY = 0.30f;

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

        // A wall-mounted enclosure on a short stand, so it reads as switchgear
        // beside the line rather than as something riding on the belt.
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
                TopRadius = 0.022f, BottomRadius = 0.028f,
                Height = PanelY - BoxHeight / 2.0f + PartLayout.FloorDrop,
            },
            MaterialOverride = trimMat,
            Position = new Vector3(0, (PanelY - BoxHeight / 2.0f - PartLayout.FloorDrop) / 2.0f, 0),
        });

        // The contactor block itself, visible through the door.
        AddChild(new MeshInstance3D
        {
            Name = "ContactorBlock",
            Mesh = new BoxMesh { Size = new Vector3(0.14f, 0.12f, 0.04f) },
            MaterialOverride = trimMat,
            Position = new Vector3(0, PanelY + 0.06f, BoxDepth / 2.0f + 0.02f),
        });

        _closedLampMat = Lamp(new Color(0.06f, 0.24f, 0.10f));
        _trippedLampMat = Lamp(new Color(0.28f, 0.06f, 0.06f));

        AddChild(new MeshInstance3D
        {
            Name = "ClosedLamp",
            Mesh = new SphereMesh { Radius = 0.022f, Height = 0.044f },
            MaterialOverride = _closedLampMat,
            Position = new Vector3(-0.055f, PanelY - 0.08f, BoxDepth / 2.0f + 0.01f),
        });
        AddChild(new MeshInstance3D
        {
            Name = "TrippedLamp",
            Mesh = new SphereMesh { Radius = 0.022f, Height = 0.044f },
            MaterialOverride = _trippedLampMat,
            Position = new Vector3(0.055f, PanelY - 0.08f, BoxDepth / 2.0f + 0.01f),
        });

        // The ammeter. Every other measuring part in the library shows its
        // reading on itself, and a current you have to hunt for in the tag list
        // is a current nobody watches climb.
        _readout = new Label3D
        {
            Name = "AmmeterReadout",
            Text = "0.0 A",
            Position = new Vector3(0, PanelY + BoxHeight / 2.0f + 0.07f, 0),
            Billboard = BaseMaterial3D.BillboardModeEnum.Enabled,
            FontSize = 78,
            PixelSize = 0.0015f,
            Modulate = new Color(0.85f, 0.90f, 1.0f),
        };
        AddChild(_readout);

        Apply();
    }

    private static StandardMaterial3D Lamp(Color dark) => new()
    {
        AlbedoColor = dark,
        Metallic = 0.10f,
        Roughness = 0.35f,
    };

    /// <summary>Clear the thermal element and let the contactor pull in again.
    /// The reset button on the front of the overload block, and in Operate mode
    /// a click on the part is exactly that.</summary>
    public void ResetOverload()
    {
        IsTripped = false;
        ThermalState = 0.0f;
        Apply();
    }

    /// <summary>
    /// One tick of the starter.
    /// </summary>
    /// <param name="coil">Is the coil energised?</param>
    public void Step(bool coil, float delta)
    {
        // The overload contact is in series with the coil, so a tripped starter
        // cannot pull in however hard the controller asks. Resolved first and
        // in one place, the same rule every faultable part here follows.
        bool wants = coil && !IsTripped;

        if (wants)
        {
            _coilTimer += delta;
            if (!IsClosed && _coilTimer >= PullInTime)
            {
                IsClosed = true;
                _startTimer = 0.0f;
            }
        }
        else
        {
            // A contactor drops out on the scan the coil loses power. There is
            // no drop-out delay worth modelling and pretending otherwise would
            // teach the wrong thing about an E-stop.
            _coilTimer = 0.0f;
            IsClosed = false;
        }

        if (IsClosed)
        {
            _startTimer += delta;
            // Starting inrush, decaying to the running current. Locked-rotor
            // current is a property of the *motor* -- six times its full-load
            // rating -- and not a multiple of whatever it happens to be pulling
            // once it is up to speed. Scaling the running current instead made a
            // heavily loaded motor draw eighteen times rated on start, which no
            // overload on earth would survive and which tripped this one every
            // time the test asked it to start.
            float running = FullLoadAmps * Mathf.Max(LoadPercent, 0.0f) / 100.0f;
            float lockedRotor = FullLoadAmps * InrushFactor;
            float decay = Mathf.Exp(-_startTimer / InrushDecay);
            Current = running + Mathf.Max(lockedRotor - running, 0.0f) * decay;
        }
        else
        {
            Current = 0.0f;
        }

        StepThermal(delta);
        Apply();
    }

    /// <summary>
    /// The trip curve. Heating goes as the square of the current over the trip
    /// setting, which is what makes an overload inverse-time: six times the
    /// trip current fills the element in <see cref="TripTime"/>, twice the trip
    /// current takes about twelve times as long, and ten percent over takes a
    /// very long while indeed.
    ///
    /// Below the trip setting the element cools, so a line that starts, runs a
    /// while and starts again does not accumulate its way to a trip.
    /// </summary>
    private void StepThermal(float delta)
    {
        // Floored, because a trip setting of zero would divide by nothing and
        // hand the tag table an infinity — which it now rejects outright, and a
        // throw inside the tick is the worst way to find out (HP-23).
        float tripAmps = Mathf.Max(FullLoadAmps * Mathf.Max(TripPercent, 1.0f) / 100.0f, 0.01f);
        float ratio = Current / tripAmps;
        float curveTime = Mathf.Max(TripTime, 0.05f);

        // Heat at (I/Itrip)^2 - 1 per second, scaled so ratio == InrushFactor
        // fills the element in TripTime -- that is what the trip class means.
        // Cool at a third of that rate, which is roughly how a bimetal behaves
        // and, more usefully, is slow enough that repeated starting still trips.
        float scale = (InrushFactor * InrushFactor - 1.0f) * curveTime;
        float rate = (ratio * ratio - 1.0f) / scale;
        if (rate < 0.0f) rate /= 3.0f;

        ThermalState = Mathf.Clamp(ThermalState + rate * delta, 0.0f, 1.0f);

        if (ThermalState >= 1.0f && !IsTripped)
        {
            GD.Print($"{Name}: thermal overload tripped at {Current:0.0} A "
                     + $"({ratio * 100.0f:0} % of the trip setting)");
            IsTripped = true;
            IsClosed = false;
            Current = 0.0f;
            _coilTimer = 0.0f;
        }
    }

    private void Apply()
    {
        SetLamp(_closedLampMat, IsClosed, new Color(0.25f, 1.0f, 0.35f), new Color(0.06f, 0.24f, 0.10f));
        SetLamp(_trippedLampMat, IsTripped, new Color(1.0f, 0.20f, 0.15f), new Color(0.28f, 0.06f, 0.06f));

        if (_readout is null) return;
        string text = IsTripped ? "TRIPPED" : $"{Current:0.0} A";
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

    // ---------- the motor this starter powers

    /// <summary>
    /// Hold the load tag at what the contactor is doing.
    ///
    /// <c>Force</c>, not <c>Set</c>, because a force is exactly what this is: a
    /// piece of hardware holding a circuit, visible as held in the Tag
    /// Inspector, and releasable there. Writing the value instead would leave
    /// the starter and the controller as two authorities for one tag with
    /// nothing on screen to say which had last won.
    /// </summary>
    private void DriveLoad(PartTick tick)
    {
        string id = LoadTag;
        _tags = tick.Tags;

        if (id.Length == 0 || !tick.Tags.Contains(id))
        {
            ReleaseLoad();
            return;
        }

        if (_heldTagId.Length > 0 && _heldTagId != id) ReleaseLoad();
        _heldTagId = id;
        tick.Tags.Force(id, IsClosed);
    }

    private void ReleaseLoad()
    {
        if (_tags is not null && _heldTagId.Length > 0 && _tags.Contains(_heldTagId))
            _tags.ClearForce(_heldTagId);
        _heldTagId = "";
    }

    /// <summary>A starter taken out of the panel stops holding the motor it was
    /// holding. Without this, deleting a part left a tag forced by nothing, and
    /// the only way to find out was the Tag Inspector's force count.</summary>
    public override void _ExitTree() => ReleaseLoad();

    // ---------- IPart (HP-34)

    public void DeclareTags(PartTagBuilder tags) => tags
        .Bit("coil", $"Starter {tags.Index} Contactor Coil", TagKind.Output)
        // The auxiliary contact, which is what a seal-in circuit latches
        // through and what tells a program the motor is actually running
        // rather than merely commanded.
        .Bit("aux", $"Starter {tags.Index} Auxiliary Contact", TagKind.Input)
        // Normally closed, like the E-stop and the guard switch: true while the
        // element is healthy, so a broken circuit reads as "not running".
        .Bit("overload", $"Starter {tags.Index} Overload OK (NC)", TagKind.Input, initial: true)
        .Float("current", $"Starter {tags.Index} Motor Current (A)", TagKind.Input);

    public void CaptureSettings(PartSettings settings)
    {
        settings.Put("fla", FullLoadAmps);
        settings.Put("load_percent", LoadPercent);
        settings.Put("trip_percent", TripPercent);
        settings.Put("trip_time", TripTime);
        settings.Put("pull_in", PullInTime);
        // External: a wire to a tag on another machine, not a property of this
        // one, so a duplicate gets a starter wired to nothing rather than a
        // second starter fighting over one motor (HP-16).
        settings.PutExternal("load_tag", LoadTag);
    }

    public void ApplySettings(PartSettings settings)
    {
        if (settings.Number("fla") is { } fla) FullLoadAmps = fla;
        if (settings.Number("load_percent") is { } load) LoadPercent = load;
        if (settings.Number("trip_percent") is { } trip) TripPercent = trip;
        if (settings.Number("trip_time") is { } tripTime) TripTime = tripTime;
        if (settings.Number("pull_in") is { } pullIn) PullInTime = pullIn;
        if (settings.Text("load_tag") is { } tag) LoadTag = tag;
    }

    public void StepPart(PartTick tick)
    {
        Step(tick.Bit("coil"), tick.Dt);

        tick.Write("aux", IsClosed);
        tick.Write("overload", !IsTripped);
        tick.Write("current", (double)Current);

        DriveLoad(tick);
    }

    public void DescribeControls(IPartInspector ui)
    {
        ui.Slider("Full Load (A)", FullLoadAmps, 0.5f, 60.0f, 0.5f, value => FullLoadAmps = value);
        ui.Slider("Motor Load (%)", LoadPercent, 0.0f, 400.0f, 5.0f, value => LoadPercent = value);
        ui.Slider("Trip Setting (%)", TripPercent, 50.0f, 200.0f, 5.0f, value => TripPercent = value);
        ui.Slider("Trip Class (s @6x)", TripTime, 0.5f, 60.0f, 0.5f, value => TripTime = value);
        ui.TagPicker("Powers", LoadTag, "", TagType.Bit, TagKind.Output,
                     chosen => LoadTag = chosen);
    }

    /// <summary>A rename moves the tags under this starter's own prefix. A load
    /// tag pointing at another machine is pointing somewhere else and is left
    /// alone.</summary>
    public void PrefixRenamed(string oldId, string newId)
    {
        if (LoadTag.StartsWith(oldId + ".")) LoadTag = newId + LoadTag[oldId.Length..];
    }

    public void ResetPart(PartReset reset)
    {
        ReleaseLoad();
        IsClosed = false;
        Current = 0.0f;
        _coilTimer = 0.0f;
        _startTimer = 0.0f;
        ResetOverload();
        reset.Write("aux", false);
        reset.Write("overload", true);
        reset.Write("current", 0.0);
    }

    public PartOperation? Operation => new("motor starter", "overload");

    /// <summary>A click is the reset button on the front of the overload block,
    /// not a toggle of the contact. Forcing <c>overload</c> true by hand would
    /// paper over a trip the starter still believes in, and the contactor would
    /// stay out with the tag saying it was healthy.</summary>
    public void Operate(PartOperate op) => ResetOverload();
}
