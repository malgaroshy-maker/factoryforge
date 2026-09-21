using System.Collections.Generic;
using FactoryForge.Editor;
using FactoryForge.Parts;
using FactoryForge.TagBus;
using Godot;

namespace FactoryForge.Sim;

/// <summary>
/// Headless check that the five parts added in LP-01…LP-05 do what their tags
/// claim:
///
/// <code>godot --headless --path engine -- --self-test=lineparts</code>
///
/// Same rule as <see cref="NewPartsSelfTest"/>: assert the *effect*, not that a
/// tag exists. Two of these parts make claims that can only be checked against
/// real physics — "the blade stops a carton on a running belt" and "the deck
/// carries its load round" are both statements about the solver, and neither
/// can be reached by turning the dispatch by hand. Those two run on real engine
/// ticks and the rest are hand-turned afterwards, which is why this test is
/// phased rather than a single burst.
/// </summary>
public partial class LinePartsSelfTest : Node
{
    public SceneEditor Editor { get; set; } = null!;
    public TagTable Tags { get; set; } = null!;

    private readonly List<string> _failures = new();
    private int _step;
    private bool _done;

    private void Expect(bool condition, string what)
    {
        if (condition) return;
        _failures.Add(what);
        GD.PrintErr($"  FAIL  {what}");
    }

    private const double Tick = 1.0 / 60.0;

    /// <summary>Turn the dispatch by hand. Free — a two-minute thermal soak
    /// costs no wall time — but it moves the dispatch and not the world, so
    /// anything that depends on the physics server has to use real ticks
    /// instead.</summary>
    private void Run(int ticks)
    {
        for (int i = 0; i < ticks; i++) Editor._PhysicsProcess(Tick);
    }

    private double Num(string id) => System.Convert.ToDouble(Tags.Visible(id));
    private bool Bit(string id) => Tags.Contains(id) && Tags.Visible(id) is true;

    private T Part<T>(string id) where T : Node3D
    {
        var node = Editor.NodeFor(id) as T;
        if (node is null) throw new System.InvalidOperationException($"'{id}' is not a {typeof(T).Name}");
        return node;
    }

    // --- physical probes, carried across the real-tick phases
    private BoxPhysics? _heldBox;
    private BoxPhysics? _deckBox;
    private float _deckBoxStartBearing;
    private float _deckBoxStartRadius;

    /// <summary>Blade x, so "stopped short of it" and "past it" are measured
    /// against the machine rather than against a number typed twice.</summary>
    private const float BladeX = 0.4f;

    public override void _PhysicsProcess(double delta)
    {
        _step++;

        // Two ticks of grace: parts build their geometry in _Ready, which does
        // not run until the node has been in the tree for a frame.
        switch (_step)
        {
            case 2: BuildScene(); return;
            case 4: StartStopGateProbe(); return;
            case 200: CheckStopGateHeld(); return;
            case 340: CheckStopGateReleased(); StartTurnTableProbe(); return;
            case 360: RecordDeckStart(); return;
            case 520: CheckTurnTable(); return;
            case 522: SweepARemoverPreviewOverACarton(); return;
            case 560: CheckTheCartonSurvivedThePreview(); return;
            case 580: CheckThePlacedRemoverStillRemoves(); return;
            case 582: break;
            default: return;
        }

        if (_done) return;
        _done = true;

        try
        {
            CheckStopGateSeizes();
            CheckTurnTableSeizes();
            CheckEncoder();
            CheckCoolingFan();
            CheckTwoHandControl();
        }
        catch (System.Exception ex)
        {
            _failures.Add(ex.Message);
            GD.PrintErr($"  FAIL  threw: {ex.GetType().Name}: {ex.Message}");
        }

        if (_failures.Count == 0)
        {
            GD.Print("self-test lineparts: PASS");
            GetTree().Quit(0);
        }
        else
        {
            GD.PrintErr($"self-test lineparts: FAIL ({_failures.Count})");
            GetTree().Quit(1);
        }
    }

    private static PartInstanceData At(string id, string type, float x, float z,
                                       Dictionary<string, string>? props = null) =>
        new()
        {
            Id = id,
            Type = type,
            Position = new[] { x, PartLayout.WorkPlaneY, z },
            Rotation = new[] { 0.0f, 0.0f, 0.0f },
            Properties = props,
        };

    private void BuildScene()
    {
        var data = new SceneData { Name = "selftest-line-parts" };

        // A three-metre buffer belt with the blade stop a little past its
        // middle, so a carton has room to run up to the blade and room to run
        // on once it is released.
        var longDeck = new Dictionary<string, string>
        {
            ["size_x"] = "3", ["size_y"] = "0.12", ["size_z"] = "0.5", ["speed"] = "0.5",
        };
        data.Parts.Add(At("buffer", "ConveyorBelt", 0.0f, 0.0f, longDeck));
        data.Parts.Add(At("stop", "StopGate", BladeX, 0.0f));

        // Everything else stands well clear, so one part's physics cannot be
        // another part's result.
        data.Parts.Add(At("xfer", "TurnTable", 0.0f, 4.0f));

        data.Parts.Add(At("feed", "ConveyorBelt", 0.0f, -4.0f, longDeck));
        data.Parts.Add(At("enc", "RotaryEncoder", 0.0f, -4.0f));
        // Deliberately over nothing: a measuring wheel that reads a belt it is
        // not touching would be the worst possible bug in this part.
        data.Parts.Add(At("encoff", "RotaryEncoder", 0.0f, 8.0f));

        data.Parts.Add(At("oven", "HeatingStation", 0.0f, -8.0f));
        data.Parts.Add(At("fan", "CoolingFan", 0.8f, -8.0f));

        data.Parts.Add(At("hands", "TwoHandControl", 0.0f, -12.0f));

        const string path = "user://selftest_lineparts.json";
        using (var file = Godot.FileAccess.Open(path, Godot.FileAccess.ModeFlags.Write))
        {
            file?.StoreString(data.ToJson());
        }
        Editor.LoadSceneFromFile(path);
    }

    // ---------- HP-41: a preview is a picture, not a machine

    private BoxPhysics? _previewVictim;

    /// <summary>
    /// Arm a Remover and hold its ghost over a carton, which is what somebody
    /// does for several seconds while deciding where to put it.
    ///
    /// The preview is an ordinary part -- that is what makes it an honest
    /// preview -- and it was added to the scene as one, so a Remover ghost
    /// deleted every carton it passed over. Remover connects BodyEntered in
    /// _Ready and calls QueueFree on whatever arrives, and cancelling the
    /// placement cannot bring them back: the cartons are simply gone, from a
    /// gesture that placed nothing.
    ///
    /// Run on real physics ticks, not the hand-turned dispatch. The deletion
    /// happens through an Area3D signal from the physics server, so a
    /// hand-turned test could not see the failure at all -- and a test that
    /// cannot see the failure mode is not coverage.
    /// </summary>
    private void SweepARemoverPreviewOverACarton()
    {
        Editor.SetMode(EditorMode.Edit);

        _previewVictim = new BoxPhysics { IsTall = false };
        Editor.GetParent().AddChild(_previewVictim);
        // Well clear of every other probe in this scene, on the work plane so it
        // sits in the middle of a remover's 0.4 m zone -- and frozen, so it
        // stays there.
        //
        // Freezing is not a convenience. The first version let it fall, and it
        // dropped clean through the zone in the twenty ticks between arming the
        // ghost and looking: "the carton survived" would then have been true
        // because nothing ever touched it, which is a test that passes while the
        // simulation does nothing. The position assertion below keeps that
        // honest.
        _previewVictim.GlobalPosition = new Vector3(0.0f, PartLayout.WorkPlaneY, 12.0f);
        _previewVictim.Freeze = true;

        Editor.SetPlacementPart("Remover");
        Expect(Editor.HasPlacementPreview, "the remover tool is armed");
        Editor.MovePreviewTo(new Vector3(0.0f, PartLayout.WorkPlaneY, 12.0f));
    }

    private void CheckTheCartonSurvivedThePreview()
    {
        var box = _previewVictim!;
        bool alive = GodotObject.IsInstanceValid(box) && !box.IsQueuedForDeletion();
        Expect(alive,
               "a carton under a remover *preview* is still there — the ghost is a "
               + "picture of a machine, not a machine");

        // And it is still *in* the zone, so "it survived" cannot be satisfied by
        // a carton that fell out of reach before anything could touch it.
        Expect(alive && Mathf.Abs(box.GlobalPosition.Y - PartLayout.WorkPlaneY) < 0.1f
                     && Mathf.Abs(box.GlobalPosition.Z - 12.0f) < 0.1f,
               $"and it stayed inside the ghost's zone while it was there "
               + $"(at {(alive ? box.GlobalPosition.ToString() : "gone")})");

        // Now put it down for real. Without this the fix could be "previews do
        // nothing" rather than "previews do not run", and nothing here would
        // notice the difference.
        Editor.PlacePreviewAt(new Vector3(0.0f, PartLayout.WorkPlaneY, 12.0f));
        Editor.CancelPlacement();
    }

    private void CheckThePlacedRemoverStillRemoves()
    {
        var box = _previewVictim!;
        Expect(!GodotObject.IsInstanceValid(box) || box.IsQueuedForDeletion(),
               "and the same remover, once actually placed, does take the carton — "
               + "the preview was inert, not the part");
    }

    // ---------- LP-01 blade stop

    private void StartStopGateProbe()
    {
        Expect(Bit("stop.down"), "a placed blade stop starts parked below the deck");
        Expect(!Bit("stop.up"), "a parked blade stop is not also up");

        Tags.Set("stop.raise", true);
        Tags.Set("buffer.rotate", true);

        // A *short* carton on purpose. It is the demanding case — the blade has
        // to stand proud of the belt by more than the carton is tall, or it
        // catches the top edge and tips it over instead of holding it.
        _heldBox = new BoxPhysics { IsTall = false };
        Editor.GetParent().AddChild(_heldBox);
        _heldBox.GlobalPosition = new Vector3(-0.8f, PartLayout.WorkPlaneY + 0.14f, 0.0f);
    }

    private void CheckStopGateHeld()
    {
        Expect(Bit("stop.up"), "the blade reaches its raised limit");
        Expect(!Bit("stop.down"), "and leaves the lower one");

        var box = _heldBox!;
        // Contact face: the blade's own half-thickness plus the carton's.
        float contact = BladeX - 0.0175f - box.Length / 2.0f;

        Expect(box.GlobalPosition.X > -0.2f,
               $"the carton travelled on the running belt (x={box.GlobalPosition.X:0.00})");
        Expect(box.GlobalPosition.X < contact + 0.06f,
               $"the raised blade holds the carton short of it (x={box.GlobalPosition.X:0.00}, "
               + $"blade face at {contact:0.00})");
        // The belt underneath is still driving. This is the claim that
        // separates a stop gate from pausing the line, and it is the reason the
        // cartons behind it close up rather than staying where they were told.
        Expect(Bit("buffer.rotate"), "the belt is still running while the carton is held");
        Expect(Mathf.Abs(box.LinearVelocity.X) < 0.08f,
               $"and the held carton is stationary against the blade (vx={box.LinearVelocity.X:0.000})");

        Tags.Set("stop.raise", false);
    }

    private void CheckStopGateReleased()
    {
        Expect(Bit("stop.down"), "the blade returns below the deck");

        var box = _heldBox!;
        Expect(box.GlobalPosition.X > BladeX + 0.15f,
               $"and the released carton runs on past it (x={box.GlobalPosition.X:0.00})");

        box.QueueFree();
        _heldBox = null;
        Tags.Set("buffer.rotate", false);
    }

    private void CheckStopGateSeizes()
    {
        var stop = Part<StopGate>("stop");

        Tags.Set("stop.raise", true);
        Run(6);
        Tags.Force("stop.fault", true);
        float frozen = stop.Lift;
        Run(180);
        Expect(Mathf.IsEqualApprox(stop.Lift, frozen),
               $"a seized blade stop freezes mid-stroke (was {frozen:0.000} m, now {stop.Lift:0.000} m)");
        // Mid-stroke is neither limit, which is the diagnosis: a stop that
        // reports neither up nor down for good is not a slow stop.
        Expect(!Bit("stop.up") && !Bit("stop.down"),
               "and reports neither limit while it is stuck between them");

        Tags.ClearForce("stop.fault");
        Run(180);
        Expect(Bit("stop.up"), "clearing the fault lets the blade finish rising");
        Tags.Set("stop.raise", false);
        Run(180);
    }

    // ---------- LP-02 turntable

    private void StartTurnTableProbe()
    {
        Expect(Bit("xfer.athome"), "a placed turntable starts square with the infeed");
        Expect(!Bit("xfer.atindex"), "a turntable at home is not also at its index");

        var table = Part<TurnTable>("xfer");
        _deckBox = new BoxPhysics { IsTall = false };
        Editor.GetParent().AddChild(_deckBox);
        // Off centre, because a carton on the axis turns on the spot and would
        // prove nothing about being carried.
        _deckBox.GlobalPosition = table.GlobalPosition + new Vector3(0.18f, 0.16f, 0.0f);
    }

    /// <summary>Where the carton sat once it had settled on the deck, so the
    /// index is measured from a carton at rest rather than from one still
    /// dropping. Recorded after the settle and before the deck moves.</summary>
    private void RecordDeckStart()
    {
        var table = Part<TurnTable>("xfer");
        Vector3 offset = _deckBox!.GlobalPosition - table.GlobalPosition;
        _deckBoxStartBearing = Mathf.Atan2(offset.Z, offset.X);
        _deckBoxStartRadius = new Vector2(offset.X, offset.Z).Length();

        Expect(_deckBoxStartRadius > 0.05f,
               $"the probe carton settled off the deck axis (r={_deckBoxStartRadius:0.000} m)");

        Tags.Set("xfer.index", true);
    }

    private void CheckTurnTable()
    {
        var table = Part<TurnTable>("xfer");
        Expect(Bit("xfer.atindex"), $"the deck reaches its index angle (at {table.Angle:0.0}°)");
        Expect(!Bit("xfer.athome"), "and leaves home");

        Vector3 offset = _deckBox!.GlobalPosition - table.GlobalPosition;
        float bearing = Mathf.Atan2(offset.Z, offset.X);
        float radius = new Vector2(offset.X, offset.Z).Length();
        float swept = Mathf.Abs(Mathf.RadToDeg(Mathf.AngleDifference(_deckBoxStartBearing, bearing)));

        // Friction, not parenting. Some slip is honest and expected, so this
        // asks for most of the index rather than all of it — but it asks for
        // enough that a carton merely sitting still on a spinning plate fails.
        Expect(swept > table.IndexAngle * 0.6f,
               $"the deck carried the carton round with it (swept {swept:0.0}° of {table.IndexAngle:0.0}°)");
        Expect(Mathf.Abs(radius - _deckBoxStartRadius) < 0.10f,
               $"and the carton stayed on the deck rather than being thrown "
               + $"(r {_deckBoxStartRadius:0.000} m → {radius:0.000} m)");

        _deckBox.QueueFree();
        _deckBox = null;
    }

    private void CheckTurnTableSeizes()
    {
        var table = Part<TurnTable>("xfer");

        Tags.Set("xfer.index", false);
        Run(20);
        Tags.Force("xfer.fault", true);
        float frozen = table.Angle;
        Run(300);
        Expect(Mathf.IsEqualApprox(table.Angle, frozen),
               $"a seized deck stops where it is (was {frozen:0.0}°, now {table.Angle:0.0}°)");
        Expect(!Bit("xfer.athome") && !Bit("xfer.atindex"),
               "and blocks both the infeed and the outfeed by reporting neither limit");

        Tags.ClearForce("xfer.fault");
        Run(300);
        Expect(Bit("xfer.athome"), "clearing the fault lets the deck square back up");
    }

    // ---------- LP-03 measuring encoder

    private void CheckEncoder()
    {
        var encoder = Part<RotaryEncoder>("enc");
        var stray = Part<RotaryEncoder>("encoff");

        Expect(encoder.IsTracking, "an encoder placed over a belt finds it");
        Expect(!stray.IsTracking, "an encoder placed over nothing does not invent a belt to read");

        Tags.Set("enc.reset", true);
        Run(4);
        Expect((int)Num("enc.count") == 0, "the reset leg zeroes the count");
        Tags.Set("enc.reset", false);

        Expect(Mathf.IsZeroApprox((float)Num("enc.rate")),
               "a stopped belt gives a stopped wheel");

        // One second of a 0.5 m/s belt at 100 pulses/m is 50 pulses. Asserting
        // the *rate* and not merely "the count went up" is what catches a wrong
        // radius or a dropped remainder: at 60 Hz a tick is 0.83 of a pulse,
        // and an integer accumulator would round every one of them away and
        // count nothing at all.
        Tags.Set("feed.rotate", true);
        Run(1);
        double rate = Num("enc.rate");
        Expect(Mathf.Abs(rate - 50.0) < 1.0,
               $"the rate is belt speed in pulses/s (got {rate:0.0}, expected 50)");

        int before = (int)Num("enc.count");
        Run(60);
        int after = (int)Num("enc.count");
        Expect(Mathf.Abs(after - before - 50) <= 2,
               $"one second of belt advances the count by 50 pulses (got {after - before})");
        Expect(encoder.Count == after, "and the part and its tag agree");

        Expect((int)Num("encoff.count") == 0,
               "the encoder over nothing counted nothing while the belt ran");

        // Held, not edged: a counter's reset leg holds the count down for as
        // long as it is high, and a program that pulses it and walks away gets
        // a counter that starts again immediately — which is the behaviour.
        Tags.Set("enc.reset", true);
        Run(60);
        Expect((int)Num("enc.count") == 0, "holding reset high holds the count at zero");
        Tags.Set("enc.reset", false);
        Run(30);
        Expect((int)Num("enc.count") > 0, "releasing it lets the count run again");

        Tags.Set("feed.rotate", false);
        Run(4);
        Expect(Mathf.IsZeroApprox((float)Num("enc.rate")), "stopping the belt stops the rate");
    }

    // ---------- LP-04 cooling fan

    private void CheckCoolingFan()
    {
        var oven = Part<HeatingStation>("oven");

        // Half power with no cooling settles where the element and the room
        // balance: 20 + (90 x 0.5) / 0.30 = 170 degC.
        Tags.Set("oven.heater", 50.0);
        Run(9000);
        float uncooled = oven.Temperature;
        Expect(Mathf.Abs(uncooled - 170.0f) < 12.0f,
               $"the uncooled plant settles where the element and the room balance "
               + $"({uncooled:0.0} degC, expected ~170)");

        // Same command, fan on. The fan adds to the *loss* term, so the balance
        // moves rather than the input changing: 20 + 45 / (0.30 + 0.60) = 70.
        // A split-range controller lives entirely in the gap between these two
        // numbers, and without the fan there is no gap.
        Tags.Set("fan.run", true);
        Tags.Set("fan.speed", 100.0);
        Run(3000);
        float cooled = oven.Temperature;
        Expect(cooled < uncooled - 60.0f,
               $"forced cooling pulls the same heater command to a far lower balance "
               + $"({uncooled:0.0} degC → {cooled:0.0} degC)");
        Expect(Mathf.Abs((float)Num("fan.airflow") - 100.0f) < 1.0f,
               $"and the fan reports the airflow it is delivering (got {Num("fan.airflow"):0.0} %)");

        // The ramp: a fan has inertia, so the reference and the reading
        // disagree while it is changing.
        Tags.Set("fan.speed", 0.0);
        Run(1);
        double mid = Num("fan.airflow");
        Expect(mid > 0.0 && mid < 100.0,
               $"the airflow lags a step change in the reference (got {mid:0.0} %)");

        // A failed motor is only visible in the measurement. The command still
        // reads 100 %, and the temperature climbs back to where it was.
        Tags.Set("fan.speed", 100.0);
        Run(600);
        Tags.Force("fan.fault", true);
        Run(6000);
        Expect(Num("fan.airflow") < 1.0,
               "a faulted fan delivers no air while its reference still reads 100 %");
        Expect(Mathf.Abs((float)Num("fan.speed") - 100.0f) < 0.01f,
               "and the reference is still commanded, so the output cannot tell you");
        Expect(oven.Temperature > uncooled - 20.0f,
               $"the plant climbs back to its uncooled balance, which is the only honest "
               + $"reading ({oven.Temperature:0.0} degC)");

        Tags.ClearForce("fan.fault");
        Tags.Set("fan.run", false);
        Tags.Set("oven.heater", 0.0);
    }

    // ---------- LP-05 two-hand control

    private void CheckTwoHandControl()
    {
        var hands = Part<TwoHandControl>("hands");

        Expect(!Bit("hands.valid"), "an untouched two-hand station gives no permissive");

        // Both hands, together.
        hands.Press("left");
        Run(2);
        hands.Press("right");
        Run(2);
        Expect(Bit("hands.left") && Bit("hands.right"), "both buttons read as held");
        Expect(Bit("hands.valid"), "two hands arriving together give the permissive");

        hands.ResetStation();
        Run(2);
        Expect(!Bit("hands.valid"), "taking both hands off drops it");

        // The tie-down. One button held, the other pressed a second later: both
        // bits are true and the permissive is refused. This is the whole reason
        // the relay computes `valid` instead of the program ANDing two bits —
        // a program that ANDed them would be satisfied here.
        hands.Press("left");
        Run(60);
        hands.Press("right");
        Run(2);
        Expect(Bit("hands.left") && Bit("hands.right"),
               "with one button taped down, both bits still read true");
        Expect(!Bit("hands.valid"),
               "and the permissive is refused, because the presses were not simultaneous");

        // And it stays refused: waiting does not turn a tie-down into a
        // two-hand press.
        Run(60);
        Expect(!Bit("hands.valid"), "waiting does not make a tie-down valid");

        hands.ResetStation();
        Run(2);

        // The hold expires on its own, so a station nobody is touching cannot
        // leave a machine enabled.
        hands.Press("left");
        hands.Press("right");
        Run(2);
        Expect(Bit("hands.valid"), "and a fresh simultaneous press is valid again");
        Run((int)(hands.HoldTime * 60.0f) + 30);
        Expect(!Bit("hands.valid") && !Bit("hands.left") && !Bit("hands.right"),
               "letting go drops both buttons and the permissive with them");
    }
}
