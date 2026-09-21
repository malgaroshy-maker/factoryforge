using FactoryForge.TagBus;
using Godot;

namespace FactoryForge.Parts;

/// <summary>
/// A dual-channel safety relay: the device that makes an E-stop mean something.
///
/// Until this existed the library's whole safety story was one latching mushroom
/// and one guard switch, both of which a program could simply ignore — and a
/// student who has only ever read a guard contact in ladder has never met the
/// three things that make guarding real.
///
/// <b>Two channels, cross-monitored.</b> A safety device brings two independent
/// contacts, and the relay watches them agree. One channel opening while the
/// other stays closed is a welded contact or a cut wire, and it latches a fault
/// that a reset alone will not clear: the relay refuses to trust a circuit it
/// has caught lying. A single-channel reading of the same guard cannot detect
/// that at all, which is exactly why the second channel exists.
///
/// <b>The reset is an edge.</b> Holding the reset input high must not restart
/// the machine when the guard closes — that is automatic restart, the thing
/// nearly every guarding standard forbids, and a relay that latched on a level
/// would teach a student to build it. The outputs energise only on a rising edge
/// of reset, taken while the channels are already healthy.
///
/// <b>The relay permits; it does not command.</b> Its <c>load_tag</c> setting
/// names an output elsewhere — a starter's coil is the natural one — and while
/// the safety outputs are open that tag is held false and cannot be started by
/// anybody. When the outputs close the relay <em>releases</em> the tag and the
/// controller has it back. A safety contact in series with a coil can stop a
/// machine and can never start one; <see cref="MotorStarter"/> is the deliberate
/// contrast, and it holds its load both ways because commanding is its job.
/// </summary>
public partial class SafetyRelay : Node3D, IPart
{
    /// <summary>The first safety contact this relay watches, as a tag id —
    /// <c>guard_1.closed</c>, <c>panel.estop</c>. Empty means the channel is
    /// linked out, which is what a commissioning engineer does and what this
    /// part lets a student see the consequence of.</summary>
    [Export] public string ChannelATag { get; set; } = "";

    /// <summary>The second, independent contact. Pointing both channels at one
    /// tag is not dual-channel monitoring and the relay will never see a
    /// discrepancy, which is worth being able to demonstrate.</summary>
    [Export] public string ChannelBTag { get; set; } = "";

    /// <summary>The output held false while the safety outputs are open. Empty
    /// means the relay only reports and interlocks nothing.</summary>
    [Export] public string LoadTag { get; set; } = "";

    /// <summary>How long the two channels may disagree before the relay calls
    /// it a fault, seconds. Real relays allow a few hundred milliseconds
    /// because two contacts on one actuator never open on the same
    /// instant.</summary>
    [Export] public float SyncWindow { get; set; } = 0.3f;

    /// <summary>Are the safety outputs closed?</summary>
    public bool IsEnergised { get; private set; }

    /// <summary>Has a channel discrepancy been latched?</summary>
    public bool IsFaulted { get; private set; }

    /// <summary>What the relay currently sees on each channel, for a readout
    /// and for a test that should not have to guess.</summary>
    public bool ChannelA { get; private set; }
    public bool ChannelB { get; private set; }

    private float _discrepancy;
    private bool _lastReset;

    private StandardMaterial3D _k1Mat = null!;
    private StandardMaterial3D _k2Mat = null!;
    private StandardMaterial3D _faultMat = null!;
    private Label3D _readout = null!;

    private TagTable? _tags;
    private string _heldTagId = "";

    private const float BodyWidth = 0.13f;
    private const float BodyHeight = 0.30f;
    private const float BodyDepth = 0.13f;
    private const float BodyY = 0.32f;

    public override void _Ready()
    {
        // A DIN-rail module: narrow, tall, grey, with a row of indicator LEDs
        // down the front. It is meant to be recognisable as the thing in the
        // control panel rather than as another machine on the line.
        var caseMat = new StandardMaterial3D
        {
            AlbedoColor = new Color(0.72f, 0.72f, 0.70f),
            Metallic = 0.20f,
            Roughness = 0.70f,
        };
        var trimMat = new StandardMaterial3D
        {
            AlbedoColor = new Color(0.18f, 0.19f, 0.21f),
            Metallic = 0.40f,
            Roughness = 0.50f,
        };

        AddChild(new MeshInstance3D
        {
            Name = "RelayBody",
            Mesh = new BoxMesh { Size = new Vector3(BodyWidth, BodyHeight, BodyDepth) },
            MaterialOverride = caseMat,
            Position = new Vector3(0, BodyY, 0),
        });

        AddChild(new MeshInstance3D
        {
            Name = "Stand",
            Mesh = new CylinderMesh
            {
                TopRadius = 0.020f, BottomRadius = 0.026f,
                Height = BodyY - BodyHeight / 2.0f + PartLayout.FloorDrop,
            },
            MaterialOverride = trimMat,
            Position = new Vector3(0, (BodyY - BodyHeight / 2.0f - PartLayout.FloorDrop) / 2.0f, 0),
        });

        _k1Mat = Lamp(new Color(0.06f, 0.24f, 0.10f));
        _k2Mat = Lamp(new Color(0.06f, 0.24f, 0.10f));
        _faultMat = Lamp(new Color(0.28f, 0.06f, 0.06f));

        AddLed("K1Lamp", _k1Mat, 0.07f);
        AddLed("K2Lamp", _k2Mat, 0.02f);
        AddLed("FaultLamp", _faultMat, -0.03f);

        _readout = new Label3D
        {
            Name = "RelayReadout",
            Text = "OPEN",
            Position = new Vector3(0, BodyY + BodyHeight / 2.0f + 0.07f, 0),
            Billboard = BaseMaterial3D.BillboardModeEnum.Enabled,
            FontSize = 78,
            PixelSize = 0.0015f,
            Modulate = new Color(1.0f, 0.85f, 0.35f),
        };
        AddChild(_readout);

        Apply();
    }

    private void AddLed(string name, StandardMaterial3D mat, float y) =>
        AddChild(new MeshInstance3D
        {
            Name = name,
            Mesh = new SphereMesh { Radius = 0.016f, Height = 0.032f },
            MaterialOverride = mat,
            Position = new Vector3(0, BodyY + y, BodyDepth / 2.0f + 0.008f),
        });

    private static StandardMaterial3D Lamp(Color dark) => new()
    {
        AlbedoColor = dark,
        Metallic = 0.10f,
        Roughness = 0.35f,
    };

    /// <summary>
    /// One tick.
    /// </summary>
    /// <param name="a">Channel A, true when the contact is closed and
    /// healthy.</param>
    /// <param name="b">Channel B, same sense.</param>
    /// <param name="reset">The reset input, read as an edge.</param>
    public void Step(bool a, bool b, bool reset, float delta)
    {
        ChannelA = a;
        ChannelB = b;

        // Cross-monitoring. The two contacts are allowed to disagree for as
        // long as it takes one actuator to move both; longer than that and one
        // of them is not following, which is a welded contact or a broken wire
        // and is exactly what the second channel is for.
        if (a != b)
        {
            _discrepancy += delta;
            if (_discrepancy >= Mathf.Max(SyncWindow, 0.0f)) IsFaulted = true;
        }
        else
        {
            _discrepancy = 0.0f;
        }

        bool healthy = a && b && !IsFaulted;

        // Dropping out is immediate and unconditional: a safety output that
        // waited for anything at all would not be a safety output.
        if (!healthy) IsEnergised = false;

        // Rising edge only. A level would be automatic restart -- close the
        // guard and the machine goes, which is the failure guarding exists to
        // prevent and the one a student will build by accident.
        bool risingReset = reset && !_lastReset;
        _lastReset = reset;

        if (risingReset)
        {
            // A reset clears a latched discrepancy only once both channels have
            // actually returned. Clearing it while they still disagree would
            // let somebody reset their way past a broken wire.
            if (IsFaulted && a == b) { IsFaulted = false; _discrepancy = 0.0f; }
            if (a && b && !IsFaulted) IsEnergised = true;
        }

        Apply();
    }

    private void Apply()
    {
        SetLamp(_k1Mat, IsEnergised, new Color(0.25f, 1.0f, 0.35f), new Color(0.06f, 0.24f, 0.10f));
        SetLamp(_k2Mat, IsEnergised, new Color(0.25f, 1.0f, 0.35f), new Color(0.06f, 0.24f, 0.10f));
        SetLamp(_faultMat, IsFaulted, new Color(1.0f, 0.20f, 0.15f), new Color(0.28f, 0.06f, 0.06f));

        if (_readout is null) return;
        string text = IsFaulted ? "CH FAULT" : IsEnergised ? "SAFE" : "OPEN";
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

    /// <summary>Read a watched contact. An unwired channel reads healthy, which
    /// is what linking a channel out does in the panel, and the relay then
    /// cannot detect a discrepancy on it -- visibly, because both lamps still
    /// light and nothing is being monitored.</summary>
    private static bool ReadChannel(PartTick tick, string tagId)
    {
        if (tagId.Length == 0) return true;
        return tick.Tags.TryGetVisible(tagId, out var raw) && System.Convert.ToBoolean(raw);
    }

    private void HoldLoad(PartTick tick)
    {
        string id = LoadTag;
        _tags = tick.Tags;

        if (id.Length == 0 || !tick.Tags.Contains(id)) { ReleaseLoad(); return; }
        if (_heldTagId.Length > 0 && _heldTagId != id) ReleaseLoad();

        if (IsEnergised)
        {
            // Permit, do not command: the controller gets its tag back.
            ReleaseLoad();
            return;
        }

        _heldTagId = id;
        tick.Tags.Force(id, false);
    }

    private void ReleaseLoad()
    {
        if (_tags is not null && _heldTagId.Length > 0 && _tags.Contains(_heldTagId))
            _tags.ClearForce(_heldTagId);
        _heldTagId = "";
    }

    public override void _ExitTree() => ReleaseLoad();

    // ---------- IPart (HP-34)

    public void DeclareTags(PartTagBuilder tags) => tags
        // The reset is the one thing the controller writes. Everything else the
        // relay decides for itself, which is the point of a safety relay: the
        // program reads the safety device's verdict, it does not compute it.
        .Bit("reset", $"Safety Relay {tags.Index} Reset", TagKind.Output)
        .Bit("k1", $"Safety Relay {tags.Index} Safety Contact K1", TagKind.Input)
        .Bit("k2", $"Safety Relay {tags.Index} Safety Contact K2", TagKind.Input)
        .Bit("cha", $"Safety Relay {tags.Index} Channel A", TagKind.Input, initial: true)
        .Bit("chb", $"Safety Relay {tags.Index} Channel B", TagKind.Input, initial: true)
        .Bit("fault", $"Safety Relay {tags.Index} Channel Fault", TagKind.Input);

    public void CaptureSettings(PartSettings settings)
    {
        settings.Put("sync_window", SyncWindow);
        settings.PutExternal("channel_a_tag", ChannelATag);
        settings.PutExternal("channel_b_tag", ChannelBTag);
        settings.PutExternal("load_tag", LoadTag);
    }

    public void ApplySettings(PartSettings settings)
    {
        if (settings.Number("sync_window") is { } window) SyncWindow = window;
        if (settings.Text("channel_a_tag") is { } a) ChannelATag = a;
        if (settings.Text("channel_b_tag") is { } b) ChannelBTag = b;
        if (settings.Text("load_tag") is { } load) LoadTag = load;
    }

    public void StepPart(PartTick tick)
    {
        Step(ReadChannel(tick, ChannelATag), ReadChannel(tick, ChannelBTag),
             tick.Bit("reset"), tick.Dt);

        tick.Write("cha", ChannelA);
        tick.Write("chb", ChannelB);
        tick.Write("k1", IsEnergised);
        tick.Write("k2", IsEnergised);
        tick.Write("fault", IsFaulted);

        HoldLoad(tick);
    }

    public void DescribeControls(IPartInspector ui)
    {
        ui.Slider("Sync Window (s)", SyncWindow, 0.05f, 2.0f, 0.05f, value => SyncWindow = value);
        ui.TagPicker("Channel A", ChannelATag, "", TagType.Bit, TagKind.Input,
                     chosen => ChannelATag = chosen);
        ui.TagPicker("Channel B", ChannelBTag, "", TagType.Bit, TagKind.Input,
                     chosen => ChannelBTag = chosen);
        ui.TagPicker("Interlocks", LoadTag, "", TagType.Bit, TagKind.Output,
                     chosen => LoadTag = chosen);
    }

    public void ResetPart(PartReset reset)
    {
        ReleaseLoad();
        IsEnergised = false;
        IsFaulted = false;
        _discrepancy = 0.0f;
        _lastReset = false;
        Apply();
        reset.Write("k1", false);
        reset.Write("k2", false);
        reset.Write("fault", false);
    }

    public PartOperation? Operation => new("safety relay", "reset");

    /// <summary>The reset button on the front of the module. A pulse, not a
    /// latch: the relay takes the edge, and a click that left the input high
    /// would be the automatic restart this part exists to refuse.</summary>
    public void Operate(PartOperate op) => op.PulseBit("reset");
}
